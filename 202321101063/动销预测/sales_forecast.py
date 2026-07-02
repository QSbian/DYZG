#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动销预测系统 (Sales Forecast System) v2
========================================
基于 OMS 销售数据库的动销预测工具

v2 改动：
- 预测维度移至左侧导航栏，点击切换
- 渠道筛选改为三按钮（线上 / 线下 / 线上和线下）
- 型号支持输入 + 下拉选择 + 历史记录
- 预计月份支持单月或月份区间
- 加载动画显示预计耗时

用法：python sales_forecast.py [数据库路径]
默认数据库路径：../任务五/oms_sales_data.sqlite
"""

import os
import sys
import re
import sqlite3
import csv
import json
import warnings
from datetime import datetime
from collections import OrderedDict
from typing import List, Tuple, Dict, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

# Windows 终端 UTF-8 修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

warnings.filterwarnings('ignore', category=FutureWarning)

# 历史记录文件路径（与程序同目录）
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.model_history.json')


# ============================================================
#  数据加载模块
# ============================================================

class DataLoader:
    """从 SQLite 加载动销数据，不修改原始数据库。

    线程安全：每次查询都创建独立的连接，避免跨线程共享连接报错。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _new_conn(self):
        """每次调用返回一个全新的连接（线程安全）"""
        return sqlite3.connect(self.db_path)

    def connect(self):
        return self._new_conn()

    def close(self):
        pass

    def load_raw_data(self) -> pd.DataFrame:
        """读取 vw_product_sales_customer_3 视图的动销数据"""
        conn = self._new_conn()
        try:
            query = """
                SELECT 账期 AS period,
                       渠道 AS channel,
                       品类 AS category,
                       细分类 AS subcategory,
                       型号 AS model,
                       动销 AS sales_qty
                FROM vw_product_sales_customer_3
                WHERE 动销 IS NOT NULL AND 动销 > 0
                ORDER BY 账期
            """
            df = pd.read_sql_query(query, conn)
            df['sales_qty'] = pd.to_numeric(df['sales_qty'], errors='coerce').fillna(0)
            return df
        finally:
            conn.close()

    def get_dimensions(self) -> Dict[str, list]:
        """获取各筛选维度的唯一值"""
        conn = self._new_conn()
        try:
            cur = conn.cursor()

            cur.execute("SELECT DISTINCT 渠道 FROM vw_product_sales_customer_3 WHERE 渠道 IS NOT NULL ORDER BY 渠道")
            channels = [r[0] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT 品类 FROM vw_product_sales_customer_3 WHERE 品类 IS NOT NULL ORDER BY 品类")
            categories = [r[0] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT 细分类 FROM vw_product_sales_customer_3 WHERE 细分类 IS NOT NULL ORDER BY 细分类")
            subcategories = [r[0] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT 型号 FROM vw_product_sales_customer_3 WHERE 型号 IS NOT NULL ORDER BY 型号")
            models = [r[0] for r in cur.fetchall()]

            cur.execute("SELECT DISTINCT 账期 FROM vw_product_sales_customer_3 WHERE 账期 IS NOT NULL ORDER BY 账期")
            periods = [r[0] for r in cur.fetchall()]

            return {
                'channels': channels,
                'categories': categories,
                'subcategories': subcategories,
                'models': models,
                'periods': periods,
            }
        finally:
            conn.close()


# ============================================================
#  预测算法模块
# ============================================================

# ---------- 基类定义 ----------
class BasePredictor:
    """预测器基类"""
    name = "Base"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        raise NotImplementedError


# ---------- 朴素预测算法 ----------
class NaivePredictor(BasePredictor):
    """朴素预测：用最后一个观测值作为所有未来期的预测值"""
    name = "Naive"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        if len(series) == 0 or np.all(series == 0):
            return np.zeros(forecast_horizon), 0.0
        last_val = series[-1]
        forecast = np.full(forecast_horizon, last_val)
        accuracy = self._calc_accuracy(series, forecast_horizon)
        return forecast, accuracy

    @staticmethod
    def _calc_accuracy(series: np.ndarray, horizon: int) -> float:
        test_len = min(horizon, len(series) // 3)
        if test_len < 2:
            return 100.0
        actual = series[-test_len:]
        naive = np.full(test_len, series[-test_len - 1])
        mape = np.mean(np.abs((actual - naive + 1e-9) / (actual + 1e-9)))
        return max(0.0, min(100.0, 100.0 * (1 - mape)))


# ---------- 简单移动平均（SMA）算法 ----------
class SMAPredictor(BasePredictor):
    """简单移动平均（SMA）"""
    name = "SMA"
    window = 3

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        if len(series) == 0:
            return np.zeros(forecast_horizon), 0.0
        w = min(self.window, len(series))
        last_avg = np.mean(series[-w:])
        forecast = np.full(forecast_horizon, last_avg)
        accuracy = self._calc_accuracy(series, w, forecast_horizon)
        return forecast, accuracy

    @staticmethod
    def _calc_accuracy(series: np.ndarray, window: int, horizon: int) -> float:
        test_len = min(horizon, len(series) // 3)
        if test_len < 2:
            return 100.0
        actual = series[-test_len:]
        pred = np.full(test_len, np.mean(actual))
        mape = np.mean(np.abs((actual - pred + 1e-9) / (actual + 1e-9)))
        return max(0.0, min(100.0, 100.0 * (1 - mape)))


# ---------- 中位数预测算法 ----------
class MedianPredictor(BasePredictor):
    """中位数预测"""
    name = "Median"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        if len(series) == 0:
            return np.zeros(forecast_horizon), 0.0
        med = np.median(series)
        forecast = np.full(forecast_horizon, med)
        accuracy = self._calc_accuracy(series, forecast_horizon)
        return forecast, accuracy

    @staticmethod
    def _calc_accuracy(series: np.ndarray, horizon: int) -> float:
        test_len = min(horizon, len(series) // 3)
        if test_len < 2:
            return 100.0
        actual = series[-test_len:]
        med = np.median(series[:-test_len])
        mape = np.mean(np.abs((actual - med + 1e-9) / (actual + 1e-9)))
        return max(0.0, min(100.0, 100.0 * (1 - mape)))


# ---------- Holt-Winters 指数平滑算法 ----------
class HWPredictor(BasePredictor):
    """Holt-Winters 指数平滑"""
    name = "HW"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        try:
            from statsmodels.tsa.holtwinters import ExponentialSmoothing
        except ImportError:
            return SMAPredictor().fit_predict(series, forecast_horizon)

        if len(series) < 4:
            return SMAPredictor().fit_predict(series, forecast_horizon)

        s = series.astype(float)
        try:
            model = ExponentialSmoothing(s, trend='add', seasonal=None,
                                          initialization_method='estimated').fit()
            forecast = model.forecast(forecast_horizon).values
            fitted = model.fittedvalues.values
            if len(fitted) >= 4:
                actual_tail = s[-min(len(fitted), 12):]
                fit_tail = fitted[-len(actual_tail):]
                mape = np.mean(np.abs((actual_tail - fit_tail + 1e-9) / (actual_tail + 1e-9)))
                accuracy = max(0.0, min(100.0, 100.0 * (1 - mape)))
            else:
                accuracy = 85.0
            return forecast, accuracy
        except Exception:
            return SMAPredictor().fit_predict(series, forecast_horizon)


# ---------- 随机森林（Random Forest）算法 ----------
class RFPredictor(BasePredictor):
    """随机森林回归预测"""
    name = "RF"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        from sklearn.ensemble import RandomForestRegressor

        n = len(series)
        if n < 10:
            return SMAPredictor().fit_predict(series, forecast_horizon)

        s = series.astype(float).flatten()
        window_size = min(6, n // 3)
        X, y = [], []
        for i in range(window_size, n):
            X.append(s[i - window_size:i])
            y.append(s[i])
        X, y = np.array(X), np.array(y)

        split = int(len(X) * 0.75)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        rf = RandomForestRegressor(n_estimators=50, max_depth=5, random_state=42, n_jobs=-1)
        rf.fit(X_train, y_train)

        if len(y_test) > 0:
            y_pred = rf.predict(X_test)
            mape = np.mean(np.abs((y_test - y_pred + 1e-9) / (y_test + 1e-9)))
            accuracy = max(0.0, min(100.0, 100.0 * (1 - mape)))
        else:
            accuracy = 80.0

        future_X = []
        recent = list(s[-window_size:])
        for _ in range(forecast_horizon):
            future_X.append(recent.copy())
            pred_val = rf.predict(np.array([recent]))[0]
            recent.append(pred_val)
            recent.pop(0)

        forecast = rf.predict(np.array(future_X))
        return np.maximum(0, forecast), accuracy


# ---------- Croston 稀疏序列预测算法 ----------
class CrostonPredictor(BasePredictor):
    """Croston 方法——适合间歇性需求序列"""
    name = "Croston"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        s = series.astype(float).flatten()
        n = len(s)
        if n < 4:
            return SMAPredictor().fit_predict(series, forecast_horizon)

        nonzero_idx = np.where(s > 0)[0]
        if len(nonzero_idx) < 2:
            val = s[nonzero_idx[0]] if len(nonzero_idx) > 0 else 0
            return np.full(forecast_horizon, val), 70.0

        demands = s[nonzero_idx]
        intervals = np.diff(nonzero_idx, prepend=-1)
        intervals[intervals == 0] = 1

        alpha = 0.1

        level_q = demands[0]
        for d in demands[1:]:
            level_q = alpha * d + (1 - alpha) * level_q

        level_p = intervals[0]
        for p in intervals[1:]:
            level_p = alpha * p + (1 - alpha) * level_p

        forecast_val = level_q / max(level_p, 1)
        forecast = np.full(forecast_horizon, max(0, forecast_val))

        accuracy = 90.0
        if len(nonzero_idx) >= 6:
            test_nz = nonzero_idx[-min(6, len(nonzero_idx)):]
            actual_part = s[test_nz[0]:]
            if len(actual_part) > 0 and np.sum(actual_part) > 0:
                naive_pred = np.full(len(actual_part), forecast_val)
                mape = np.mean(np.abs((actual_part - naive_pred + 1e-9) / (np.maximum(actual_part, 1))))
                accuracy = max(50.0, min(99.0, 100.0 * (1 - mape)))

        return forecast, accuracy


# ============================================================
#  预测引擎模块（算法调度 + 回溯验证 + 结果生成）
# ============================================================

class ForecastEngine:
    """预测引擎：协调数据聚合、算法选择、结果输出"""

    PREDICTORS = [
        RFPredictor(),
        HWPredictor(),
        CrostonPredictor(),
        SMAPredictor(),
        MedianPredictor(),
        NaivePredictor(),
    ]

    # =========================================================
    #  回溯验证与序列特征分析（新增）
    # =========================================================

    @staticmethod
    def smape(actual: np.ndarray, predicted: np.ndarray) -> float:
        """计算 sMAPE（对称平均百分比误差），返回值 ∈ [0, 200]"""
        actual = np.array(actual, dtype=float).flatten()
        predicted = np.array(predicted, dtype=float).flatten()
        denom = (np.abs(actual) + np.abs(predicted)) / 2.0
        return float(np.mean(np.abs(actual - predicted) / (denom + 1e-9))) * 100

    @staticmethod
    def backtest(predictor, series: np.ndarray, test_horizon: int = 3) -> float:
        """回溯验证：用扩展窗口法评估预测器，返回 sMAPE（越低越好）。

        做法：
          1. 保留最后 test_horizon 期作为测试集
          2. 用前面所有数据训练，预测测试集
          3. 用 sMAPE 比较预测值与真实值
        """
        n = len(series)
        if n <= test_horizon + 3:
            return 50.0  # 数据太少，给中等分数

        train = series[:-test_horizon]
        test = series[-test_horizon:]

        try:
            forecast, _ = predictor.fit_predict(train, test_horizon)
            smape_val = ForecastEngine.smape(test, forecast)
            return smape_val
        except Exception:
            return 100.0  # 预测失败，给最差分数

    @staticmethod
    def characterize_series(series: np.ndarray) -> Dict[str, float]:
        """分析时间序列特征，返回特征字典。"""
        s = np.array(series, dtype=float).flatten()
        n = len(s)
        nonzero = s[s > 0]
        nz_len = len(nonzero)

        # 稀疏度：0值占比（越高 = 间歇性越强）
        sparsity = float(np.sum(s == 0)) / max(n, 1)

        # 趋势强度：用首尾各1/3数据比较（忽略全0）
        trend_strength = 0.0
        if n >= 6:
            head = np.mean(s[:max(1, n // 3)])
            tail = np.mean(s[-max(1, n // 3):])
            if head > 0:
                trend_strength = (tail - head) / head
            elif tail > 0:
                trend_strength = 1.0

        # 变异系数：稳定性（越低 = 越稳定）
        cv = float(np.std(nonzero) / (np.mean(nonzero) + 1e-9)) if nz_len > 1 else 1.0

        # 数据量
        n_points = n

        return {
            'sparsity': sparsity,
            'trend_strength': trend_strength,
            'cv': cv,
            'n_points': n_points,
            'n_nonzero': nz_len,
        }

    @staticmethod
    def preselect_predictors(series: np.ndarray) -> List[BasePredictor]:
        """根据序列特征预选候选算法，减少不必要的回溯验证。"""
        feats = ForecastEngine.characterize_series(series)
        candidates = list(ForecastEngine.PREDICTORS)

        # 高度稀疏（>50% 为0）→ 优先 Croston，禁用 RF（数据太少）
        if feats['sparsity'] > 0.5:
            candidates = [p for p in candidates if p.name in ('Croston', 'Naive', 'Median')]
            return candidates

        # 强趋势（增长 >50% 或下降 >30%）→ 优先 HW / RF
        if abs(feats['trend_strength']) > 0.5 and feats['n_points'] >= 8:
            # 保留 HW、RF、SMA，禁用 Naive（趋势下 Naive 最差）
            candidates = [p for p in candidates if p.name not in ('Naive', 'Median')]

        # 非常稳定（CV < 0.3）→ 优先 SMA / Median
        if feats['cv'] < 0.3 and feats['n_nonzero'] >= 4:
            candidates = [p for p in candidates if p.name in ('SMA', 'Median', 'Naive', 'Croston')]

        return candidates if candidates else list(ForecastEngine.PREDICTORS)

    # =========================================================
    #  核心方法：数据加载 / 预测执行 / 耗时估算
    # =========================================================

    # =========================================================
    #  维度定义（三种预测粒度）
    # =========================================================
    DIMENSIONS = {
        'model': {
            'groupby': ['channel', 'category', 'subcategory', 'model'],
            'label': '渠道型号',
        },
        'subcategory': {
            'groupby': ['channel', 'category', 'subcategory'],
            'label': '渠道细分分类',
        },
        'category': {
            'groupby': ['channel', 'category'],
            'label': '渠道大类',
        },
    }

    # 维度显示顺序（对应左侧导航栏）
    DIM_ORDER = ['model', 'subcategory', 'category']

    def __init__(self, data_loader: DataLoader):
        self.loader = data_loader
        self.raw_df = None
        self.result_df = None

    def load_data(self):
        self.raw_df = self.loader.load_raw_data()
        return self.raw_df

    def run_forecast(
        self,
        dimension: str = 'model',
        forecast_months: int = 5,
        filter_channel: Optional[str] = None,
        filter_category: Optional[str] = None,
        filter_subcategory: Optional[str] = None,
        filter_model: Optional[str] = None,
        algorithm_filter: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        if self.raw_df is None:
            self.load_data()

        df = self.raw_df.copy()
        dim_info = self.DIMENSIONS.get(dimension, self.DIMENSIONS['model'])
        groupby_cols = dim_info['groupby']

        # ---- 应用筛选 ----
        # filter_channel 现在可能是 '线上'/'线下'/'线上和线下'/None
        if filter_channel:
            if filter_channel == '线上':
                df = df[df['channel'] == '线上']
            elif filter_channel == '线下':
                df = df[df['channel'] == '线下']
            # '线上和线下' 不做过滤（保留全部）

        if filter_category and filter_category != '全部':
            df = df[df['category'] == filter_category]
        if filter_subcategory and filter_subcategory not in ('全部', None, ''):
            df = df[df['subcategory'] == filter_subcategory]
        if filter_model and filter_model not in ('全部', None, ''):
            df = df[df['model'].str.contains(filter_model, na=False)]

        # ---- 构建时间序列透视表 ----
        if df.empty:
            return pd.DataFrame()

        pivot = df.pivot_table(
            index=groupby_cols,
            columns='period',
            values='sales_qty',
            aggfunc='sum',
            fill_value=0,
        )
        all_periods = sorted(df['period'].unique())
        if not all_periods:
            return pd.DataFrame()
        for p in all_periods:
            if p not in pivot.columns:
                pivot[p] = 0
        pivot = pivot[sorted(pivot.columns)]

        # ---- 获取最新账期，确定预测目标月份 ----
        latest_period = all_periods[-1]
        target_periods = self._generate_future_periods(latest_period, forecast_months)

        # ---- 智能选择算法（根据数据量） ----
        n_groups = len(pivot)
        predictors = list(self.PREDICTORS)
        if algorithm_filter:
            predictors = [p for p in predictors if p.name in algorithm_filter]
        if dimension == 'model' and n_groups > 200 and not algorithm_filter:
            predictors = [p for p in predictors if p.name not in ('RF', 'HW')]
        elif dimension == 'subcategory' and n_groups > 100 and not algorithm_filter:
            predictors = [p for p in predictors if p.name != 'RF']

        # ---- 准备预测任务 ----
        tasks = []
        for idx, row in pivot.iterrows():
            series_values = row.values.astype(float)
            if series_values.sum() < 1:
                continue
            tasks.append((idx, series_values))

        # ---- 并行执行预测 ----
        results = []
        max_workers = min(8, os.cpu_count() or 4)
        completed = 0
        total_tasks = len(tasks)

        if total_tasks <= 30 or max_workers <= 1:
            for i, (idx, series_values) in enumerate(tasks):
                result = self._predict_one_group(
                    idx, series_values, predictors, forecast_months,
                    pivot, target_periods
                )
                if result is not None:
                    results.append(result)
                completed += 1
        else:
            def _task_fn(args):
                idx, sv = args
                return self._predict_one_group(
                    idx, sv, predictors, forecast_months,
                    pivot, target_periods
                )

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_task_fn, t): t for t in tasks}
                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result is not None:
                            results.append(result)
                    except Exception:
                        pass
                    completed += 1

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results)

        # 添加合计行
        total_row = OrderedDict([('渠道', ''), ('型号', ''), ('细分类', ''), ('预测算法', ''), ('准确率', '')])
        # 只对纯数值列做合计（排除含字符串的列如准确率、数据状态等）
        numeric_cols = [c for c in result_df.columns
                        if c not in ('渠道', '型号', '细分类', '预测算法', '准确率', '数据状态')
                        and result_df[c].dtype.kind in 'iuf']
        for col in numeric_cols:
            total_row[col] = int(result_df[col].sum()) if result_df[col].dtype.kind in 'iuf' else round(result_df[col].sum(), 1)
        total_df = pd.DataFrame([total_row])
        result_df = pd.concat([total_df, result_df], ignore_index=True)

        self.result_df = result_df
        return result_df

    def estimate_time(self, dimension: str, filter_channel: Optional[str] = None,
                      filter_model: Optional[str] = None) -> float:
        """
        快速估算预测所需时间（秒），用于加载动画展示。
        通过快速查询分组数来估算。
        """
        if self.raw_df is None:
            try:
                self.load_data()
            except Exception:
                return 10.0

        df = self.raw_df.copy()
        dim_info = self.DIMENSIONS.get(dimension, self.DIMENSIONS['model'])
        groupby_cols = dim_info['groupby']

        # 应用同样的筛选逻辑
        if filter_channel:
            if filter_channel == '线上':
                df = df[df['channel'] == '线上']
            elif filter_channel == '线下':
                df = df[df['channel'] == '线下']

        if filter_model and filter_model not in ('全部', None, ''):
            df = df[df['model'].str.contains(filter_model, na=False)]

        try:
            n_groups = df.groupby(groupby_cols).ngroups
        except Exception:
            n_groups = 500

        # 经验公式：基础时间 + 每组耗时 * 分组数 / 并行度
        base = 1.5
        per_group = 0.08 if dimension == 'model' else (0.15 if dimension == 'subcategory' else 0.25)
        parallel_factor = min(8, os.cpu_count() or 4)

        estimated = base + (n_groups * per_group / parallel_factor)

        # 根据维度调整系数
        if dimension == 'model':
            estimated *= 1.2  # 型号维度数据量大
        elif dimension == 'category':
            estimated *= 0.6  # 大类维度快一些

        return round(max(2.0, min(estimated, 120.0)), 1)

    def _predict_one_group(self, idx, series_values, predictors, forecast_months, pivot, target_periods):
        """对单个分组执行预测（用回溯验证选最优算法）
        
        数据充足度判断：
        - 充足：历史数据 ≥ 12 个月且非零值 ≥ 6
        - 一般：历史数据 ≥ 6 个月且非零值 ≥ 3
        - 不足：历史数据 < 6 个月或非零值 < 3
        - 严重不足：历史数据 < 3 个月或全为 0
        """
        n_total = len(series_values)
        n_nonzero = int(np.sum(series_values > 0))
        last_nonzero = max(1, n_total - int(np.argmax(series_values[::-1] > 0))) if n_nonzero > 0 else 0

        # ---- 数据状态评估 ----
        if n_total < 3 or n_nonzero == 0:
            data_status = '严重不足'
            best_acc = None  # 标记为数据不足，表格中显示"数据不足"
        elif n_total < 6 or n_nonzero < 3:
            data_status = '不足'
            best_acc = None
        elif n_total >= 12 and n_nonzero >= 6:
            data_status = '充足'
        else:
            data_status = '一般'

        # ---- 第一步：回溯验证，选 sMAPE 最低的算法 ----
        # 数据不足时跳过回溯验证，直接用朴素预测
        if data_status in ('严重不足', '不足'):
            best_predictor = predictors[0] if predictors else NaivePredictor()
            best_smape = 80.0  # 默认较低准确率
        else:
            test_h = max(2, min(4, n_total // 4))
            test_h = min(test_h, forecast_months)

            best_smape = float('inf')
            best_predictor = None

            for predictor in predictors:
                try:
                    smape_val = ForecastEngine.backtest(predictor, series_values, test_h)
                    if smape_val < best_smape:
                        best_smape = smape_val
                        best_predictor = predictor
                except Exception:
                    continue

            if best_predictor is None:
                best_predictor = predictors[0] if predictors else NaivePredictor()
                best_smape = 50.0

        # ---- 第二步：用最优算法做正式预测 ----
        try:
            best_forecast, _ = best_predictor.fit_predict(series_values, forecast_months)
        except Exception:
            best_forecast = np.zeros(forecast_months)

        best_algo_name = best_predictor.name
        # 准确率：数据不足时设为 None（表格中显示"数据不足"）
        if data_status in ('严重不足', '不足'):
            best_acc = None
        else:
            best_acc = max(0.0, min(100.0, 100.0 - best_smape))

        # ---- 第三步：构建结果行 ----
        result_row = OrderedDict([
            ('渠道', idx[0]),
            ('型号', idx[3] if len(idx) > 3 else ''),
            ('细分类', idx[2] if len(idx) > 2 else ''),
            ('预测算法', best_algo_name),
            ('准确率', round(best_acc, 2) if best_acc is not None else '数据不足'),
            ('数据状态', data_status),
        ])

        hist_cols = list(pivot.columns)[-forecast_months:] if len(pivot.columns) >= forecast_months else list(pivot.columns)
        for hc in hist_cols:
            result_row[hc] = int(pivot.loc[idx, hc])

        for tp, fv in zip(target_periods, best_forecast):
            result_row[tp] = round(float(fv), 1)

        return result_row

    @staticmethod
    def _generate_future_periods(last_period: str, n: int) -> List[str]:
        """根据最后一个账期生成未来 n 个账期"""
        year = int(last_period[:4])
        month = int(last_period[4:])
        periods = []
        for _ in range(n):
            month += 1
            if month > 12:
                month = 1
                year += 1
            periods.append(f"{year}{month:02d}")
        return periods


# ============================================================
#  GUI 模块 (PySide6)
# ============================================================

try:
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGridLayout, QPushButton, QLineEdit, QLabel, QComboBox,
        QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
        QSplitter, QGroupBox, QListWidget, QListWidgetItem, QProgressBar, QFileDialog,
        QCheckBox, QSpinBox, QDialog, QDialogButtonBox, QAbstractItemView, QFrame,
        QSizePolicy, QButtonGroup, QRadioButton, QCompleter,
    )
    from PySide6.QtCore import Qt, QTimer, Signal, QThread, QObject, QPropertyAnimation, QEasingCurve
    from PySide6.QtGui import QFont, QColor, QBrush, QPalette, QPainter
    HAS_PYSIDE6 = True
except ImportError as _e:
    HAS_PYSIDE6 = False


# ============================================================
#  全局样式表（左暗右亮分体主题）
#  左侧导航栏保持暗色主题，右侧内容区为浅色主题
# ============================================================
TABLE_STYLESHEET = """
/* ---- 全局默认（暗色底，nav 依赖此默认值） ---- */
QMainWindow { background-color: #1e1e2e; }
QWidget { color: #333333; font-family: "Microsoft YaHei", sans-serif; font-size: 13px; }

QLabel#title_label { color: #89b4fa; font-size: 16px; font-weight: bold; padding: 8px; }

/* ---- 左侧导航栏（暗色主题保持） ---- */
QWidget#nav_panel { background-color: #181825; }
QWidget#nav_panel QLabel { color: #cdd6f4; }
QWidget#nav_panel QLabel#title_label { color: #89b4fa; }
QListWidget#nav_list {
    background-color: transparent; border: none; outline: none; font-size: 13px;
    color: #a6adc8;
}
QListWidget#nav_list::item { padding: 10px 14px; border-radius: 6px; color: #a6adc8; margin: 2px 6px; }
QListWidget#nav_list::item:hover { background-color: #313244; color: #cdd6f4; }
QListWidget#nav_list::item:selected { background-color: #45475a; color: #89b4fa; font-weight: bold; border-left: 3px solid #89b4fa; }

/* ---- 右侧内容区（浅色主题） ---- */
QWidget#content_widget { background-color: #f8f9fa; }
QWidget#content_widget QLabel { color: #333333; }
QWidget#content_widget QLabel#dim_title { color: #5e4b8b; font-size: 18px; font-weight: bold; padding: 8px 12px; }

/* 内容区 - 表格 */
QWidget#content_widget QTableWidget {
    background-color: #ffffff;
    color: #333333;
    gridline-color: #e0e0e0;
    border: 1px solid #cccccc;
    border-radius: 6px;
    selection-background-color: #cce5ff;
    selection-color: #004085;
}
QWidget#content_widget QTableWidget::item { padding: 4px 8px; }
QWidget#content_widget QTableWidget::item:selected { color: #004085; background-color: #cce5ff; }
QWidget#content_widget QHeaderView::section {
    background-color: #e9ecef;
    color: #495057;
    padding: 6px 10px;
    border: none;
    border-right: 1px solid #dee2e6;
    border-bottom: 2px solid #adb5bd;
    font-weight: bold;
}

/* 内容区 - 按钮 */
QWidget#content_widget QPushButton#btn_primary {
    background-color: #0d6efd; color: #ffffff; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QWidget#content_widget QPushButton#btn_primary:hover { background-color: #0b5ed7; }
QWidget#content_widget QPushButton#btn_primary:pressed { background-color: #0a58ca; }

QWidget#content_widget QPushButton#btn_success {
    background-color: #198754; color: #ffffff; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QWidget#content_widget QPushButton#btn_success:hover { background-color: #157347; }

QWidget#content_widget QPushButton#btn_warning {
    background-color: #ffc107; color: #212529; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QWidget#content_widget QPushButton#btn_warning:hover { background-color: #ffca2c; }

QWidget#content_widget QPushButton#btn_danger {
    background-color: #dc3545; color: #ffffff; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QWidget#content_widget QPushButton#btn_danger:hover { background-color: #bb2d3b; }

QWidget#content_widget QPushButton#btn_default {
    background-color: #6c757d; color: #ffffff; border: 1px solid #6c757d; border-radius: 4px;
    padding: 6px 16px; min-height: 28px;
}
QWidget#content_widget QPushButton#btn_default:hover { background-color: #5c636a; }

/* 内容区 - 渠道按钮 */
QWidget#content_widget QPushButton#ch_btn {
    background-color: #e9ecef; color: #495057; border: 1px solid #ced4da; border-radius: 4px;
    padding: 6px 18px; font-weight: normal; min-height: 28px;
}
QWidget#content_widget QPushButton#ch_btn:hover { background-color: #dee2e6; border-color: #0d6efd; }
QWidget#content_widget QPushButton#ch_btn:checked {
    background-color: #0d6efd; color: #ffffff; border-color: #0d6efd; font-weight: bold;
}

/* 内容区 - QComboBox（浅色主题 + 消除黑框） */
QWidget#content_widget QComboBox {
    background-color: #ffffff; color: #333333;
    border: 1px solid #ced4da;
    border-radius: 4px; padding: 4px 8px; min-height: 26px;
    outline: none;
}
QWidget#content_widget QComboBox:focus { border-color: #0d6efd; }
QWidget#content_widget QComboBox:editable { background-color: #ffffff; }
QWidget#content_widget QComboBox::drop-down { border: none; width: 22px; background: transparent; }
QWidget#content_widget QComboBox::down-arrow {
    image: url(arrow_down.png);
    width: 14px; height: 14px;
}
/* 下拉列表弹窗 —— 消除原生黑框的关键规则 */
QWidget#content_widget QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #333333;
    selection-background-color: #cce5ff;
    selection-color: #004085;
    border: 1px solid #ced4da;
    border-radius: 4px;
    outline: none;
    padding: 2px 0px;
}
QWidget#content_widget QComboBox QAbstractItemView::item { padding: 5px 10px; }
QWidget#content_widget QComboBox QAbstractItemView::item:selected { background-color: #cce5ff; color: #004085; }
QWidget#content_widget QComboBox QAbstractItemView::item:hover { background-color: #e9ecef; }
QWidget#content_widget QComboBox QAbstractItemView { max-height: 200px; }

/* 内容区 - QLineEdit */
QWidget#content_widget QLineEdit {
    background-color: #ffffff; color: #333333;
    border: 1px solid #ced4da;
    border-radius: 4px; padding: 4px 8px; min-height: 22px;
}
QWidget#content_widget QLineEdit:focus { border-color: #0d6efd; }

/* 内容区 - QSpinBox */
QWidget#content_widget QSpinBox {
    background-color: #ffffff; color: #333333;
    border: 1px solid #ced4da;
    border-radius: 4px; padding: 2px 6px; min-height: 24px;
}

/* 内容区 - QGroupBox */
QWidget#content_widget QGroupBox {
    border: 1px solid #dee2e6; border-radius: 6px; margin-top: 8px; padding-top: 16px;
    font-weight: bold; color: #495057;
    background-color: #ffffff;
}
QWidget#content_widget QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }

/* ---- 全局 QStatusBar ---- */
QStatusBar { color: #6c7086; font-size: 11px; background-color: #f0f0f0; }
QProgressBar { border: 1px solid #ced4da; border-radius: 3px; text-align: center; background-color: #e9ecef; color: #495057; height: 18px; }
QProgressBar::chunk { background-color: #0d6efd; border-radius: 2px; }
"""

# ============================================================
#  加载动画遮罩层（带预计时间）
# ============================================================
class LoadingOverlay(QWidget):
    """半透明加载遮罩，带旋转动画、文字提示和预计耗时"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loading_overlay")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("""
            QWidget#loading_overlay {
                background-color: rgba(30, 30, 46, 220);
                border-radius: 0px;
            }
            QLabel#loading_text {
                color: #89b4fa;
                font-size: 16px;
                font-weight: bold;
            }
            QLabel#loading_sub {
                color: #a6adc8;
                font-size: 13px;
            }
            QLabel#time_label {
                color: #f9e2af;
                font-size: 14px;
                font-weight: bold;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)

        # 旋转动画文字
        self.spinner_label = QLabel("\u29d6")  # ⏳
        self.spinner_label.setObjectName("loading_text")
        self.spinner_label.setAlignment(Qt.AlignCenter)
        self.spinner_label.setFixedSize(64, 64)
        font = QFont("Microsoft YaHei", 32)
        self.spinner_label.setFont(font)
        layout.addWidget(self.spinner_label)

        self.text_label = QLabel("正在加载数据...")
        self.text_label.setObjectName("loading_text")
        self.text_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.text_label)

        self.sub_label = QLabel("请稍候，预测计算进行中")
        self.sub_label.setObjectName("loading_sub")
        self.sub_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.sub_label)

        # 预计耗时
        self.time_label = QLabel("")
        self.time_label.setObjectName("time_label")
        self.time_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.time_label)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(260)
        self.progress_bar.setTextVisible(True)
        layout.addWidget(self.progress_bar)

        # 旋转动画
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._dots = 0
        self._text_timer = QTimer(self)
        self._text_timer.timeout.connect(self._update_text)

    def _rotate(self):
        self._angle = (self._angle + 12) % 360
        chars = ["|", "/", "-", "\\"]
        idx = (self._angle // 90) % 4
        self.spinner_label.setText(chars[idx])

    def _update_text(self):
        self._dots = (self._dots + 1) % 4
        base = self._base_text
        dots = "\uff0e" * self._dots  # ．
        self.text_label.setText(base + dots)

    def set_text(self, text: str):
        self._base_text = text
        self.text_label.setText(text)
        self._dots = 0

    def set_sub_text(self, text: str):
        self.sub_label.setText(text)

    def set_estimated_time(self, seconds: float):
        """设置预计耗时（秒），自动格式化显示"""
        if seconds < 60:
            self.time_label.setText(f"\u23f1 \u9884\u8ba1\u8017\u65f6\uff1a{seconds:.0f} \u79d2")
        else:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            self.time_label.setText(f"\u23f1 \u9884\u8ba1\u8017\u65f6\uff1a{mins} \u520d {secs} \u79d2")

    def set_progress(self, value: int):
        self.progress_bar.setValue(value)

    def showEvent(self, event):
        self._timer.start(60)
        self._text_timer.start(400)
        self._base_text = self.text_label.text()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        self._text_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        if self.parent():
            self.resize(self.parent().size())
        super().resizeEvent(event)


# ============================================================
#  型号输入历史记录管理
# ============================================================
class ModelHistoryManager:
    """管理型号输入历史记录，持久化到 JSON 文件"""

    MAX_HISTORY = 50  # 最大保存条数

    def __init__(self, history_file: str = HISTORY_FILE):
        self.history_file = history_file
        self._history = self._load()

    def _load(self) -> List[str]:
        """从文件加载历史记录"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [str(x) for x in data if x]
            except (json.JSONDecodeError, IOError):
                pass
        return []

    def _save(self):
        """保存到文件"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self._history, f, ensure_ascii=False, indent=2)
        except IOError:
            pass

    def add(self, text: str):
        """添加一条新记录（去重，最新的排最前）"""
        text = text.strip()
        if not text:
            return
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        # 限制最大数量
        self._history = self._history[:self.MAX_HISTORY]
        self._save()

    def get_all(self) -> List[str]:
        """获取所有历史记录"""
        return list(self._history)

    def search(self, prefix: str) -> List[str]:
        """按前缀搜索匹配的历史记录"""
        prefix = prefix.lower().strip()
        if not prefix:
            return self.get_all()[:20]
        return [h for h in self._history if prefix in h.lower()][:20]


# ============================================================
#  后台预测线程
# ============================================================
class ForecastWorker(QThread):
    """后台线程：运行预测，通过信号通知主线程"""
    progress = Signal(int, str)   # (百分比, 提示文字)
    finished = Signal(object)      # 预测结果 DataFrame
    error = Signal(str)           # 错误信息

    def __init__(self, engine, dimension, months, ch, cat, subcat, model_kw):
        super().__init__()
        self.engine = engine
        self.dimension = dimension
        self.months = months
        self.ch = ch
        self.cat = cat
        self.subcat = subcat
        self.model_kw = model_kw
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            self.progress.emit(10, "正在加载数据...")
            df = self.engine.load_data()
            if df is None or df.empty:
                self.error.emit("没有可用的动销数据")
                return

            dim_info = self.engine.DIMENSIONS.get(self.dimension, self.engine.DIMENSIONS['model'])
            groupby_cols = dim_info['groupby']
            temp_df = df.copy()
            if self.ch:
                if self.ch == '线上':
                    temp_df = temp_df[temp_df['channel'] == '线上']
                elif self.ch == '线下':
                    temp_df = temp_df[temp_df['channel'] == '线下']

            if self.cat and self.cat != '全部':
                temp_df = temp_df[temp_df['category'] == self.cat]
            if self.subcat and self.subcat not in ('全部', None, ''):
                temp_df = temp_df[temp_df['subcategory'] == self.subcat]
            if self.model_kw and self.model_kw not in ('全部', None, ''):
                temp_df = temp_df[temp_df['model'].str.contains(self.model_kw, na=False)]

            n_groups = temp_df.groupby(groupby_cols)['sales_qty'].sum().astype(float)
            n_active = (n_groups > 0).sum()
            dim_label = dim_info['label']

            self.progress.emit(30,
                f"数据加载完成 | 维度: {dim_label} | 待预测: {n_active} 组 | 开始预测...")

            result_df = self.engine.run_forecast(
                dimension=self.dimension,
                forecast_months=self.months,
                filter_channel=self.ch,
                filter_category=self.cat,
                filter_subcategory=self.subcat,
                filter_model=self.model_kw,
            )

            if self._cancelled:
                return

            n_results = max(0, len(result_df) - 1) if result_df is not None else 0
            self.progress.emit(95, f"预测完成！共生成 {n_results} 条预测记录，正在更新界面...")
            self.finished.emit(result_df)

        except Exception as e:
            import traceback
            self.error.emit(f"预测出错: {e}\n{traceback.format_exc()}")

# ============================================================
#  主窗口：动销预测系统 UI（左侧导航 + 筛选区 + 结果表格）
# ============================================================
class SalesForecastWindow(QMainWindow):
    """动销预测主窗口 v2"""

    status_message = Signal(str)

    # 渠道按钮选项
    CHANNEL_OPTIONS = ['线上和线下', '线上', '线下']

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self.data_loader = DataLoader(db_path)
        self.engine = ForecastEngine(self.data_loader)
        self.current_result_df = None
        self.dimensions_data = {}
        self.worker = None
        self.model_history = ModelHistoryManager()
        # 当前选中的维度key
        self.current_dimension = 'model'
        # 当前选中的渠道
        self.current_channel = None  # None 表示"线上和线下"
        self.setWindowTitle("动销预测系统 — DYZG OMS")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)
        self.setStyleSheet(TABLE_STYLESHEET)

        self._setup_ui()
        self._fix_combo_popup_frames()
        self._load_initial_data()

    # ========== UI 搭建 ==========
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ===== 左侧导航栏（含预测维度） =====
        nav_panel = QWidget()
        nav_panel.setObjectName("nav_panel")
        nav_panel.setMaximumWidth(220)
        nav_panel.setMinimumWidth(180)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(6, 12, 6, 12)
        nav_layout.setSpacing(4)

        title = QLabel("\U0001f4ca \u9500\u552e\u7ba1\u7406")
        title.setObjectName("title_label")
        title.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(title)

        # 导航列表（包含维度选择）
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("nav_list")
        nav_items = [
            ("\U0001f3e0 \u9996\u9875", "home"),
            ("\U0001f4c8 \u6e20\u9053\u578b\u53f7\u51c6\u786e\u7387\u62a5\u8868", "model"),
            ("\U0001f4ca \u6e20\u9053\u7ec6\u5206\u7c7b\u51c6\u786e\u7387\u62a5\u8868", "subcategory"),
            ("\U0001f4cb \u6e20\u9053\u5927\u7c7b\u51c6\u786e\u7387\u62a5\u8868", "category"),
        ]
        for label, key in nav_items:
            item_text = f"  {label}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, key)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(1)  # 默认选中"渠道型号"
        self.nav_list.itemClicked.connect(self._on_nav_clicked)
        nav_layout.addWidget(self.nav_list)

        nav_spacer = QWidget()
        nav_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        nav_layout.addWidget(nav_spacer)

        main_layout.addWidget(nav_panel)

        # ===== 右侧内容区 =====
        content = QWidget()
        content.setObjectName("content_widget")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 12, 16, 12)
        content_layout.setSpacing(10)

        # --- 顶部标题区（动态显示当前维度） ---
        header_layout = QHBoxLayout()
        self.dim_title = QLabel("\U0001f52e \u52a8\u9500\u9884\u6d4a\u7387 \u2014 \u6e20\u9053\u578b\u53f7")
        self.dim_title.setObjectName("dim_title")
        header_layout.addWidget(self.dim_title)
        header_layout.addStretch()
        content_layout.addLayout(header_layout)

        # --- 筛选区 ---
        filter_box = QGroupBox("\u7b5b\u9009\u6761\u4ef6")
        filter_layout = QGridLayout(filter_box)
        filter_layout.setSpacing(10)

        # 第一行：渠道（三个按钮） + 品类
        filter_layout.addWidget(QLabel("\u6e20\u9053:"), 0, 0)

        # 渠道按钮组（替换原来的下拉框）
        self.channel_btn_group = {}
        ch_btn_layout = QHBoxLayout()
        ch_btn_layout.setSpacing(8)
        for i, ch_option in enumerate(self.CHANNEL_OPTIONS):
            btn = QPushButton(ch_option)
            btn.setObjectName("ch_btn")
            btn.setCheckable(True)
            # 默认选中"线上和线下"
            if i == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, opt=ch_option: self._on_channel_button_clicked(opt))
            self.channel_btn_group[ch_option] = btn
            ch_btn_layout.addWidget(btn)
        filter_layout.addLayout(ch_btn_layout, 0, 1, 1, 2)

        filter_layout.addWidget(QLabel("\u54c1\u7c7b:"), 0, 3)
        self.combo_category = QComboBox()
        self.combo_category.setMinimumWidth(120)
        filter_layout.addWidget(self.combo_category, 0, 4)

        # 第二行：细分类 + 型号（可编辑下拉+历史）
        filter_layout.addWidget(QLabel("\u7ec6\u5206\u7c7b:"), 1, 0)
        self.combo_subcategory = QComboBox()
        self.combo_subcategory.setMinimumWidth(130)
        filter_layout.addWidget(self.combo_subcategory, 1, 1)

        filter_layout.addWidget(QLabel("\u578b\u53f7:"), 1, 2)
        # 型号：使用可编辑的 QComboBox（支持输入 + 下拉选择 + 历史记录）
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.setPlaceholderText("\u8f93\u5165/\u9009\u62e9\u578b\u53f7\u5173\u952e\u8bcd...")
        self.combo_model.setMinimumWidth(180)
        self.combo_model.lineEdit().returnPressed.connect(self._on_model_enter_pressed)
        self.combo_model.currentTextChanged.connect(self._on_model_text_changed)
        # 自动补全
        self.combo_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)  # 不自动插入非列表项
        model_completer = self.combo_model.completer()
        if model_completer:
            model_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            model_completer.setFilterMode(Qt.MatchContains)
            model_completer.setCaseSensitivity(Qt.CaseInsensitive)
        filter_layout.addWidget(self.combo_model, 1, 3, 1, 2)

        # 第三行：预测月份（起始月 ~ 结束月）
        filter_layout.addWidget(QLabel("预测月份:"), 2, 0)

        month_widget = QWidget()
        month_layout = QHBoxLayout(month_widget)
        month_layout.setContentsMargins(0, 0, 0, 0)
        month_layout.setSpacing(6)

        # 起始月份下拉框（可编辑）
        self.combo_month_start = QComboBox()
        self.combo_month_start.setEditable(True)
        self.combo_month_start.setMinimumWidth(80)
        self.combo_month_start.setMaximumWidth(100)
        for m in range(1, 37):
            self.combo_month_start.addItem(f"{m}月")
        self.combo_month_start.setCurrentIndex(0)  # 默认1月
        month_layout.addWidget(self.combo_month_start)
        month_layout.addWidget(QLabel("~"))
        # 结束月份下拉框（可编辑）
        self.combo_month_end = QComboBox()
        self.combo_month_end.setEditable(True)
        self.combo_month_end.setMinimumWidth(80)
        self.combo_month_end.setMaximumWidth(100)
        for m in range(1, 37):
            self.combo_month_end.addItem(f"{m}月")
        self.combo_month_end.setCurrentIndex(4)  # 默认5月
        month_layout.addWidget(self.combo_month_end)

        # 起止联动约束
        self.combo_month_start.currentTextChanged.connect(self._on_month_start_changed)
        self.combo_month_end.currentTextChanged.connect(self._on_month_end_changed)

        filter_layout.addWidget(month_widget, 2, 1, 1, 2)

        # 第四行：操作按钮组
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.btn_search = QPushButton("\u641c\u7d22")
        self.btn_search.setObjectName("btn_primary")
        self.btn_search.clicked.connect(self._on_search)
        btn_layout.addWidget(self.btn_search)

        self.btn_reset = QPushButton("\u91cd\u7f6e")
        self.btn_reset.setObjectName("btn_default")
        self.btn_reset.clicked.connect(self._on_reset)
        btn_layout.addWidget(self.btn_reset)

        self.btn_export = QPushButton("\u5bfc\u51fa")
        self.btn_export.setObjectName("btn_success")
        self.btn_export.clicked.connect(self._on_export)
        btn_layout.addWidget(self.btn_export)

        self.btn_refresh = QPushButton("\u5237\u65b0\u6570\u636e")
        self.btn_refresh.setObjectName("btn_warning")
        self.btn_refresh.clicked.connect(self._on_refresh)
        btn_layout.addWidget(self.btn_refresh)

        filter_layout.addLayout(btn_layout, 3, 0, 1, 5)

        content_layout.addWidget(filter_box)

        # --- 结果表格 ---
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.verticalHeader().setDefaultSectionSize(28)
        content_layout.addWidget(self.table, stretch=1)

        # --- 底部状态栏 ---
        self.statusBar().showMessage("\u5c31\u7eea \u2014 \u8bf7\u70b9\u51fb\u300c\u641c\u7d22\u300d\u5f00\u59cb\u9884\u6d4a")

        main_layout.addWidget(content, stretch=1)

        # ===== 加载动画遮罩 =====
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.hide()

    # ========== QComboBox 下拉弹窗去原生黑框 ==========
    def _fix_combo_popup_frames(self):
        """移除所有 QComboBox 下拉列表的原生 Windows 黑框"""
        combos = [self.combo_category, self.combo_subcategory, self.combo_model,
                  self.combo_month_start, self.combo_month_end]
        for combo in combos:
            view = combo.view()
            if view:
                view.setFrameShape(QFrame.NoFrame)
                view.verticalScrollBar().setStyleSheet("""
                    QScrollBar:vertical {
                        background: #f0f0f0; width: 8px;
                        border: none; border-radius: 4px;
                    }
                    QScrollBar::handle:vertical {
                        background: #c0c0c0; border-radius: 4px; min-height: 20px;
                    }
                    QScrollBar::handle:vertical:hover { background: #a0a0a0; }
                    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
                    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
                """)

    # ========== 初始数据加载 ==========
    def _load_initial_data(self):
        """初始化时加载数据填充下拉框和型号列表"""
        self.statusBar().showMessage("\u6b63\u5728\u52a0\u8f7d\u6570\u636e...")
        QApplication.processEvents()

        try:
            self.dimensions_data = self.data_loader.get_dimensions()
            categories = self.dimensions_data.get('categories', [])
            subcategories = self.dimensions_data.get('subcategories', [])
            models = self.dimensions_data.get('models', [])

            # 品类
            self.combo_category.clear()
            self.combo_category.addItem('\u5168\u90e8')
            self.combo_category.addItems(categories)

            # 细分类
            self.combo_subcategory.clear()
            self.combo_subcategory.addItem('\u5168\u90e8')
            self.combo_subcategory.addItems(subcategories)

            # 型号（填入下拉列表 + 历史记录）
            self._populate_model_combo(models)

            period_list = self.dimensions_data.get('periods', [])
            if period_list:
                self.statusBar().showMessage(
                    f"\u6570\u636e\u52a0\u8f7e\u5b8c\u6210 | \u8d26\u671f\u8303\u56f4: {period_list[0]} ~ {period_list[-1]}"
                )
        except Exception as e:
            self.statusBar().showMessage(f"\u6570\u636e\u52a0\u8f7d\u5931\u8d25: {e}")
            QMessageBox.critical(self, "\u9519\u8bef", f"\u65e0\u6cd5\u52a0\u8f7d\u6570\u636e\u5e93:\n{e}")

    def _populate_model_combo(self, db_models: List[str]):
        """填充型号下拉框：数据库选项 + 历史记录"""
        self.combo_model.clear()
        self.combo_model.setPlaceholderText("\u8f93\u5165/\u9009\u62e9\u578b\u53f7...")

        # 先加入历史记录（标记为历史）
        history = self.model_history.get_all()
        history_set = set(history)

        # 加入数据库中的型号（排除已在历史中的避免重复）
        added = set()
        for h in history:
            self.combo_model.addItem(h)
            added.add(h)

        for m in db_models:
            if m not in added:
                self.combo_model.addItem(m)
                added.add(m)

    # ========== 导航/维度切换 ==========
    def _on_nav_clicked(self, item):
        """左侧导航点击事件 —— 切换预测维度"""
        dim_key = item.data(Qt.UserRole)
        if dim_key == 'home':
            # 首页暂不实现具体功能，仅清空表格
            self.table.setColumnCount(0)
            self.table.setRowCount(0)
            self.current_dimension = None
            self.dim_title.setText("\U0001f3e0 \u9996\u9875")
            return

        if dim_key in ('model', 'subcategory', 'category'):
            self.current_dimension = dim_key
            dim_info = self.engine.DIMENSIONS[dim_key]
            label = dim_info['label']

            # 更新标题
            self.dim_title.setText(f"\U0001f52e \u52a8\u9500\u9884\u6d4a\u7387 \u2014 {label}")

            # 高亮左侧导航当前项
            # （QListWidget 已自动处理 selected 状态）

            self.statusBar().showMessage(f"\u5df2\u5207\u6362\u81f3: {label} \u7ef4\u5ea6")

    # ========== 渠道按钮 ==========
    def _on_channel_button_clicked(self, option: str):
        """渠道按钮点击"""
        # 取消其他按钮的选中状态
        for opt, btn in self.channel_btn_group.items():
            if opt != option:
                btn.setChecked(False)
        # 当前选中的按钮保持选中
        self.channel_btn_group[option].setChecked(True)
        self.current_channel = option if option != '\u7ebf\u4e0a\u548c\u7ebf\u4e0b' else None

    def _get_selected_channel(self) -> Optional[str]:
        """获取当前选中的渠道值"""
        for opt, btn in self.channel_btn_group.items():
            if btn.isChecked():
                if opt == '\u7ebf\u4e0a\u548c\u7ebf\u4e0b':
                    return None  # 不过滤
                return opt
        return None

    # ========== 型号输入处理 ==========
    def _on_model_enter_pressed(self):
        """回车键提交型号时保存到历史记录"""
        text = self.combo_model.currentText().strip()
        if text:
            self.model_history.add(text)
            # 更新下拉列表（将新输入放到最前面）
            models = self.dimensions_data.get('models', [])
            self._populate_model_combo(models)

    def _on_model_text_changed(self, text: str):
        """型号文本变化时的实时过滤提示（可选增强）"""
        pass  # QCompleter 已自动处理补全

    # ========== 月份区间处理 ==========
    def _get_month_value(self, combo) -> int:
        """从月份下拉框安全读取值（支持手动输入）"""
        text = combo.currentText().strip()
        # 提取数字
        m = re.search(r'(\d+)', text)
        if m:
            val = int(m.group(1))
            return max(1, min(36, val))
        return 1

    def _on_month_start_changed(self, text: str):
        """起始月份变化时确保不超过结束月份"""
        start = self._get_month_value(self.combo_month_start)
        end = self._get_month_value(self.combo_month_end)
        if start > end:
            # 设置结束月份为相同值
            idx = max(0, min(35, start - 1))
            self.combo_month_end.blockSignals(True)
            self.combo_month_end.setCurrentIndex(idx)
            self.combo_month_end.blockSignals(False)

    def _on_month_end_changed(self, text: str):
        """结束月份变化时确保不小于起始月份"""
        start = self._get_month_value(self.combo_month_start)
        end = self._get_month_value(self.combo_month_end)
        if end < start:
            idx = max(0, min(35, end - 1))
            self.combo_month_start.blockSignals(True)
            self.combo_month_start.setCurrentIndex(idx)
            self.combo_month_start.blockSignals(False)

    def _get_forecast_months(self) -> int:
        """获取实际预测月数（起止差值 + 1）"""
        start = self._get_month_value(self.combo_month_start)
        end = self._get_month_value(self.combo_month_end)
        return max(1, end - start + 1)


    # ========== 搜索/预测 ==========
    def _on_search(self):
        """执行搜索和预测（启动后台线程）"""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)

        if not self.current_dimension:
            QMessageBox.information(self, "\u63d0\u793a", "\u8bf7\u5148\u5728\u5de6\u4fa7\u5bfc\u822a\u680f\u9009\u62e9\u4e00\u4e2a\u9884\u6d4a\u7ef4\u5ea6")
            return

        channel = self._get_selected_channel()
        category = self.combo_category.currentText() if self.combo_category.currentIndex() > 0 else None
        subcat = self.combo_subcategory.currentText() if self.combo_subcategory.currentIndex() > 0 else None
        model_kw = self.combo_model.currentText().strip() or None
        months = self._get_forecast_months()

        dim_label = self.engine.DIMENSIONS[self.current_dimension]['label']

        # 计算预计耗时并显示
        est_time = self.engine.estimate_time(
            dimension=self.current_dimension,
            filter_channel=channel,
            filter_model=model_kw,
        )

        # 显示加载遮罩
        self.loading_overlay.set_text("\u6b63\u5728\u9884\u6d4b\u8ba1\u7b97")
        ch_display = channel if channel else "\u5168\u90e8"
        self.loading_overlay.set_sub_text(
            f"\u7ef4\u5ea6: {dim_label}  |  \u9884\u6d4a\u6708\u6570: {months}\u4e2a\u6708  |  \u6e20\u9053: {ch_display}"
        )
        self.loading_overlay.set_estimated_time(est_time)
        self.loading_overlay.set_progress(5)
        self.loading_overlay.show()
        self.loading_overlay.raise_()

        self.btn_search.setEnabled(False)
        self.statusBar().showMessage("\u6b63\u5728\u540e\u53f0\u9884\u6d4a\uff0c\u754c\u9762\u53ef\u81ea\u7531\u64cd\u4f5c...")

        # 启动后台线程
        self.worker = ForecastWorker(
            engine=self.engine,
            dimension=self.current_dimension,
            months=months,
            ch=channel, cat=category, subcat=subcat, model_kw=model_kw,
        )
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.error.connect(self._on_worker_error)
        self.worker.start()

    def _on_worker_progress(self, pct: int, text: str):
        self.loading_overlay.set_text(text)
        self.loading_overlay.set_progress(pct)
        self.statusBar().showMessage(text)

    def _on_worker_finished(self, result_df):
        self.current_result_df = result_df
        self._populate_table(result_df)
        self.loading_overlay.hide()
        self.btn_search.setEnabled(True)
        dim_label = self.engine.DIMENSIONS.get(self.current_dimension, {}).get('label', '')
        n_records = max(0, len(result_df) - 1) if result_df is not None and not result_df.empty else 0
        self.statusBar().showMessage(
            f"\u9884\u6d4a\u5b8c\u6210 | \u5171 {n_records} \u6761\u8bb0\u5f55 | \u7ef4\u5ea6: {dim_label}"
        )
        if (result_df is None or result_df.empty) and n_records == 0:
            QMessageBox.information(self, "\u63d0\u793a", "\u672a\u627e\u5230\u7b26\u5408\u7b5b\u9009\u6761\u4ef6\u7684\u6570\u636e\uff0c\u8bf7\u8c03\u6574\u7b5b\u9009\u6761\u4ef6\u540e\u91cd\u8bd5\u3002")

    def _on_worker_error(self, msg: str):
        self.loading_overlay.hide()
        self.btn_search.setEnabled(True)
        self.statusBar().showMessage("\u9884\u6d4a\u51fa\u9519")
        QMessageBox.warning(self, "\u8b66\u544a", msg[:500])

    # ========== 表格填充 ==========
    def _populate_table(self, df: pd.DataFrame):
        """将预测结果填充到表格"""
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)

        if df is None or df.empty or len(df) == 0:
            self.table.setColumnCount(0)
            self.table.setRowCount(0)
            self.table.setUpdatesEnabled(True)
            return

        columns = list(df.columns)
        n_rows = len(df)
        n_cols = len(columns)

        self.table.setColumnCount(n_cols)
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setRowCount(n_rows)

        col_widths = {'渠道': 55, '型号': 130, '细分类': 90, '预测算法': 65, '准确率': 70, '数据状态': 65}
        for ci, col in enumerate(columns):
            self.table.setColumnWidth(ci, col_widths.get(col, 95))

        for ri in range(n_rows):
            row = df.iloc[ri]
            for ci, col in enumerate(columns):
                val = row[col]
                if pd.isna(val) or val == '':
                    text = ""
                elif isinstance(val, (float, np.floating)):
                    text = f"{val:.1f}" if val != int(val) else str(int(val))
                elif isinstance(val, (int, np.integer)):
                    text = str(int(val))
                else:
                    text = str(val)

                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)

                if ri == 0:
                    item.setBackground(QBrush(QColor("#e9ecef")))
                    item.setForeground(QBrush(QColor("#212529")))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                # ---- 数据状态列：颜色标注 ----
                if col == '数据状态':
                    if text == '充足':
                        item.setForeground(QBrush(QColor("#198754")))  # 深绿
                    elif text == '一般':
                        item.setForeground(QBrush(QColor("#fd7e14")))  # 琥珀色
                    elif text == '不足':
                        item.setForeground(QBrush(QColor("#e8590c")))  # 橙色
                    else:  # 严重不足
                        item.setForeground(QBrush(QColor("#dc3545")))  # 红色
                # ---- 准确率列：数据不足时标红 ----
                elif col == '准确率' and isinstance(val, str):
                    item.setForeground(QBrush(QColor("#dc3545")))  # 红色
                # ---- 预测值列：正负颜色 ----
                elif ci >= 5 and col not in ('准确率', '数据状态', '预测算法'):
                    try:
                        v = float(str(val).replace(',', '')) if val else 0
                        if v > 0:
                            item.setForeground(QBrush(QColor("#198754")))
                        elif v < 0:
                            item.setForeground(QBrush(QColor("#dc3545")))
                    except (ValueError, TypeError):
                        pass

                self.table.setItem(ri, ci, item)

        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)

    # ========== 重置 ==========
    def _on_reset(self):
        """重置筛选条件"""
        # 重置渠道按钮
        for opt, btn in self.channel_btn_group.items():
            if opt == '\u7ebf\u4e0a\u548c\u7ebf\u4e0b':
                btn.setChecked(True)
            else:
                btn.setChecked(False)
        self.current_channel = None

        self.combo_category.setCurrentIndex(0)
        self.combo_subcategory.setCurrentIndex(0)
        self.combo_model.setCurrentText('')
        self.combo_model.lineEdit().setPlaceholderText("\u8f93\u5165/\u9009\u62e9\u578b\u53f7\u5173\u952e\u8bcd...")

        self.combo_month_start.setCurrentIndex(0)
        self.combo_month_end.setCurrentIndex(4)  # 默认5月

        self.table.setColumnCount(0)
        self.table.setRowCount(0)
        self.current_result_df = None
        self.statusBar().showMessage("\u5df2\u91cd\u7f6e\u7b5b\u9009\u6761\u4ef6")

    # ========== 导出 ==========
    def _on_export(self):
        """导出为 CSV"""
        if self.current_result_df is None or self.current_result_df.empty:
            QMessageBox.information(self, "\u63d0\u793a", "\u6682\u65e0\u6570\u636e\u53ef\u5bfc\u51fa\uff0c\u8bf7\u5148\u6267\u884c\u641c\u7d22\u3002")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "\u5bfc\u51fa\u9884\u6d4b\u7ed3\u679c",
            f"\u52a8\u9500\u9884\u6d4a_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV \u6587\u4ef6 (*.csv);;\u6240\u6709\u6587\u4ef6 (*)",
        )
        if not path:
            return

        try:
            self.current_result_df.to_csv(path, index=False, encoding='utf-8-sig')
            self.statusBar().showMessage(f"\u5df2\u5bfc\u51fa\u5230: {path}")
            QMessageBox.information(self, "\u6210\u529f", f"\u6587\u4ef6\u5df2\u5bfc\u51fa\u5230:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "\u5bfc\u51fa\u5931\u8d25", str(e))

    def _on_refresh(self):
        """重新加载数据"""
        self._load_initial_data()
        self.statusBar().showMessage("\u6570\u636e\u5df2\u5237\u65b0")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.quit()
            self.worker.wait(3000)
        # 关闭前保存当前型号输入到历史
        current_model = self.combo_model.currentText().strip()
        if current_model:
            self.model_history.add(current_model)
        self.data_loader.close()
        super().closeEvent(event)


# ============================================================
#  入口
# ============================================================

def find_db_path():
    """查找数据库文件"""
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return sys.argv[1]

    candidates = [
        os.path.join(os.path.dirname(__file__), '..', '\u4efb\u52a1\u4e94', 'oms_sales_data.sqlite'),
        r"C:\Users\cheng\Desktop\作业文件夹\实习\cangku\202321101063\任务五\oms_sales_data.sqlite",
        os.path.join(os.path.dirname(__file__), 'oms_sales_data.sqlite'),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def main():
    db_path = find_db_path()
    if not db_path:
        print("[\u9519\u8bef] \u672a\u627e\u5230\u6570\u636e\u5e93\u6587\u4ef6 oms_sales_data.sqlite")
        print("\u7528\u6cd5: python sales_forecast.py [\u6570\u636e\u5e93\u8def\u5f84]")
        print("      \u6216\u786e\u4fdd ../任务五/oms_sales_data.sqlite \u5b58\u5728")
        input("\u6309\u56de\u8f66\u9000\u51fa...")
        sys.exit(1)

    print(f"[\u4fe1\u606f] \u4f7f\u7528\u6570\u636e\u5e93: {db_path}")

    if not HAS_PYSIDE6:
        print("\n" + "=" * 54)
        print("[提示] 未检测到 PySide6，无法启动图形界面。")
        print("=" * 54)
        print()
        print("  1. 安装 PySide6（需要联网，约 60~100 MB）")
        print("  2. 退出（直接回车）")
        print()

        try:
            choice = input("请选择 (输入 1 或直接回车退出): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(1)

        if choice == '1':
            import subprocess
            pip_cmd = [sys.executable, "-m", "pip", "install", "PySide6",
                        "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"]
            print("\n[信息] 正在安装 PySide6，请稍候...\n")
            ret = subprocess.run(pip_cmd, check=False)

            # 真正验证安装是否可用（而不仅仅看 pip 返回码）
            py_cp = subprocess.run(
                [sys.executable, "-c", "import PySide6; print('PySide6', PySide6.__version__)"],
                capture_output=True, text=True, check=False
            )
            if py_cp.returncode == 0:
                ver = py_cp.stdout.strip().replace("PySide6 ", "")
                print(f"\n[成功] PySide6 安装完成（版本 {ver}）！请重新运行本程序。")
            else:
                print("\n[错误] pip 安装成功，但 import PySide6 仍失败。")
                print("可能原因：pip 安装到了其他 Python 环境。")
                print("请在 VS Code 终端手动执行以下命令：")
                print(f'  "{sys.executable}" -m pip install PySide6')
                print("安装完成后，在 VS Code 终端运行以下命令验证：")
                print(f'  "{sys.executable}" -c "import PySide6; print(PySide6.__version__)"')
        else:
            print("\n[信息] 已退出。")

        input("\n按回车退出...")
        sys.exit(1)


    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = SalesForecastWindow(db_path)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
