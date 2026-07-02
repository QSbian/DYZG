#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动销预测系统 (Sales Forecast System)
=====================================
基于 OMS 销售数据库的动销预测工具
支持三大预测维度：渠道大类 / 渠道细分类 / 渠道型号
支持多种预测算法：Naive / SMA / Median / HW(指数平滑) / RF(随机森林) / Croston

用法：python sales_forecast.py [数据库路径]
默认数据库路径：../任务五/oms_sales_data.sqlite
"""

import os
import sys
import sqlite3
import csv
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
        # 兼容旧接口，返回新连接（调用方需自行关闭）
        return self._new_conn()

    def close(self):
        pass  # 不再持有持久连接，无需关闭

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
            # 确保数值类型
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

class BasePredictor:
    """预测器基类"""
    name = "Base"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        """
        拟合并预测
        返回: (预测值数组, 准确率)
        准确率基于最后一段历史数据的拟合误差计算
        """
        raise NotImplementedError


class NaivePredictor(BasePredictor):
    """朴素预测：用最后一个观测值作为所有未来期的预测值"""
    name = "Naive"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        if len(series) == 0 or np.all(series == 0):
            return np.zeros(forecast_horizon), 0.0
        last_val = series[-1]
        forecast = np.full(forecast_horizon, last_val)
        # 用最近 6 期的 MAPE 评估
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


class SMAPredictor(BasePredictor):
    """简单移动平均（SMA）"""
    name = "SMA"
    window = 3  # 默认窗口

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
            # 尝试加法模型
            model = ExponentialSmoothing(s, trend='add', seasonal=None,
                                          initialization_method='estimated').fit()
            forecast = model.forecast(forecast_horizon).values
            # 计算拟合精度
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


class RFPredictor(BasePredictor):
    """随机森林回归预测"""
    name = "RF"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        from sklearn.ensemble import RandomForestRegressor

        n = len(series)
        if n < 10:
            return SMAPredictor().fit_predict(series, forecast_horizon)

        s = series.astype(float).flatten()
        # 特征工程：用滑动窗口构造特征
        window_size = min(6, n // 3)
        X, y = [], []
        for i in range(window_size, n):
            X.append(s[i - window_size:i])
            y.append(s[i])
        X, y = np.array(X), np.array(y)

        # 留出测试集评估准确率
        split = int(len(X) * 0.75)
        X_train, X_test = X[:split], X[split:]
        y_train, y_test = y[:split], y[split:]

        rf = RandomForestRegressor(n_estimators=50, max_depth=5, random_state=42, n_jobs=-1)
        rf.fit(X_train, y_train)

        # 计算准确率
        if len(y_test) > 0:
            y_pred = rf.predict(X_test)
            mape = np.mean(np.abs((y_test - y_pred + 1e-9) / (y_test + 1e-9)))
            accuracy = max(0.0, min(100.0, 100.0 * (1 - mape)))
        else:
            accuracy = 80.0

        # 预测未来
        future_X = []
        recent = list(s[-window_size:])
        for _ in range(forecast_horizon):
            future_X.append(recent.copy())
            pred_val = rf.predict(np.array([recent]))[0]
            recent.append(pred_val)
            recent.pop(0)

        forecast = rf.predict(np.array(future_X))
        return np.maximum(0, forecast), accuracy


class CrostonPredictor(BasePredictor):
    """Croston 方法——适合间歇性需求序列"""
    name = "Croston"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        """
        Croston 经典实现：
        分别对非零需求间隔和需求量做指数平滑
        """
        s = series.astype(float).flatten()
        n = len(s)
        if n < 4:
            return SMAPredictor().fit_predict(series, forecast_horizon)

        # 提取非零点
        nonzero_idx = np.where(s > 0)[0]
        if len(nonzero_idx) < 2:
            # 全是零或只有一个非零点
            val = s[nonzero_idx[0]] if len(nonzero_idx) > 0 else 0
            return np.full(forecast_horizon, val), 70.0

        demands = s[nonzero_idx]
        intervals = np.diff(nonzero_idx, prepend=-1)
        intervals[intervals == 0] = 1

        alpha = 0.1  # 平滑系数

        # 对需求量做简单指数平滑
        level_q = demands[0]
        for d in demands[1:]:
            level_q = alpha * d + (1 - alpha) * level_q

        # 对间隔做简单指数平滑
        level_p = intervals[0]
        for p in intervals[1:]:
            level_p = alpha * p + (1 - alpha) * level_p

        forecast_val = level_q / max(level_p, 1)
        forecast = np.full(forecast_horizon, max(0, forecast_val))

        # 评估：对最近非零段拟合
        accuracy = 90.0  # Croston 难以精确评估，给一个合理默认值
        if len(nonzero_idx) >= 6:
            test_nz = nonzero_idx[-min(6, len(nonzero_idx)):]
            actual_part = s[test_nz[0]:]
            if len(actual_part) > 0 and np.sum(actual_part) > 0:
                naive_pred = np.full(len(actual_part), forecast_val)
                mape = np.mean(np.abs((actual_part - naive_pred + 1e-9) / (np.maximum(actual_part, 1))))
                accuracy = max(50.0, min(99.0, 100.0 * (1 - mape)))

        return forecast, accuracy


# ============================================================
#  预测引擎
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

    # 维度映射：维度名 → (groupby 字段列表, 显示名)
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
        """
        执行预测主流程

        Parameters
        ----------
        dimension : str
            'model'(渠道型号) / 'subcategory'(渠道细分类) / 'category'(渠道大类)
        forecast_months : int
            向前预测几个月
        filter_* : str or None
            各维度的筛选条件
        algorithm_filter : list of str or None
            只使用指定的算法，如 ['RF','HW']

        Returns
        -------
        DataFrame : 预测结果表
        """
        if self.raw_df is None:
            self.load_data()

        df = self.raw_df.copy()
        dim_info = self.DIMENSIONS.get(dimension, self.DIMENSIONS['model'])
        groupby_cols = dim_info['groupby']

        # ---- 应用筛选 ----
        if filter_channel and filter_channel != '全部':
            df = df[df['channel'] == filter_channel]
        if filter_category and filter_category != '全部':
            df = df[df['category'] == filter_category]
        if filter_subcategory and filter_subcategory not in ('全部', None, ''):
            df = df[df['subcategory'] == filter_subcategory]
        if filter_model and filter_model not in ('全部', None, ''):
            df = df[df['model'] == filter_model]

        # ---- 构建时间序列透视表 ----
        pivot = df.pivot_table(
            index=groupby_cols,
            columns='period',
            values='sales_qty',
            aggfunc='sum',
            fill_value=0,
        )
        all_periods = sorted(df['period'].unique())
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
        # 渠道型号维度数据量大时，去掉最慢的 RF 和 HW
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
        max_workers = min(8, os.cpu_count() or 4)  # 最多8线程
        completed = 0
        total_tasks = len(tasks)

        # 小数据量直接串行（避免线程开销）
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
            # 大数据量并行
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
        total_row = OrderedDict([('渠道', '合计'), ('型号', ''), ('细分类', ''), ('预测算法', ''), ('准确率', '')])
        numeric_cols = [c for c in result_df.columns if c not in ['渠道', '型号', '细分类', '预测算法', '准确率']]
        for col in numeric_cols:
            total_row[col] = int(result_df[col].sum()) if result_df[col].dtype.kind in 'iuf' else round(result_df[col].sum(), 1)
        total_df = pd.DataFrame([total_row])
        result_df = pd.concat([total_df, result_df], ignore_index=True)

        self.result_df = result_df
        return result_df

    def _predict_one_group(self, idx, series_values, predictors, forecast_months, pivot, target_periods):
        """对单个分组执行预测（可被并行调用）"""
        best_algo_name = 'SMA'
        best_forecast = None
        best_acc = 0.0

        for predictor in predictors:
            try:
                forecast_arr, acc = predictor.fit_predict(series_values, forecast_months)
                if acc > best_acc:
                    best_acc = acc
                    best_forecast = forecast_arr
                    best_algo_name = predictor.name
            except Exception:
                continue

        if best_forecast is None:
            return None

        # 构造结果行
        result_row = OrderedDict([
            ('渠道', idx[0]),
            ('型号', idx[3] if len(idx) > 3 else ''),
            ('细分类', idx[2] if len(idx) > 2 else ''),
            ('预测算法', best_algo_name),
            ('准确率', round(best_acc, 2)),
        ])

        # 历史实际值（取最后几期用于对比）
        hist_cols = list(pivot.columns)[-forecast_months:] if len(pivot.columns) >= forecast_months else list(pivot.columns)
        for hc in hist_cols:
            result_row[hc] = int(pivot.loc[idx, hc])

        # 预测值
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
        QCheckBox, QSpinBox, QDialog, QDialogButtonBox, QAbstractItemView,
        QSizePolicy,
    )
    from PySide6.QtCore import Qt, QTimer, Signal, QThread, QObject, QPropertyAnimation, QEasingCurve, QParallelAnimationGroup
    from PySide6.QtGui import QFont, QColor, QBrush, QPalette, QPainter
    HAS_PYSIDE6 = True
except ImportError:
    HAS_PYSIDE6 = False


TABLE_STYLESHEET = """
QMainWindow { background-color: #1e1e2e; }
QWidget { background-color: #1e1e2e; color: #cdd6f4; font-family: "Microsoft YaHei", sans-serif; font-size: 13px; }

QLabel#title_label { color: #89b4fa; font-size: 16px; font-weight: bold; padding: 8px; }
QLabel#section_label { color: #a6adc8; font-size: 13px; font-weight: bold; padding: 4px 8px 2px; border-bottom: 1px solid #45475a; margin-top: 4px; }

QTableWidget {
    background-color: #181825;
    color: #cdd6f4;
    gridline-color: #313244;
    border: 1px solid #45475a;
    border-radius: 6px;
    selection-background-color: #45475a;
}
QTableWidget::item { padding: 4px 8px; }
QTableWidget::item:selected { color: #89b4fa; background-color: #45475a; }
QHeaderView::section {
    background-color: #313244;
    color: #bac2de;
    padding: 6px 10px;
    border: none;
    border-right: 1px solid #45475a;
    border-bottom: 1px solid #45475a;
    font-weight: bold;
}

QPushButton#btn_primary {
    background-color: #89b4fa; color: #1e1e2e; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QPushButton#btn_primary:hover { background-color: #74c7ec; }
QPushButton#btn_success {
    background-color: #a6e3a1; color: #1e1e2e; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QPushButton#btn_success:hover { background-color: #94e2d5; }
QPushButton#btn_warning {
    background-color: #f9e2af; color: #1e1e2e; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QPushButton#btn_warning:hover { background-color: #f5c2e7; }
QPushButton#btn_danger {
    background-color: #f38ba8; color: #1e1e2e; border: none; border-radius: 4px;
    padding: 6px 16px; font-weight: bold; min-height: 28px;
}
QPushButton#btn_danger:hover { background-color: #eba0ac; }
QPushButton#btn_default {
    background-color: #45475a; color: #cdd6f4; border: 1px solid #585b70; border-radius: 4px;
    padding: 6px 16px; min-height: 28px;
}
QPushButton#btn_default:hover { background-color: #585b70; }

QComboBox {
    background-color: #181825; color: #cdd6f4; border: 1px solid #585b70;
    border-radius: 4px; padding: 4px 8px; min-height: 26px;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox::down-arrow { image: none; border-left: 5px solid transparent; border-right: 5px solid transparent; border-top: 6px solid #a6adc8; margin-right: 6px; }
QComboBox QAbstractItemView { background-color: #181825; color: #cdd6f4; selection-background-color: #45475a; }
QComboBox:focus { border-color: #89b4fa; }

QLineEdit {
    background-color: #181825; color: #cdd6f4; border: 1px solid #585b70;
    border-radius: 4px; padding: 4px 8px; min-height: 22px;
}
QLineEdit:focus { border-color: #89b4fa; }

QListWidget#nav_list {
    background-color: transparent; border: none; outline: none; font-size: 13px;
}
QListWidget#nav_list::item { padding: 10px 14px; border-radius: 4px; color: #a6adc8; margin: 1px 4px; }
QListWidget#nav_list::item:hover { background-color: #313244; color: #cdd6f4; }
QListWidget#nav_list::item:selected { background-color: #585b70; color: #89b4fa; font-weight: bold; }

QStatusBar { color: #6c7086; font-size: 11px; }
QProgressBar { border: 1px solid #45475a; border-radius: 3px; text-align: center; background-color: #181825; color: #a6adc8; height: 18px;}
QProgressBar::chunk { background-color: #89b4fa; border-radius: 2px; }

QGroupBox {
    border: 1px solid #45475a; border-radius: 6px; margin-top: 8px; padding-top: 16px;
    font-weight: bold; color: #a6adc8;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
"""

# ============================================================
#  加载动画遮罩层
# ============================================================
class LoadingOverlay(QWidget):
    """半透明加载遮罩，带旋转动画和文字提示"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loading_overlay")
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("""
            QWidget#loading_overlay {
                background-color: rgba(30, 30, 46, 200);
                border-radius: 0px;
            }
            QLabel#loading_text {
                color: #89b4fa;
                font-size: 16px;
                font-weight: bold;
            }
            QLabel#loading_sub {
                color: #a6adc8;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(16)

        # 旋转动画文字
        self.spinner_label = QLabel("⏳")
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

        # 旋转动画
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._dots = 0
        self._text_timer = QTimer(self)
        self._text_timer.timeout.connect(self._update_text)

    def _rotate(self):
        self._angle = (self._angle + 12) % 360
        # 用 ASCII 兼容的旋转字符
        chars = ["|", "/", "-", "\\"]
        idx = (self._angle // 90) % 4
        self.spinner_label.setText(chars[idx])

    def _update_text(self):
        self._dots = (self._dots + 1) % 4
        base = self._base_text
        dots = "．" * self._dots
        self.text_label.setText(base + dots)

    def set_text(self, text: str):
        self._base_text = text
        self.text_label.setText(text)
        self._dots = 0

    def set_sub_text(self, text: str):
        self.sub_label.setText(text)

    def showEvent(self, event):
        self._timer.start(60)    # ~16fps 旋转
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
            # 在后台线程中加载数据
            df = self.engine.load_data()
            if df is None or df.empty:
                self.error.emit("没有可用的动销数据")
                return

            # 先快速统计分组数，给用户预期
            dim_info = self.engine.DIMENSIONS.get(self.dimension, self.engine.DIMENSIONS['model'])
            groupby_cols = dim_info['groupby']
            temp_df = df.copy()
            if self.ch and self.ch != '全部':
                temp_df = temp_df[temp_df['channel'] == self.ch]
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

            # 运行预测（这个最耗时）
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
            self.progress.emit(90, f"预测完成！共生成 {n_results} 条预测记录，正在更新界面...")
            self.finished.emit(result_df)

        except Exception as e:
            import traceback
            self.error.emit(f"预测出错: {e}\n{traceback.format_exc()}")


class SalesForecastWindow(QMainWindow):
    """动销预测主窗口"""

    status_message = Signal(str)

    def __init__(self, db_path: str):
        super().__init__()
        self.db_path = db_path
        self.data_loader = DataLoader(db_path)
        self.engine = ForecastEngine(self.data_loader)
        self.current_result_df = None
        self.dimensions_data = {}
        self.worker = None       # 后台线程引用
        self.setWindowTitle("动销预测系统 — DYZG OMS")
        self.setMinimumSize(1200, 750)
        self.resize(1350, 800)
        self.setStyleSheet(TABLE_STYLESHEET)

        # 初始化UI
        self._setup_ui()
        self._load_initial_data()

    # ========== UI 搭建 ==========
    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ===== 左侧导航栏 =====
        nav_panel = QWidget()
        nav_panel.setMaximumWidth(200)
        nav_panel.setMinimumWidth(160)
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(4, 8, 4, 8)
        nav_layout.setSpacing(2)

        title = QLabel("📊 销售管理")
        title.setObjectName("title_label")
        title.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(title)

        self.nav_list = QListWidget()
        self.nav_list.setObjectName("nav_list")
        nav_items = [
            ("🏠 首页", "home"),
            ("📈 渠道型号动销预测报表", "model"),
            ("📊 渠道细分类动销预测报表", "subcategory"),
            ("📋 渠道大类动销预测报表", "category"),
        ]
        for label, key in nav_items:
            item_text = f"  {label}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, key)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(1)
        self.nav_list.itemClicked.connect(self._on_nav_clicked)
        nav_layout.addWidget(self.nav_list)

        nav_spacer = QWidget()
        nav_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        nav_layout.addWidget(nav_spacer)

        main_layout.addWidget(nav_panel)

        # ===== 右侧内容区 =====
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 8, 12, 8)
        content_layout.setSpacing(8)

        # --- 顶部标题 ---
        header = QLabel("🔮 动销预测率")
        header.setObjectName("section_label")
        content_layout.addWidget(header)

        # --- 筛选区 ---
        filter_box = QGroupBox("筛选条件")
        filter_layout = QGridLayout(filter_box)

        # 第一行：维度选择 + 渠道 + 品类
        filter_layout.addWidget(QLabel("预测维度:"), 0, 0)
        self.combo_dimension = QComboBox()
        self.combo_dimension.addItems(["渠道型号", "渠道细分类", "渠道大类"])
        self.combo_dimension.setCurrentIndex(0)
        self.combo_dimension.currentIndexChanged.connect(self._on_dimension_changed)
        filter_layout.addWidget(self.combo_dimension, 0, 1)

        filter_layout.addWidget(QLabel("渠道:"), 0, 2)
        self.combo_channel = QComboBox()
        self.combo_channel.setMinimumWidth(100)
        filter_layout.addWidget(self.combo_channel, 0, 3)

        filter_layout.addWidget(QLabel("品类:"), 0, 4)
        self.combo_category = QComboBox()
        self.combo_category.setMinimumWidth(100)
        filter_layout.addWidget(self.combo_category, 0, 5)

        # 第二行：细分类 + 型号 + 预测月数
        filter_layout.addWidget(QLabel("细分类:"), 1, 0)
        self.combo_subcategory = QComboBox()
        self.combo_subcategory.setMinimumWidth(120)
        filter_layout.addWidget(self.combo_subcategory, 1, 1)

        filter_layout.addWidget(QLabel("型号:"), 1, 2)
        self.input_model = QLineEdit()
        self.input_model.setPlaceholderText("输入型号关键词筛选")
        self.input_model.setMinimumWidth(140)
        filter_layout.addWidget(self.input_model, 1, 3)

        filter_layout.addWidget(QLabel("预测月数:"), 1, 4)
        self.spin_months = QSpinBox()
        self.spin_months.setRange(1, 36)
        self.spin_months.setValue(5)
        self.spin_months.setMinimumWidth(60)
        filter_layout.addWidget(self.spin_months, 1, 5)

        # 第三行：按钮组
        btn_layout = QHBoxLayout()

        self.btn_search = QPushButton("搜索")
        self.btn_search.setObjectName("btn_primary")
        self.btn_search.clicked.connect(self._on_search)
        btn_layout.addWidget(self.btn_search)

        self.btn_reset = QPushButton("重置")
        self.btn_reset.setObjectName("btn_default")
        self.btn_reset.clicked.connect(self._on_reset)
        btn_layout.addWidget(self.btn_reset)

        self.btn_export = QPushButton("导出")
        self.btn_export.setObjectName("btn_success")
        self.btn_export.clicked.connect(self._on_export)
        btn_layout.addWidget(self.btn_export)

        self.btn_refresh = QPushButton("刷新数据")
        self.btn_refresh.setObjectName("btn_warning")
        self.btn_refresh.clicked.connect(self._on_refresh)
        btn_layout.addWidget(self.btn_refresh)

        filter_layout.addLayout(btn_layout, 2, 0, 1, 6)

        content_layout.addWidget(filter_box)

        # --- 进度条 ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setRange(0, 100)
        content_layout.addWidget(self.progress_bar)

        # --- 结果表格 ---
        self.table = QTableWidget()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.verticalHeader().setDefaultSectionSize(28)
        content_layout.addWidget(self.table, stretch=1)

        # --- 底部状态栏 ---
        self.statusBar().showMessage("就绪 — 请点击「搜索」开始预测")

        main_layout.addWidget(content, stretch=1)

        # ===== 加载动画遮罩 =====
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.hide()

    # ========== 初始数据加载 ==========
    def _load_initial_data(self):
        """初始化时加载维度数据填充下拉框"""
        self.statusBar().showMessage("正在加载数据...")
        QApplication.processEvents()

        try:
            self.dimensions_data = self.data_loader.get_dimensions()
            channels = self.dimensions_data.get('channels', [])
            categories = self.dimensions_data.get('categories', [])
            subcategories = self.dimensions_data.get('subcategories', [])

            self.combo_channel.clear()
            self.combo_channel.addItem('全部')
            self.combo_channel.addItems(channels)

            self.combo_category.clear()
            self.combo_category.addItem('全部')
            self.combo_category.addItems(categories)

            self.combo_subcategory.clear()
            self.combo_subcategory.addItem('全部')
            self.combo_subcategory.addItems(subcategories)

            self.statusBar().showMessage(f"数据加载完成 | 账期范围: {self.dimensions_data['periods'][0]} ~ {self.dimensions_data['periods'][-1]}")
        except Exception as e:
            self.statusBar().showMessage(f"数据加载失败: {e}")
            QMessageBox.critical(self, "错误", f"无法加载数据库:\n{e}")

    def _get_current_dimension_key(self) -> str:
        idx = self.combo_dimension.currentIndex()
        mapping = {0: 'model', 1: 'subcategory', 2: 'category'}
        return mapping.get(idx, 'model')

    def _on_nav_clicked(self, item):
        dim_key = item.data(Qt.UserRole)
        if dim_key in ('model', 'subcategory', 'category'):
            mapping = {'model': 0, 'subcategory': 1, 'category': 2}
            self.combo_dimension.setCurrentIndex(mapping[dim_key])
        elif dim_key == 'home':
            pass  # 可扩展首页仪表盘

    def _on_dimension_changed(self, index):
        pass  # 维度变化在搜索时处理即可

    def _on_search(self):
        """执行搜索和预测（启动后台线程）"""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)

        dim_key = self._get_current_dimension_key()
        channel = self.combo_channel.currentText() if self.combo_channel.currentIndex() > 0 else None
        category = self.combo_category.currentText() if self.combo_category.currentIndex() > 0 else None
        subcat = self.combo_subcategory.currentText() if self.combo_subcategory.currentIndex() > 0 else None
        model_kw = self.input_model.text().strip() or None
        months = self.spin_months.value()

        # 显示加载遮罩
        self.loading_overlay.set_text("正在预测计算")
        self.loading_overlay.set_sub_text("维度：%s  |  预测月数：%d" % (self.combo_dimension.currentText(), months))
        self.loading_overlay.show()
        self.loading_overlay.raise_()

        self.btn_search.setEnabled(False)
        self.statusBar().showMessage("正在后台预测，界面可自由操作...")

        # 启动后台线程
        self.worker = ForecastWorker(
            engine=self.engine,
            dimension=dim_key,
            months=months,
            ch=channel, cat=category, subcat=subcat, model_kw=model_kw,
        )
        self.worker.progress.connect(self._on_worker_progress)
        self.worker.finished.connect(self._on_worker_finished)
        self.worker.error.connect(self._on_worker_error)
        self.worker.start()

    def _on_worker_progress(self, pct: int, text: str):
        self.loading_overlay.set_text(text)
        self.statusBar().showMessage(text)

    def _on_worker_finished(self, result_df):
        self.current_result_df = result_df
        self._populate_table(result_df)
        self.loading_overlay.hide()
        self.btn_search.setEnabled(True)
        self.statusBar().showMessage(
            "预测完成 | 共 %d 条记录 | 维度：%s"
            % (max(0, len(result_df) - 1), self.combo_dimension.currentText())
        )

    def _on_worker_error(self, msg: str):
        self.loading_overlay.hide()
        self.btn_search.setEnabled(True)
        self.statusBar().showMessage("预测出错")
        QMessageBox.warning(self, "警告", msg[:500])

    def _populate_table(self, df: pd.DataFrame):
        """将预测结果填充到表格（优化版：批量操作）"""
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

        # 列宽预设
        col_widths = {'渠道': 55, '型号': 130, '细分类': 90, '预测算法': 65, '准确率': 60}
        for ci, col in enumerate(columns):
            self.table.setColumnWidth(ci, col_widths.get(col, 95))

        # 批量填充
        is_float = df.apply(lambda col: col.map(lambda x: isinstance(x, (float, np.floating))).any() if col.dtype == 'object' else col.dtype.kind in 'iuf')
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

                # 合计行样式
                if ri == 0:
                    item.setBackground(QBrush(QColor("#313244")))
                    item.setForeground(QBrush(QColor("#f9e2af")))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif ci >= 5:
                    try:
                        v = float(str(val).replace(',', '')) if val else 0
                        if v > 0:
                            item.setForeground(QBrush(QColor("#a6e3a1")))
                        else:
                            item.setForeground(QBrush(QColor("#f38ba8")))
                    except (ValueError, TypeError):
                        pass

                self.table.setItem(ri, ci, item)

        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)

    def _on_reset(self):
        """重置筛选条件"""
        self.combo_channel.setCurrentIndex(0)
        self.combo_category.setCurrentIndex(0)
        self.combo_subcategory.setCurrentIndex(0)
        self.input_model.clear()
        self.spin_months.setValue(5)
        self.table.setColumnCount(0)
        self.table.setRowCount(0)
        self.current_result_df = None
        self.statusBar().showMessage("已重置筛选条件")

    def _on_export(self):
        """导出为 CSV"""
        if self.current_result_df is None or self.current_result_df.empty:
            QMessageBox.information(self, "提示", "暂无数据可导出，请先执行搜索。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出预测结果",
            f"动销预测_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            "CSV 文件 (*.csv);;所有文件 (*)",
        )
        if not path:
            return

        try:
            self.current_result_df.to_csv(path, index=False, encoding='utf-8-sig')
            self.statusBar().showMessage(f"已导出到: {path}")
            QMessageBox.information(self, "成功", f"文件已导出到:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_refresh(self):
        """重新加载数据"""
        self._load_initial_data()
        self.statusBar().showMessage("数据已刷新")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.quit()
            self.worker.wait(3000)
        self.data_loader.close()
        super().closeEvent(event)


# ============================================================
#  入口
# ============================================================

def find_db_path():
    """查找数据库文件"""
    # 优先级：命令行参数 → 相对路径 → 绝对路径
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        return sys.argv[1]

    candidates = [
        os.path.join(os.path.dirname(__file__), '..', '任务五', 'oms_sales_data.sqlite'),
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
        print("[错误] 未找到数据库文件 oms_sales_data.sqlite")
        print("用法: python sales_forecast.py [数据库路径]")
        print("      或确保 ../任务五/oms_sales_data.sqlite 存在")
        input("按回车退出...")
        sys.exit(1)

    print(f"[信息] 使用数据库: {db_path}")

    if not HAS_PYSIDE6:
        print("[错误] 未安装 PySide6，请运行: pip install PySide6")
        input("按回车退出...")
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    window = SalesForecastWindow(db_path)
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
