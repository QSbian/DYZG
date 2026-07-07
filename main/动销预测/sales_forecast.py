#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
动销预测系统 (Sales Forecast System) v3
========================================
基于 OMS 销售数据库的动销预测工具
用法：python sales_forecast.py [数据库路径]
默认数据库路径：../任务五/oms_sales_data.sqlite
"""

import os
import sys
import re
import sqlite3
import json
import warnings
from datetime import datetime
from collections import OrderedDict
from typing import List, Tuple, Dict, Optional


# ============================================================
#  全局常量
# ============================================================
VIEW_NAME = 'vw_product_sales_customer_3'           # 数据视图名
UNKNOWN_CATEGORY = '未分类'
UNKNOWN_MODEL = '未命名'
ONLINE_CHANNELS = ('线上', '官方商城', '电商', '电商&双品牌经营部',
                   '电商-双品牌经营部', '天猫', '京东', '苏宁')
CHANNEL_ONLINE = '线上'
CHANNEL_OFFLINE = '线下'
CHANNEL_ALL = '线上和线下'
DB_CONN_TIMEOUT = 30.0  # SQLite 连接超时（秒）
YEAR_RANGE_FUTURE_MARGIN = 5  # 年份选择上限在后推几年
MAX_TABLE_ROWS = 500  # 表格 UI 最大渲染行数（QTableWidget 承载太多 QLabel 会卡死）
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

# Windows 终端 UTF-8 修复
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

warnings.filterwarnings('ignore', category=FutureWarning)

# 历史记录文件路径（与程序同目录）
HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.model_history.json')
DB_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.db_history.json')
APP_SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.app_settings.json')

# 默认应用设置
DEFAULT_SETTINGS = {
    "enabled_algorithms": ["Naive", "SMA", "Median", "HW", "Croston", "SARIMA", "XGBoost", "LightGBM"],
    "visible_elements": {
        "channel": True, "category": True, "subcategory": True, "model": True,
        "time_range": True, "db_bar": True,
    },
    "export_default_dir": "",
    "default_dimension": "model",
    # 运行优化开关（默认全部启用）
    "forecast_range_limit": True,   # 预测时间限制 24 个月
    "auto_downgrade": True,         # >50 组自动关闭重型算法
    "table_row_limit": True,        # 展示上限 500 行
}


def load_app_settings() -> dict:
    """从文件加载应用设置，缺失的键用默认值补全"""
    settings = dict(DEFAULT_SETTINGS)
    try:
        if os.path.exists(APP_SETTINGS_FILE):
            with open(APP_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    for k, v in DEFAULT_SETTINGS.items():
                        if k in saved:
                            settings[k] = saved[k]
    except Exception:
        pass
    return settings


def save_app_settings(settings: dict):
    """保存应用设置到文件"""
    try:
        with open(APP_SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
#  数据库路径管理
# ============================================================

class DbPathManager:
    """管理历史数据库路径，支持多路径保存和快速切换"""

    def __init__(self, history_file: str = DB_HISTORY_FILE):
        self.history_file = history_file
        self._paths: List[str] = self._load()

    def _load(self) -> List[str]:
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [p for p in data if isinstance(p, str) and os.path.exists(p)]
        except Exception:
            pass
        return []

    def _save(self):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self._paths, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def add(self, path: str):
        path = os.path.abspath(path)
        if path in self._paths:
            self._paths.remove(path)
        self._paths.insert(0, path)
        # 最多保留 10 条
        self._paths = self._paths[:10]
        self._save()

    def get_all(self) -> List[str]:
        return list(self._paths)

    def get_default(self) -> Optional[str]:
        return self._paths[0] if self._paths else None

    def remove(self, path: str):
        """从历史列表中移除指定路径"""
        path = os.path.abspath(path)
        if path in self._paths:
            self._paths.remove(path)
            self._save()

    def list_paths(self) -> List[str]:
        """返回所有路径（包括已不存在的，用于管理界面展示）"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return [p for p in data if isinstance(p, str)]
        except Exception:
            pass
        return list(self._paths)


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

    @staticmethod
    def _normalize_channel(channel: str) -> str:
        """根据数据库报告将渠道归一化为线上/线下两类"""
        if not isinstance(channel, str):
            return CHANNEL_OFFLINE
        ch = channel.strip()
        if ch in ONLINE_CHANNELS:
            return CHANNEL_ONLINE
        return CHANNEL_OFFLINE

    @staticmethod
    def _clean_text(value, default: str = UNKNOWN_CATEGORY) -> str:
        """清理文本字段：空值/NULL/纯空白替换为默认值"""
        if pd.isna(value):
            return default
        s = str(value).strip()
        return s if s else default

    @staticmethod
    def _map_category_group(category: str) -> str:
        """根据业务参考图，将原始品类归并为大类"""
        if not isinstance(category, str):
            return '其他'
        cat = category.strip()
        if cat in ('家用电', '佳尼特电'):
            return '电'
        if cat in ('家用气', '佳尼特气'):
            return '气'
        if cat in ('净水', '佳尼特净水', '净水滤芯', '空净', '空净滤芯'):
            return '净'
        if cat in ('壁挂炉', '壁挂炉材料'):
            return '壁挂炉'
        if cat == '热泵':
            return '热泵'
        if cat in ('烟灶', '洗碗机', '蒸烤箱'):
            return '厨'
        if cat in ('冷暖风水', '水暖毯', 'AI-LiNK智能件', '五金卫浴'):
            return '生态'
        if cat == '生态':
            return '生态'
        if cat in ('其他', '商用', UNKNOWN_CATEGORY):
            return '其他'
        return '其他'

    def load_raw_data(self) -> pd.DataFrame:
        """读取 {VIEW_NAME} 视图的动销数据，并进行清洗"""
        conn = self._new_conn()
        try:
            query = f"""
                SELECT 账期 AS period,
                       渠道 AS channel,
                       品类 AS category,
                       细分类 AS subcategory,
                       型号 AS model,
                       动销 AS sales_qty
                FROM {VIEW_NAME}
                WHERE 动销 IS NOT NULL AND 动销 > 0
                ORDER BY 账期
            """
            df = pd.read_sql_query(query, conn)
            df['sales_qty'] = pd.to_numeric(df['sales_qty'], errors='coerce').fillna(0)

            # 数据清洗：渠道归一化、空文本填充、大类归并
            df['channel'] = df['channel'].apply(self._normalize_channel)
            df['category'] = df['category'].apply(lambda x: self._clean_text(x, UNKNOWN_CATEGORY))
            df['subcategory'] = df['subcategory'].apply(lambda x: self._clean_text(x, UNKNOWN_CATEGORY))
            df['model'] = df['model'].apply(lambda x: self._clean_text(x, UNKNOWN_MODEL))
            df['大类'] = df['category'].apply(self._map_category_group)

            return df
        finally:
            conn.close()

    def get_dimensions(self) -> Dict[str, list]:
        """获取各筛选维度的唯一值（基于数据库报告做渠道归一化与空值填充）"""
        conn = self._new_conn()
        try:
            cur = conn.cursor()

            cur.execute(f"""
                SELECT DISTINCT
                    CASE
                        WHEN 渠道 IN {ONLINE_CHANNELS} THEN '{CHANNEL_ONLINE}'
                        ELSE '{CHANNEL_OFFLINE}'
                    END AS ch
                FROM {VIEW_NAME}
                WHERE 渠道 IS NOT NULL AND 渠道 != ''
                ORDER BY ch
            """)
            channels = [r[0] for r in cur.fetchall()]

            cur.execute(f"""
                SELECT DISTINCT COALESCE(NULLIF(TRIM(品类), ''), '{UNKNOWN_CATEGORY}') AS cat
                FROM {VIEW_NAME}
                ORDER BY cat
            """)
            categories = [r[0] for r in cur.fetchall()]

            # 大类由原始品类映射得到
            category_groups = sorted(set(self._map_category_group(c) for c in categories))

            cur.execute(f"""
                SELECT DISTINCT COALESCE(NULLIF(TRIM(细分类), ''), '{UNKNOWN_CATEGORY}') AS sub
                FROM {VIEW_NAME}
                ORDER BY sub
            """)
            subcategories = [r[0] for r in cur.fetchall()]

            cur.execute(f"""
                SELECT DISTINCT COALESCE(NULLIF(TRIM(型号), ''), '{UNKNOWN_MODEL}') AS m
                FROM {VIEW_NAME}
                ORDER BY m
            """)
            models = [r[0] for r in cur.fetchall()]

            cur.execute(f"SELECT DISTINCT 账期 FROM {VIEW_NAME} WHERE 账期 IS NOT NULL ORDER BY 账期")
            periods = [r[0] for r in cur.fetchall()]

            return {
                'channels': channels,
                'categories': categories,
                'category_groups': category_groups,
                'subcategories': subcategories,
                'models': models,
                'periods': periods,
            }
        finally:
            conn.close()

    def get_subcategories_by_category(self, category: str) -> List[str]:
        """根据品类获取其下的细分类列表（用于品类-细分类联动）"""
        if category in (None, '', '全部'):
            dims = self.get_dimensions()
            return dims['subcategories']
        conn = self._new_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT DISTINCT COALESCE(NULLIF(TRIM(细分类), ''), '{UNKNOWN_CATEGORY}') AS sub
                FROM {VIEW_NAME}
                WHERE COALESCE(NULLIF(TRIM(品类), ''), '{UNKNOWN_CATEGORY}') = ?
                ORDER BY sub
            """, (category,))
            return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def get_years(self) -> List[int]:
        """获取数据库中存在的年份列表"""
        conn = self._new_conn()
        try:
            cur = conn.cursor()
            cur.execute(f"""
                SELECT DISTINCT SUBSTR(账期, 1, 4) AS yr
                FROM {VIEW_NAME}
                WHERE 账期 IS NOT NULL
                ORDER BY yr
            """)
            return [int(r[0]) for r in cur.fetchall()]
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


# ---------- SARIMA 季节自回归预测算法 ----------
class SARIMAPredictor(BasePredictor):
    """SARIMA —— 经典季节时间序列模型，适合有趋势和季节规律的数据"""
    name = "SARIMA"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        from statsmodels.tsa.arima.model import ARIMA
        s = series.astype(float).flatten()
        n = len(s)
        if n < 12:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        try:
            # 自动判断差分阶数 d
            d = 0
            try:
                from statsmodels.tsa.stattools import adfuller
                adf_p = adfuller(s[s > 0] if np.any(s > 0) else s,
                                 maxlag=min(12, n // 2))[1]
                if adf_p > 0.05:
                    d = 1
            except Exception:
                d = 1

            # 数据 >= 24 月且非零比例 > 20% 时启用季节模式
            if n >= 24 and np.sum(s > 0) / n > 0.2:
                from statsmodels.tsa.statespace.sarimax import SARIMAX
                model = SARIMAX(
                    s, order=(1, d, 1),
                    seasonal_order=(0, 1, 1, 12),
                    enforce_stationarity=False, enforce_invertibility=False,
                )
            else:
                model = ARIMA(s, order=(2, d, 2))

            fitted = model.fit(method_kwargs={'maxiter': 20}, disp=False)
            forecast = fitted.forecast(steps=forecast_horizon)
            forecast = np.maximum(forecast, 0)
            return forecast.astype(float), 85.0
        except Exception:
            return NaivePredictor().fit_predict(series, forecast_horizon)


# ---------- XGBoost 梯度提升预测算法 ----------
class XGBoostPredictor(BasePredictor):
    """XGBoost —— 梯度提升树，通过滞后特征学习时间依赖"""
    name = "XGBoost"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        try:
            import xgboost as xgb
        except ImportError:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        s = series.astype(float).flatten()
        n = len(s)
        if n < 12:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        lag = min(12, n // 3, n - forecast_horizon)
        if lag < 2:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        X, y = [], []
        for i in range(lag, n):
            X.append(s[i - lag:i])
            y.append(s[i])
        X, y = np.array(X), np.array(y)

        if len(X) == 0:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        try:
            model = xgb.XGBRegressor(
                n_estimators=50, max_depth=3, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8,
                verbosity=0, random_state=42,
            )
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='X does not have valid feature names')
                model.fit(X, y)

            last_window = s[-lag:].copy()
            predictions = []
            for _ in range(forecast_horizon):
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='X does not have valid feature names')
                    pred = model.predict(last_window.reshape(1, -1))[0]
                pred = max(0, pred)
                predictions.append(pred)
                last_window = np.roll(last_window, -1)
                last_window[-1] = pred

            return np.array(predictions), 85.0
        except Exception:
            return NaivePredictor().fit_predict(series, forecast_horizon)


# ---------- LightGBM 梯度提升预测算法 ----------
class LightGBMPredictor(BasePredictor):
    """LightGBM —— 高效梯度提升，比 XGBoost 更快，适合大量分组场景"""
    name = "LightGBM"

    def fit_predict(self, series: np.ndarray, forecast_horizon: int = 5) -> Tuple[np.ndarray, float]:
        try:
            import lightgbm as lgb
        except ImportError:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        s = series.astype(float).flatten()
        n = len(s)
        if n < 12:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        lag = min(12, n // 3, n - forecast_horizon)
        if lag < 2:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        X, y = [], []
        for i in range(lag, n):
            X.append(s[i - lag:i])
            y.append(s[i])
        X, y = np.array(X), np.array(y)

        if len(X) == 0:
            return NaivePredictor().fit_predict(series, forecast_horizon)

        try:
            model = lgb.LGBMRegressor(
                n_estimators=50, max_depth=4, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8,
                verbosity=-1, random_state=42, force_col_wise=True,
            )
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='X does not have valid feature names')
                model.fit(X, y)

            last_window = s[-lag:].copy()
            predictions = []
            for _ in range(forecast_horizon):
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='X does not have valid feature names')
                    pred = model.predict(last_window.reshape(1, -1))[0]
                pred = max(0, pred)
                predictions.append(pred)
                last_window = np.roll(last_window, -1)
                last_window[-1] = pred

            return np.array(predictions), 85.0
        except Exception:
            return NaivePredictor().fit_predict(series, forecast_horizon)


# ============================================================
#  预测引擎模块（算法调度 + 回溯验证 + 结果生成）
# ============================================================

class ForecastEngine:
    """预测引擎：协调数据聚合、算法选择、结果输出"""

    PREDICTORS = [
        SARIMAPredictor(),
        XGBoostPredictor(),
        LightGBMPredictor(),
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
        """计算 sMAPE（对称平均百分比误差），返回值 ∈ [0, 200]
        
        对实际值 < 1.0 的点排除误差计算——微小动销本质上是噪声，
        0.1的误差在sMAPE中会被放大到200%，掩盖真实预测能力。
        """
        actual = np.array(actual, dtype=float).flatten()
        predicted = np.array(predicted, dtype=float).flatten()
        # 排除实际值 < 1 的点（微小噪声不参与评估）
        mask = np.abs(actual) >= 1.0
        if not mask.any():
            return 0.0  # 全为噪声，不评价
        actual = actual[mask]
        predicted = predicted[mask]
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
            'groupby': ['channel', 'model', 'category', 'subcategory'],
            'label': '渠道型号',
        },
        'subcategory': {
            'groupby': ['channel', 'category', 'subcategory'],
            'label': '渠道细分类',
        },
        'category': {
            'groupby': ['channel', '大类'],
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

    @staticmethod
    def _filter_channel(df: pd.DataFrame, channel: Optional[str]) -> pd.DataFrame:
        """按渠道筛选 DataFrame，提取为共享方法避免三处重复"""
        if not channel:
            return df
        if channel == CHANNEL_ONLINE:
            return df[df['channel'] == CHANNEL_ONLINE]
        if channel == CHANNEL_OFFLINE:
            return df[df['channel'] == CHANNEL_OFFLINE]
        return df

    def run_forecast(
        self,
        dimension: str = 'model',
        forecast_months: int = 5,
        start_period: Optional[str] = None,
        end_period: Optional[str] = None,
        filter_channel: Optional[str] = None,
        filter_category: Optional[str] = None,
        filter_subcategory: Optional[str] = None,
        filter_model: Optional[str] = None,
        algorithm_filter: Optional[List[str]] = None,
        auto_downgrade: bool = True,
    ) -> pd.DataFrame:
        if self.raw_df is None:
            self.load_data()

        df = self.raw_df.copy()
        dim_info = self.DIMENSIONS.get(dimension, self.DIMENSIONS['model'])
        groupby_cols = dim_info['groupby']

        # ---- 应用筛选 ----
        df = self._filter_channel(df, filter_channel)

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

        # 如果用户指定了目标区间，则按用户选择；否则按原有逻辑（最后账期 + N 月）
        if start_period and end_period:
            target_periods = self._generate_periods_range(start_period, end_period)
            forecast_months = len(target_periods)
            # 训练截止到目标起始月的前一月
            pre_start = self._period_add(start_period, -1)
        else:
            target_periods = self._generate_future_periods(latest_period, forecast_months)
            pre_start = latest_period

        # 找到 pre_start 在时间序列中的位置，作为训练数据截断点
        pivot_cols = list(pivot.columns)
        try:
            training_cutoff_idx = pivot_cols.index(pre_start) + 1  # +1 转为切片右边界
        except ValueError:
            # pre_start 不在数据中（可能超出范围），取最接近的位置
            if pre_start < pivot_cols[0]:
                training_cutoff_idx = 0
            else:
                training_cutoff_idx = len(pivot_cols)

        # ---- 智能选择算法（根据数据量 + 维度） ----
        n_groups = len(pivot)
        predictors = list(self.PREDICTORS)
        if algorithm_filter:
            predictors = [p for p in predictors if p.name in algorithm_filter]
        # 型号维度：组数多且单组数据少，重型算法性价比极低
        if auto_downgrade and dimension == 'model' and n_groups > 50 and not algorithm_filter:
            predictors = [p for p in predictors if p.name not in
                          ('RF', 'HW', 'SARIMA', 'XGBoost', 'LightGBM')]
        elif auto_downgrade and dimension == 'subcategory' and n_groups > 50 and not algorithm_filter:
            predictors = [p for p in predictors if p.name not in ('RF', 'SARIMA')]

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
        total_tasks = len(tasks)

        if total_tasks <= 30 or max_workers <= 1:
            for i, (idx, series_values) in enumerate(tasks):
                result = self._predict_one_group(
                    idx, series_values, predictors, forecast_months,
                    pivot, target_periods, groupby_cols, training_cutoff_idx
                )
                if result is not None:
                    results.append(result)
        else:
            _cutoff = training_cutoff_idx
            def _task_fn(args):
                idx, sv = args
                return self._predict_one_group(
                    idx, sv, predictors, forecast_months,
                    pivot, target_periods, groupby_cols, _cutoff
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

        if not results:
            return pd.DataFrame()

        result_df = pd.DataFrame(results)

        # 添加合计行（处理 (hist_val, pred_val, accuracy) 元组列）
        numeric_pattern = re.compile(r'^\d')
        string_cols = [c for c in result_df.columns if not numeric_pattern.match(str(c))]
        total_row = OrderedDict((c, '') for c in string_cols)
        for col in result_df.columns:
            if numeric_pattern.match(str(col)):
                hist_sum = 0
                pred_sum = 0
                has_pred = False
                for v in result_df[col]:
                    if isinstance(v, tuple):
                        if v[0] is not None:
                            hist_sum += float(v[0])
                        if v[1] is not None:
                            pred_sum += float(v[1])
                            has_pred = True
                    elif v is not None:
                        try:
                            hist_sum += float(v)
                        except (ValueError, TypeError):
                            pass
                if has_pred:
                    h = int(hist_sum) if hist_sum > 0 else None
                    p = int(pred_sum)
                    # 合计行准确率：用汇总后的 hist/pred 计算
                    acc = None
                    if hist_sum > 0:
                        acc = round(max(0, 1 - abs(pred_sum - hist_sum) / hist_sum) * 100, 2)
                    total_row[col] = (h, p, acc) if hist_sum > 0 else (None, p, None)
                else:
                    total_row[col] = int(hist_sum)
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
        df = self._filter_channel(df, filter_channel)

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

    def _predict_one_group(self, idx, series_values, predictors, forecast_months,
                           pivot, target_periods, groupby_cols, training_cutoff_idx):
        """对单个分组执行预测（用回溯验证选最优算法）

        数据状态评估（综合数据量与回溯准确率）：
        - 良好：训练 ≥ 12 月且非零 ≥ 6 个，回溯准确率 ≥ 70%
        - 一般：回溯准确率 ≥ 60%
        - 偏低：回溯准确率 30%~59%
        - 质量差：回溯准确率 < 30%
        - 不足：训练 < 6 月或非零 < 3 个
        - 严重不足：训练 < 3 月或无销量记录

        training_cutoff_idx: 训练数据截止位置（切片右边界），即目标起始月前一个月的索引+1
        """
        # 只用训练截止前数据做评估和训练
        training_data = series_values[:training_cutoff_idx] if training_cutoff_idx > 0 else series_values[:3]
        n_total = len(training_data)
        n_nonzero = int(np.sum(training_data > 0))
        forecast_months = len(target_periods)

        # ---- 数据状态评估（基于训练数据 + 回溯验证准确率） ----
        # 第一步：数据量是否足以回溯验证
        if n_total < 3 or n_nonzero == 0:
            data_status = '严重不足'
            best_acc = None
            best_predictor = predictors[0] if predictors else NaivePredictor()
            best_smape = 80.0
        elif n_total < 6 or n_nonzero < 3:
            data_status = '不足'
            best_acc = None
            best_predictor = predictors[0] if predictors else NaivePredictor()
            best_smape = 80.0
        else:
            # 第二步：执行回溯验证，根据准确率评定质量
            test_h = max(2, min(4, n_total // 4))
            test_h = min(test_h, forecast_months)

            best_smape = float('inf')
            best_predictor = None

            for predictor in predictors:
                try:
                    smape_val = ForecastEngine.backtest(predictor, training_data, test_h)
                    if smape_val < best_smape:
                        best_smape = smape_val
                        best_predictor = predictor
                except Exception:
                    continue

            if best_predictor is None:
                best_predictor = predictors[0] if predictors else NaivePredictor()
                best_smape = 50.0

            # 第三步：综合评定——准确率优先，兼顾数据量
            accuracy = max(0.0, min(100.0, 100.0 - best_smape))
            if n_total >= 12 and n_nonzero >= 6 and accuracy >= 70:
                data_status = '良好'
            elif accuracy >= 60:
                data_status = '一般'
            elif accuracy >= 30:
                data_status = '偏低'
            else:
                data_status = '质量差'
        try:
            best_forecast, _ = best_predictor.fit_predict(training_data, forecast_months)
        except Exception:
            best_forecast = np.zeros(forecast_months)

        best_algo_name = best_predictor.name
        if data_status in ('严重不足', '不足'):
            best_acc = None
        else:
            best_acc = max(0.0, min(100.0, 100.0 - best_smape))

        # ---- 第三步：构建结果行（目标期在前，历史期在后）----
        col_display_map = {
            'channel': '渠道',
            'category': '品类',
            'subcategory': '细分类',
            'model': '型号',
            '大类': '大类',
        }
        result_row = OrderedDict()
        for col_name, val in zip(groupby_cols, idx):
            display_name = col_display_map.get(col_name, col_name)
            result_row[display_name] = val
        result_row['预测算法'] = best_algo_name
        result_row['准确率'] = round(best_acc, 2) if best_acc is not None else '数据不足'
        result_row['数据状态'] = data_status

        pivot_cols = list(pivot.columns)

        # ---- 第四步：目标期内拆分「纯未来」与「可回测历史」 ----
        # 纯未来：目标期中 DB 没有记录的月份 → 只用预测值（绿色）
        # 可回测：目标期中 DB 有记录的月份 → 历史实际 + 算法回推对比
        future_targets = [p for p in target_periods if p not in pivot.columns]
        hist_targets  = [p for p in target_periods if p in pivot.columns]

        # 对可回测的历史期做逐期 1-step 回测
        hist_preds = {}
        for period in hist_targets:
            col_idx = pivot_cols.index(period)
            if col_idx >= 3:
                try:
                    hpred, _ = best_predictor.fit_predict(series_values[:col_idx], 1)
                    hist_preds[period] = round(float(hpred[0]), 1)
                except Exception:
                    pass

        # 展示顺序：纯未来预测在前 → 可回测历史在后
        all_periods = future_targets + hist_targets
        for period in all_periods:
            hist_val = int(pivot.loc[idx, period]) if period in pivot.columns else None

            if period in future_targets:
                # 纯未来：多步预测值
                pi = target_periods.index(period)
                pred_val = round(float(best_forecast[pi]), 1)
            elif period in hist_targets:
                # 可回测历史：1-step 回测值
                pi = target_periods.index(period)
                pred_val = hist_preds.get(period, round(float(best_forecast[pi]), 1))
            else:
                pred_val = None

            # 计算该时期的准确率: 1 - |pred-actual|/actual
            accuracy = None
            if hist_val is not None and pred_val is not None and hist_val > 0:
                error_rate = abs(pred_val - hist_val) / hist_val
                accuracy = round(max(0, 1 - error_rate) * 100, 2)

            if hist_val is not None and pred_val is not None:
                result_row[period] = (hist_val, pred_val, accuracy)
            elif pred_val is not None:
                result_row[period] = (None, pred_val, None)
            else:
                result_row[period] = (hist_val, None, None)

        return result_row

    @staticmethod
    def _generate_future_periods(last_period: str, n: int) -> List[str]:
        """根据最后一个账期生成未来 n 个账期"""
        return ForecastEngine._period_add(last_period, n, as_list=True)

    @staticmethod
    def _period_add(period: str, n: int, as_list: bool = False) -> List[str]:
        """给账期加减 n 个月；as_list=True 返回 [period+1, ..., period+n]"""
        year = int(period[:4])
        month = int(period[4:])
        if as_list:
            periods = []
            for _ in range(n):
                month += 1
                if month > 12:
                    month = 1
                    year += 1
                periods.append(f"{year}{month:02d}")
            return periods
        # 单个偏移（正数后推、负数前推）
        month += n
        while month > 12:
            month -= 12
            year += 1
        while month < 1:
            month += 12
            year -= 1
        return f"{year}{month:02d}"

    @staticmethod
    def _generate_periods_range(start_period: str, end_period: str) -> List[str]:
        """生成 start 到 end(含) 的所有账期"""
        period = start_period
        periods = [period]
        # 安全上限，防止无限循环
        max_periods = 120
        while period != end_period and len(periods) < max_periods:
            period = ForecastEngine._period_add(period, 1)
            periods.append(period)
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
        QSizePolicy, QButtonGroup, QRadioButton, QCompleter, QScrollArea, QStackedWidget,
        QTableView, QAbstractItemView, QStyledItemDelegate, QStyleOptionViewItem,
        QStyle,
    )
    from PySide6.QtCore import (
        Qt, QTimer, Signal, QThread, QObject, QPropertyAnimation, QEasingCurve, QEvent,
        QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QSize,
    )
    from PySide6.QtGui import (
        QFont, QColor, QBrush, QPalette, QPainter,
        QTextDocument, QAbstractTextDocumentLayout,
    )
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

/* ---- 左侧导航栏 - 帮助及常见问题 ---- */
QWidget#nav_panel QGroupBox#help_section {
    border: 1px solid #313244; border-radius: 6px;
    margin-top: 8px; padding-top: 14px;
    color: #a6adc8; font-size: 11px; font-weight: bold;
}
QWidget#nav_panel QGroupBox#help_section::title {
    subcontrol-origin: margin; left: 8px; padding: 0 4px;
    color: #89b4fa;
}
QListWidget#faq_list {
    background-color: transparent; border: none; outline: none;
    font-size: 11px; color: #a6adc8;
}
QListWidget#faq_list::item {
    padding: 5px 8px; border-radius: 4px; color: #a6adc8;
    margin: 1px 2px;
}
QListWidget#faq_list::item:hover {
    background-color: #313244; color: #cdd6f4;
}
QListWidget#faq_list::item:selected {
    background-color: #45475a; color: #89b4fa;
}
QLineEdit#faq_search {
    background-color: #1e1e2e; color: #cdd6f4; border: 1px solid #313244;
    border-radius: 4px; padding: 5px 8px; font-size: 11px;
}
QLineEdit#faq_search:focus {
    border: 1px solid #89b4fa;
}
QLineEdit#faq_search::placeholder {
    color: #6c7086;
}

/* ---- 右侧内容区（浅色主题） ---- */
QWidget#content_widget { background-color: #f8f9fa; }
QWidget#content_stack > QWidget { background-color: #f8f9fa; }
QWidget#content_widget QLabel { color: #333333; }
QWidget#content_widget QLabel#dim_title { color: #5e4b8b; font-size: 18px; font-weight: bold; padding: 8px 12px; }

/* 内容区 - 表格 */
QWidget#content_widget QTableWidget,
QWidget#content_widget QTableView {
    background-color: #ffffff;
    color: #333333;
    gridline-color: #e8e8e8;
    border: 1px solid #cccccc;
    border-radius: 6px;
    selection-background-color: #e7f1ff;
    selection-color: #0a47a3;
}
QWidget#content_widget QTableWidget::item { padding: 4px 8px; }
QWidget#content_widget QTableWidget::item:selected { color: #0a47a3; background-color: #e7f1ff; }
QWidget#content_widget QTableView::item { padding: 4px 8px; }
QWidget#content_widget QTableView::item:selected { color: #0a47a3; background-color: #e7f1ff; }
QWidget#content_widget QHeaderView::section {
    background-color: #f1f3f5;
    color: #495057;
    padding: 6px 10px;
    border: none;
    border-right: 1px solid #dee2e6;
    border-bottom: 1px solid #ced4da;
    font-weight: bold;
}
QWidget#content_widget QHeaderView::section:vertical {
    background-color: #f8f9fa;
    color: #868e96;
    border-right: 1px solid #dee2e6;
    border-bottom: 1px solid #e9ecef;
    font-weight: normal;
}
/* 表格左上角交叉区域 */
QWidget#content_widget QTableWidget::corner {
    background-color: #e9ecef;
    border-right: 1px solid #dee2e6;
    border-bottom: 1px solid #ced4da;
}

/* 内容区 - 滚动条（改为蓝色系，不再是灰色） */
QWidget#content_widget QScrollBar:vertical {
    background-color: #e7f1ff;
    width: 10px;
    border-radius: 5px;
}
QWidget#content_widget QScrollBar::handle:vertical {
    background-color: #74b0ff;
    border-radius: 5px;
    min-height: 30px;
}
QWidget#content_widget QScrollBar::handle:vertical:hover {
    background-color: #4a9eff;
}
QWidget#content_widget QScrollBar::add-line:vertical,
QWidget#content_widget QScrollBar::sub-line:vertical {
    height: 0px;
}
QWidget#content_widget QScrollBar::add-page:vertical,
QWidget#content_widget QScrollBar::sub-page:vertical {
    background: none;
}
QWidget#content_widget QScrollBar:horizontal {
    background-color: #e7f1ff;
    height: 20px;
    border-radius: 5px;
}
QWidget#content_widget QScrollBar::handle:horizontal {
    background-color: #74b0ff;
    border-radius: 5px;
    min-width: 60px;
}
QWidget#content_widget QScrollBar::handle:horizontal:hover {
    background-color: #4a9eff;
}
QWidget#content_widget QScrollBar::add-line:horizontal,
QWidget#content_widget QScrollBar::sub-line:horizontal {
    width: 0px;
}
QWidget#content_widget QScrollBar::add-page:horizontal,
QWidget#content_widget QScrollBar::sub-page:horizontal {
    background: none;
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
QWidget#content_widget {
    background-color: #f5f5f7;
}
QWidget#content_widget QGroupBox {
    border: 1px solid #dee2e6; border-radius: 6px; margin-top: 8px; padding-top: 16px;
    font-weight: bold; color: #495057;
    background-color: #ffffff;
}
QWidget#content_widget QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }

/* ---- 全局 QStatusBar ---- */
QMainWindow::statusBar { background-color: #f8f9fa; border-top: 1px solid #dee2e6; }
QStatusBar { color: #6c757d; font-size: 11px; background-color: #f8f9fa; }
QStatusBar::item { border: none; }
/* ---- 全局 QMessageBox（弹窗） ---- */
QMessageBox {
    background-color: #ffffff;
    color: #333333;
}
QMessageBox QLabel {
    color: #333333;
    font-size: 13px;
}
QMessageBox QPushButton {
    background-color: #0d6efd;
    color: #ffffff;
    border: none;
    border-radius: 4px;
    padding: 6px 16px;
    min-width: 60px;
    min-height: 26px;
    font-weight: bold;
}
QMessageBox QPushButton:hover {
    background-color: #0b5ed7;
}
QMessageBox QPushButton:pressed {
    background-color: #0a58ca;
}
QMessageBox QDialogButtonBox { background-color: #ffffff; }

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

    def __init__(self, engine, dimension, months, ch, cat, subcat, model_kw,
                 start_period=None, end_period=None, algorithm_filter=None,
                 auto_downgrade=True):
        super().__init__()
        self.engine = engine
        self.dimension = dimension
        self.months = months
        self.ch = ch
        self.cat = cat
        self.subcat = subcat
        self.model_kw = model_kw
        self.start_period = start_period
        self.end_period = end_period
        self.algorithm_filter = algorithm_filter
        self.auto_downgrade = auto_downgrade
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
            temp_df = ForecastEngine._filter_channel(temp_df, self.ch)

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
                start_period=self.start_period,
                end_period=self.end_period,
                filter_channel=self.ch,
                filter_category=self.cat,
                filter_subcategory=self.subcat,
                filter_model=self.model_kw,
                algorithm_filter=self.algorithm_filter,
                auto_downgrade=self.auto_downgrade,
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

class NumericTableItem(QTableWidgetItem):
    """支持按数值排序的表格项（用于准确率百分比列）"""
    def __lt__(self, other):
        try:
            return float(self.data(Qt.UserRole)) < float(other.data(Qt.UserRole))
        except (ValueError, TypeError, AttributeError):
            return self.text() < other.text()


# ============================================================
#  FAQ 问答数据与弹窗
# ============================================================

FAQ_DATA = [
    ("预测准确率的含义是什么？",
     "准确率 = 100% − sMAPE（对称平均百分比误差）。\n\n"
     "系统通过「回溯验证」来评估：\n"
     "  1. 保留最后 2~4 个月作为测试集\n"
     "  2. 用前面的数据训练算法\n"
     "  3. 比较预测值与真实值的 sMAPE\n"
     "  4. 选择 sMAPE 最低的算法作为最终预测器\n\n"
     "准确率越高（如 85%+），说明算法在历史数据上表现越好，未来预测也更可信。\n"
     "准确率显示「数据不足」说明该分组的历史数据太少，无法进行有意义的回溯验证。"),
    ("数据状态「良好/一般/偏低/质量差/不足/严重不足」代表什么？",
     "数据状态综合评估数据的「量」和「质」：\n\n"
     "◆ 良好：训练 ≥ 12 月且非零 ≥ 6 个，回溯准确率 ≥ 70%\n"
     "◆ 一般：回溯准确率 60%~69%\n"
     "◆ 偏低：回溯准确率 30%~59%\n"
     "◆ 质量差：回溯准确率 < 30%（历史表现差，预测可信度低）\n"
     "◆ 不足：训练 < 6 月或非零 < 3 个\n"
     "◆ 严重不足：训练 < 3 月或无销量记录\n\n"
     "注意：准确率来自回溯验证（用历史数据模拟预测），不代表未来必定准确。\n"
     "当状态为「质量差」时，说明即使数据量足够，算法在历史上也表现不佳，\n"
     "可能由于间歇性需求、高波动性等原因，建议谨慎使用预测结果。"),
    ("为什么有些预测准确率为 0%？",
     "准确率 0% 通常由以下原因导致：\n\n"
     "1. 间歇性需求：销量忽有忽无（如 0→5→0→3→0），\n"
     "   任何算法都很难捕捉这种随机模式\n"
     "2. 非平稳序列：销量模式发生结构性变化\n"
     "   （如新品上量、促销冲击），历史规律不再适用\n"
     "3. 回测撞上异常点：测试集恰好是异常月份\n"
     "4. 微销量放大效应：月销仅 0.x 的小数波动\n"
     "   在 sMAPE 中会被剧烈放大\n\n"
     "系统已过滤实际值 < 1.0 的噪声点来缓解问题 4，\n"
     "但前三个原因属于数据本身的固有难度。"),
    ("数值列的格式怎么看？",
     "每个时期列显示三种（或一种）数值：\n\n"
     "  黑色（120）= 该月的历史实际销量\n"
     "  绿色（135）= 算法推算的预测/回测销量\n"
     "  蓝色（88.73%）= 该月单独计算的准确率\n"
     "    = 1 − |预测值 − 实际值| / 实际值\n\n"
     "纯绿色单值：该月是未来月份（DB 中无记录）\n"
     "三色对比：该月 DB 有记录，可做回测验证\n\n"
     "列顺序：未来的月份在前，历史的月份在后。"),
    ("如何更换数据库？",
     "点击右侧内容区顶部的「更换」按钮：\n\n"
     "  1. 在弹出的文件对话框中选择 .sqlite 数据库文件\n"
     "  2. 系统自动加载新数据，更新筛选条件和下拉选项\n"
     "  3. 历史使用过的数据库会保留在列表中，方便快速切换\n\n"
     "数据库路径存储在程序同目录的 .db_history.json 文件中。"),
    ("如何导出预测结果？",
     "点击右侧内容区底部的「导出 CSV」按钮即可。\n\n"
     "导出的 CSV 文件包含：\n"
     "  - 所有维度和状态列\n"
     "  - 历史实际销量 + 预测/回测数值\n"
     "  - 各期的准确率\n\n"
     "文件编码为 UTF-8 BOM，可在 Excel 中直接打开。\n"
     "注意：表格 UI 最多展示 500 行，但导出的 CSV 包含全部数据。"),
    ("预测算法有哪些？如何选择？",
     "系统内置 9 种算法，自动择优：\n\n"
     "  轻量算法（速度快，适用广）：\n"
     "  Naive  — 朴素预测（上期值重复）\n"
     "  SMA    — 简单移动平均\n"
     "  Median — 中位数预测\n"
     "  Croston— 间歇性需求专用\n\n"
     "  重型算法（精度高，适合少分组）：\n"
     "  HW     — Holt-Winters 指数平滑\n"
     "  RF     — 随机森林回归\n"
     "  SARIMA — 季节 ARIMA 模型\n"
     "  XGBoost— 梯度提升树\n"
     "  LightGBM— 轻量梯度提升\n\n"
     "选择流程：每组数据都回溯验证所有启用的算法，\n"
     "自动选出 sMAPE 最低的那个。\n\n"
     "在「功能设置」页可以勾选/取消各算法，\n"
     "点击「应用算法设置」后生效。\n"
     "重型算法可在「运行优化」中通过\n"
     "「自动降速」开关控制何时关闭。"),
    ("预测的时间范围如何设置？",
     "在筛选区的「预测时间」区域选择：\n\n"
     "  起始年月：不能早于数据库最老数据 + 6 个月\n"
     "  （需留出最少训练窗口，否则无法预测）\n"
     "  结束年月：可以超出数据库最新月份（未来预测）\n"
     "  跨年份：结束年 ≠ 起始年时，结束月可选 1~12\n"
     "  同一年：结束月 ≥ 起始月\n\n"
     "预测区间上限为 24 个月（可在运行优化中关闭）。\n"
     "预测公式：用「起始月前一个月」及之前的所有数据\n"
     "训练模型，推算起始月至结束月共 N 个月的销量。"),
    ("预测速度怎么样？",
     "预测速度取决于维度、算法和数据量：\n\n"
     "◆ 大类维度（~10 组）：3~5 秒\n"
     "◆ 细分类（~50 组，全算法）：10~20 秒\n"
     "◆ 型号维度 ≤ 50 组（全算法）：10~30 秒\n"
     "◆ 型号维度 > 50 组（仅轻量算法）：5~15 秒\n\n"
     "系统采用多线程并行计算。重型算法在组数多时\n"
     "默认自动关闭（可在「运行优化」中关闭此保护）。"),
    ("如何提高预测准确率？",
     "可以从以下几个方面着手：\n\n"
     "1. 缩小筛选范围——选特定品类或渠道\n"
     "2. 选择较粗粒度——大类维度通常比型号更稳定\n"
     "3. 在功能设置中打开重型算法——SARIMA/XGBoost\n"
     "   等算法精度通常更高（但更慢）\n"
     "4. 检查数据状态——优先使用「良好」和「一般」的分组预测结果"),
    ("数据安全吗？会修改原始数据库吗？",
     "不会。系统使用 SQLite 只读连接打开数据库：\n\n"
     "◆ 连接模式为 sqlite3 'readonly'\n"
     "◆ 所有计算在内存中的 DataFrame 完成\n"
     "◆ 不做任何 INSERT/UPDATE/DELETE 操作\n"
     "◆ 数据库文件路径存储在 .db_history.json 中\n\n"
     "可以完全放心使用，不会对原始数据造成任何影响。"),
    ("可以自定义算法吗？",
     "可以。系统采用插件式算法架构：\n\n"
     "  1. 继承 BasePredictor 类\n"
     "  2. 实现 fit_predict() 方法\n"
     "  3. 将新算法加入 PREDICTORS 列表\n\n"
     "内置 9 种算法已覆盖绝大多数动销场景。\n"
     "在功能设置页可以按需启用/禁用各算法。"),
    ("数据有缺失怎么办？",
     "系统中缺失数据的处理策略：\n\n"
     "◆ 某月无销售记录 → 填充为 0（表示无动销）\n"
     "◆ 训练数据不足（< 12 个月或 < 3 个非零值）→\n"
     "  跳过回溯验证，用朴素预测\n"
     "◆ 安装算法包缺失（如 LightGBM）→\n"
     "  对应算法自动降级为朴素预测\n\n"
     "关键原则：准确率必须基于原始数据计算，\n"
     "插值仅用于模型训练。"),
    ("为什么表格只显示了部分行？",
     "系统默认展示上限为 500 行（可在运行优化中关闭）。\n\n"
     "当预测结果超过 500 条时：\n"
     "  1. 按准确率排序，优先展示数据状态好的分组\n"
     "  2. 末尾行提示还有多少条未显示\n"
     "  3. 导出 CSV 包含完整数据\n\n"
     "此限制是为了保障界面响应速度，\n"
     "500 行以上的表格渲染会导致内存溢出。"),
    ("为什么型号维度部分算法没有运行？",
     "为保障响应速度，系统会自动调整算法池：\n\n"
     "型号 > 50 组 → 关闭 RF/HW/SARIMA/XGBoost/LightGBM\n"
     "细分类 > 50 组 → 关闭 RF/SARIMA\n\n"
     "可在「功能设置」>「运行优化」中关闭\n"
     "「重型算法自动降速」开关，恢复全算法运行。\n"
     "也可在「预测算法」中手动勾选所需算法。"),
    ("「数据质量评估」模块怎么用？",
     "左侧导航栏点击「📊 数据质量评估」进入：\n\n"
     "模块自动扫描数据库，生成四类信息：\n"
     "◆ 数据库概览：总记录数、时间范围、品类/型号数\n"
     "◆ 数据完整性：哪些型号缺少月份、平均覆盖率\n"
     "◆ 可预测性评估：按 CV 和零值比分级（易/中/难/不足）\n"
     "◆ 潜在问题：当月数据不完整（如 7 月仅 7 天）\n"
     "  导致准确率异常、数据不足型号、间歇需求过多等\n\n"
     "结果来自后台线程，加载时不阻塞界面。"),
    ("「运行优化」的三个开关分别控制什么？",
     "在功能设置页「运行优化」区域配置，三个开关默认全部启用：\n\n"
     "◆ 预测时间限制（24 个月）\n"
     "  好处：防止超长区间导致算法拟合失真和内存溢出\n"
     "  关闭后允许更长区间，但准确度会下降\n\n"
     "◆ 重型算法自动降速（>50 组关闭重型算法）\n"
     "  好处：型号维度组数多时自动跳过耗时算法，预测时间\n"
     "  从数分钟降至数秒，体验流畅\n"
     "  关闭后全量算法运行，精度可能提升但速度显著变慢\n\n"
     "◆ 展示上限 500 行\n"
     "  好处：表格渲染时只显示前 500 行 + 合计行，\n"
     "  避免创建数万个控件导致界面卡死\n"
     "  关闭后显示全量数据，但有性能风险\n\n"
     "每个开关独立控制，点击「应用配置」生效。\n"
     "建议保持全开，遇到特殊需求时再单独关闭。"),
    ("7 月份预测准确率为 0% 是正常的吗？",
     "是正常的。当前日期是 7 月上旬（如 7 月 7 日），\n"
     "数据库中 7 月销量只记录了 7 天，仅是全月销量的一小部分。\n\n"
     "而绿色预测值是完整的全月预测（30 天）。\n"
     "用 7 天数据和 30 天预测比较，天然偏低。\n\n"
     "建议：7 月数据完整之前，该月准确率无参考意义。\n"
     "6 月及之前的准确率才是有效的回测指标。"),
]


class FaqDialog(QDialog):
    """FAQ 详情弹窗 —— 紧贴帮助区域显示，固定大小，可滚动，浅色主题"""

    def __init__(self, parent, question: str, answer: str, anchor_widget: QWidget):
        super().__init__(parent)
        self.setWindowTitle("帮助详情")
        self.setWindowFlags(Qt.Tool)
        self.setFixedSize(440, 340)
        self.setStyleSheet("""
            QDialog { background-color: #ffffff; }
            QPushButton { background-color: #0d6efd; color: #ffffff; border: none;
                          border-radius: 4px; padding: 6px 12px; font-size: 13px; }
            QPushButton:hover { background-color: #0b5ed7; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        # 问题标题
        q_label = QLabel(question)
        q_label.setWordWrap(True)
        q_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #1a1a2e; padding: 0;")
        layout.addWidget(q_label)

        # 分隔线
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("background-color: #dee2e6; max-height: 1px;")
        layout.addWidget(sep)

        # 可滚动答案区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background-color: transparent; border: none; }")

        a_label = QLabel(answer)
        a_label.setWordWrap(True)
        a_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        a_label.setStyleSheet("""
            font-size: 13px; color: #333333; line-height: 1.7;
            padding: 10px 12px; background-color: #f8f9fa;
            border-radius: 6px; border: 1px solid #e9ecef;
        """)
        a_label.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(a_label)
        layout.addWidget(scroll, stretch=1)

        # 关闭按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.close)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

        # 点击弹窗外任意位置自动关闭
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._app = QApplication.instance()
        self._app.installEventFilter(self)

        # 定位：紧贴帮助区域右侧
        self._position_near(anchor_widget)

    def eventFilter(self, watched, event):
        """全局事件过滤：鼠标点击弹窗外时自动关闭"""
        if event.type() == QEvent.MouseButtonPress and self.isVisible():
            if hasattr(event, 'globalPosition'):
                gpos = event.globalPosition().toPoint()
            else:
                gpos = event.globalPos()
            widget_under = QApplication.widgetAt(gpos)
            if widget_under is not None and not self.isAncestorOf(widget_under):
                self.close()
        return False  # 不吞事件，确保 FAQ 列表等能正常响应

    def closeEvent(self, event):
        """关闭时移除事件过滤器"""
        self._app.removeEventFilter(self)
        super().closeEvent(event)

    def _position_near(self, anchor: QWidget):
        """将弹窗定位在锚点控件的右侧，若超出屏幕则改为左侧"""
        pos = anchor.mapToGlobal(anchor.rect().topRight())
        screen = QApplication.primaryScreen()
        if screen:
            screen_geo = screen.availableGeometry()
            if pos.x() + self.width() > screen_geo.right():
                # 右侧空间不够，改到左侧
                left_pos = anchor.mapToGlobal(anchor.rect().topLeft())
                pos.setX(max(0, left_pos.x() - self.width() - 8))
            else:
                pos.setX(pos.x() + 8)
        self.move(pos)


# ============================================================
#  虚拟化表格模型 + HTML 委托（QTableView 替代 QTableWidget）
# ============================================================

HTML_ROLE = Qt.UserRole + 100       # 元组列富文本（委托绘制用）
ROW_TYPE_ROLE = Qt.UserRole + 101   # 'total' | 'hint' | 'normal'
SORT_VAL_ROLE = Qt.UserRole + 102   # 排序键值

class PredictionTableModel(QAbstractTableModel):
    """包装 DataFrame 的虚拟化表格模型：无预创建对象，按需查询 data()"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df = pd.DataFrame()
        self._columns: List[str] = []
        self._fixed_cols = 0
        self._truncated = False

    def set_dataframe(self, df: pd.DataFrame, fixed_cols: int, truncated: bool = False):
        """替换数据源，通知视图刷新"""
        self.beginResetModel()
        self._df = df
        self._columns = list(df.columns)
        self._fixed_cols = fixed_cols
        self._truncated = truncated
        self.endResetModel()

    def clear(self):
        self.set_dataframe(pd.DataFrame(), 0, False)

    def rowCount(self, parent=QModelIndex()) -> int:
        return len(self._df)

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(self._columns)

    def headerData(self, section: int, orientation: int, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if 0 <= section < len(self._columns):
                return str(self._columns[section])
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        ri, ci = index.row(), index.column()
        if ri < 0 or ri >= len(self._df) or ci < 0 or ci >= len(self._columns):
            return None
        col = self._columns[ci]
        try:
            val = self._df.iloc[ri, ci]
        except (IndexError, KeyError):
            return None
        if pd.isna(val) or val == '':
            val = None

        # 行类型
        if ri == 0:
            row_type = 'total'
        elif self._truncated and ri == len(self._df) - 1:
            row_type = 'hint'
        else:
            row_type = 'normal'

        # 是否数值列（元组列）
        is_num_col = ci >= self._fixed_cols and col not in ('准确率', '数据状态', '预测算法')
        is_tuple = is_num_col and isinstance(val, tuple)

        if role == ROW_TYPE_ROLE:
            return row_type

        if role == Qt.DisplayRole:
            if val is None:
                return ''
            if col == '准确率' and not isinstance(val, str):
                return f"{float(val):.2f}%"
            if is_tuple:
                t = val
                h, p = t[0], t[1]
                a = t[2] if len(t) >= 3 else None
                if h is not None and p is not None:
                    return f"{int(h)} / {p}" + (f" / {a:.2f}%" if a is not None else "")
                elif p is not None:
                    return str(p)
                else:
                    return str(int(h)) if h is not None else ''
            return str(val)

        if role == HTML_ROLE and is_tuple:
            t = val
            h, p = t[0], t[1]
            a = t[2] if len(t) >= 3 else None
            if h is not None and p is not None:
                acc = f"<span style='color:#0d6efd'> / {a:.2f}%</span>" if a is not None else ""
                return f"<span style='color:#212529'>{int(h)}</span> / <span style='color:#198754'>{p}</span>{acc}"
            elif p is not None:
                return f"<span style='color:#198754'>{p}</span>"
            else:
                return f"<span style='color:#212529'>{int(h)}</span>" if h is not None else ''
            return None

        if role == Qt.ForegroundRole:
            if col == '数据状态':
                t = str(val)
                return QColor("#198754") if t in ('良好', '充足') else \
                       QColor("#0d6efd") if t == '一般' else \
                       QColor("#fd7e14") if t == '偏低' else \
                       QColor("#dc3545")
            if col == '准确率':
                return QColor("#dc3545") if isinstance(val, str) else QColor("#198754")
            if is_tuple:
                t = val
                h, p = t[0], t[1]
                if p is not None and h is None:
                    return QColor("#198754")
                return QColor("#212529")
            if is_num_col:
                return QColor("#212529")
            return None

        if role == Qt.BackgroundRole:
            if row_type == 'total':
                return QColor("#f1f3f5")
            return None

        if role == Qt.FontRole:
            if row_type == 'total':
                f = QFont()
                f.setBold(True)
                return f
            return None

        if role == Qt.TextAlignmentRole:
            if is_num_col or col in ('准确率', '数据状态', '预测算法'):
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return None

        if role == SORT_VAL_ROLE:
            if col == '准确率' and not isinstance(val, str):
                return float(val)
            if is_tuple:
                t = val
                p = t[1] if t[1] is not None else t[0]
                return float(p) if p is not None else 0.0
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        if role == Qt.UserRole:
            # NumericItem 兼容：准确率排序
            if col == '准确率' and not isinstance(val, str):
                return float(val)
            return None

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable


class HTMLDelegate(QStyledItemDelegate):
    """用 QTextDocument 渲染 HTML 元组列，替代 QLabel"""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        html = index.data(HTML_ROLE)
        if html:
            painter.save()
            # 背景
            selected = bool(option.state & QStyle.State_Selected)
            if selected:
                painter.fillRect(option.rect, QColor("#e7f1ff"))
            else:
                row_type = index.data(ROW_TYPE_ROLE)
                if row_type == 'total':
                    painter.fillRect(option.rect, QColor("#f1f3f5"))
                else:
                    painter.fillRect(option.rect, QColor("#ffffff"))

            # HTML 文本：QTextDocument 直接绘制
            doc = QTextDocument()
            doc.setDefaultFont(option.font)
            doc.setHtml(f"<body style='margin:0; padding:2px 6px; text-align:right;'>{html}</body>")
            doc.setTextWidth(option.rect.width() - 12)
            # 垂直居中
            doc_h = doc.size().height()
            y_offset = max(0, (option.rect.height() - doc_h) / 2)
            painter.translate(option.rect.left() + 6, option.rect.top() + y_offset)
            ctx = QAbstractTextDocumentLayout.PaintContext()
            doc.documentLayout().draw(painter, ctx)
            painter.restore()
        else:
            super().paint(painter, option, index)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(super().sizeHint(option, index).width(), 28)


class QualityWorker(QThread):
    """后台数据质量评估线程"""
    finished_data = Signal(object)
    finished_error = Signal(str)

    def __init__(self, data_loader, db_path):
        super().__init__()
        self.data_loader = data_loader
        self.db_path = db_path

    def run(self):
        try:
            data = self._collect()
            self.finished_data.emit(data)
        except Exception as e:
            import traceback
            self.finished_error.emit(f"{e}\n{traceback.format_exc()}")

    def _collect(self):
        """批量 SQL 采集数据质量信息（后台线程）"""
        conn = self.data_loader._new_conn()
        now = datetime.now()
        data = {}

        # ── 基本统计 ──
        total = conn.execute(f"SELECT COUNT(*) FROM {VIEW_NAME}").fetchone()[0]
        periods = conn.execute(
            f"SELECT MIN(账期), MAX(账期) FROM {VIEW_NAME}"
        ).fetchone()
        min_p, max_p = (periods[0] if periods else None, periods[1] if periods else None)
        months_count = 0
        if min_p and max_p:
            months_count = (int(max_p[:4]) - int(min_p[:4])) * 12 + \
                           (int(max_p[4:6]) - int(min_p[4:6])) + 1

        cats = conn.execute(
            f"SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(品类),''),'{UNKNOWN_CATEGORY}')) FROM {VIEW_NAME}"
        ).fetchone()[0]
        models_cnt = conn.execute(
            f"SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(型号),''),'{UNKNOWN_MODEL}')) FROM {VIEW_NAME}"
        ).fetchone()[0]

        db_size = os.path.getsize(self.db_path) / (1024 * 1024) \
            if self.db_path and os.path.exists(self.db_path) else 0

        data['total_rows'] = total or 0
        data['period_min'] = min_p or '-'
        data['period_max'] = max_p or '-'
        data['total_months'] = months_count
        data['category_count'] = cats or 0
        data['model_count'] = models_cnt or 0
        data['db_size_mb'] = f"{db_size:.1f}"

        # ── 批量获取所有型号×月份的数据（一次查询替代 N 次） ──
        all_periods = {row[0] for row in conn.execute(
            f"SELECT DISTINCT 账期 FROM {VIEW_NAME} ORDER BY 账期"
        ) if row[0]}
        expected_months = len(all_periods)

        batch = conn.execute(f"""
            SELECT COALESCE(NULLIF(TRIM(型号),''),'{UNKNOWN_MODEL}') as model_name,
                   账期, SUM(动销) as qty
            FROM {VIEW_NAME}
            GROUP BY 1, 2 ORDER BY 1, 2
        """).fetchall()

        # 按型号分组
        from collections import defaultdict
        model_data = defaultdict(list)
        for model_name, period, qty in batch:
            model_data[model_name].append((period, qty))

        # 完整性统计
        model_period_counts = []
        all_model_names = set(model_data.keys())
        for name in all_model_names:
            periods_set = set(p for p, _ in model_data[name])
            model_period_counts.append((name, len(periods_set)))

        full = sum(1 for _, c in model_period_counts if c >= expected_months * 0.95) if expected_months > 0 else 0
        missing_grp = sum(1 for _, c in model_period_counts if 0 < c < expected_months * 0.95) if expected_months > 0 else 0
        empty = models_cnt - len(all_model_names)
        missing_details = sorted(
            [{'name': n, 'missing': expected_months - c} for n, c in model_period_counts if c < expected_months],
            key=lambda x: x['missing'], reverse=True
        )[:10]
        avg_cov = sum(c / expected_months * 100 for _, c in model_period_counts) / max(len(model_period_counts), 1) \
            if expected_months > 0 and model_period_counts else 0.0

        data['completeness'] = {
            'full_groups': full, 'missing_groups': missing_grp, 'empty_groups': empty,
            'avg_coverage': avg_cov, 'missing_group_details': missing_details,
        }

        # ── 可预测性评估（批量计算） ──
        easy = medium = hard = insufficient = 0
        for name, cnt in model_period_counts:
            if cnt < 2:
                insufficient += 1
                continue
            values = [q for _, q in model_data[name]]
            nz = [v for v in values if v and v > 0]
            if len(nz) < 2:
                insufficient += 1
                continue
            mean_val = sum(nz) / len(nz)
            std_val = (sum((v - mean_val) ** 2 for v in nz) / len(nz)) ** 0.5
            cv = std_val / mean_val if mean_val > 0 else 999
            zero_ratio = (len(values) - len(nz)) / len(values) if values else 0

            if cv < 0.5 and zero_ratio < 0.3:
                easy += 1
            elif cv < 1.0 and zero_ratio < 0.6:
                medium += 1
            else:
                hard += 1

        data['predictability'] = {
            'easy': easy, 'medium': medium, 'hard': hard, 'insufficient': insufficient
        }

        # ── 潜在问题 ──
        issues = []
        current_period = f"{now.year}{now.month:02d}"

        if total > 0 and max_p:
            try:
                cur_rows = conn.execute(
                    f"SELECT COUNT(*) FROM {VIEW_NAME} WHERE 账期 = ?", (current_period,)
                ).fetchone()[0]
                if cur_rows > 0:
                    prev_period = f"{now.year}{now.month - 1:02d}" if now.month > 1 else f"{now.year - 1}12"
                    prev_rows = conn.execute(
                        f"SELECT COUNT(*) FROM {VIEW_NAME} WHERE 账期 = ?", (prev_period,)
                    ).fetchone()[0]
                    if prev_rows > 0 and cur_rows < prev_rows * (now.day / 30):
                        issues.append({
                            'level': 'warning',
                            'title': f"\u5f53\u6708\u6570\u636e\u4e0d\u5b8c\u6574\uff08{current_period}\uff09",
                            'detail': f"\u4ec5\u6709{cur_rows}\u6761\u8bb0\u5f55\uff08\u4eca\u5929{now.day}\u53f7\uff09\uff0c\u9884\u6d4b\u51c6\u786e\u7387\u53ef\u80fd\u5f02\u5e38\u504f\u4f4e"
                        })
            except Exception:
                pass
        elif total == 0:
            issues.append({
                'level': 'error',
                'title': "\u6570\u636e\u5e93\u4e3a\u7a7a",
                'detail': "\u8bf7\u786e\u8ba4\u6570\u636e\u5e93\u662f\u5426\u6b63\u786e\u5bfc\u5165"
            })

        if insufficient > 0:
            issues.append({
                'level': 'warning',
                'title': f"\u6570\u636e\u4e0d\u8db3\u578b\u53f7\uff1a{insufficient} \u4e2a",
                'detail': "\u53ea\u80fd\u7528 Naive \u7b97\u6cd5\uff0c\u9884\u6d4b\u7cbe\u5ea6\u6709\u9650"
            })

        if medium + hard > easy * 2 and easy > 0:
            issues.append({
                'level': 'info',
                'title': f"\u56f0\u96be\u9884\u6d4b\u578b\u53f7\u5360\u6bd4\u8f83\u9ad8\uff08{medium + hard}/{total} \u7ec4\uff09",
                'detail': "\u5927\u91cf\u96f6\u503c\u5bfc\u81f4\u7edf\u8ba1\u6a21\u5f0f\u96be\u4ee5\u6355\u6349\uff0c\u5efa\u8bae\u63d0\u9ad8\u805a\u5408\u7ef4\u5ea6\u9884\u6d4b"
            })

        try:
            if max_p and int(max_p) > int(current_period):
                issues.append({
                    'level': 'info',
                    'title': f"\u6570\u636e\u5e93\u5305\u542b\u672a\u6765\u6708\u4efd\u6570\u636e\uff08{max_p}\uff09",
                    'detail': "\u6b63\u5e38\u73b0\u8c61\uff0c\u53ef\u80fd\u4e3a\u9884\u5148\u5bfc\u5165\u7684\u8ba1\u5212\u6570\u636e"
                })
        except Exception:
            pass

        if months_count < 12:
            issues.append({
                'level': 'error',
                'title': f"\u6570\u636e\u65f6\u95f4\u8de8\u5ea6\u4e0d\u8db3\uff08\u4ec5{months_count}\u4e2a\u6708\uff09",
                'detail': "\u65e0\u6cd5\u6355\u6349\u5b63\u8282\u6027\u6a21\u5f0f\uff0c\u5efa\u8bae\u8865\u5145\u66f4\u591a\u5386\u53f2\u6570\u636e"
            })

        data['issues'] = issues
        conn.close()
        return data


class SalesForecastWindow(QMainWindow):
    """动销预测主窗口 v2"""

    status_message = Signal(str)

    # 渠道按钮选项
    CHANNEL_OPTIONS = ['线上和线下', '线上', '线下']

    def __init__(self, db_path: Optional[str] = None):
        super().__init__()
        self.db_path = db_path or ''
        self.db_manager = DbPathManager()
        self.data_loader = None
        self.engine = None
        self.current_result_df = None
        self.dimensions_data = {}
        self.worker = None
        self.model_history = ModelHistoryManager()
        # 数据时间边界
        self.data_min_year = 2018
        self.data_max_year = 2026
        self.data_min_month = 1
        self.data_max_month = 12
        self.all_years: List[int] = []
        self.current_dimension = 'model'
        self.current_channel = None
        self.app_settings = load_app_settings()
        self.setWindowTitle("动销预测系统 — DYZG OMS")
        self.setMinimumSize(1200, 750)
        self.resize(1400, 850)
        self.setStyleSheet(TABLE_STYLESHEET)

        self._setup_ui()
        self._fix_combo_popup_frames()

        # 有有效数据库则加载，否则显示占位提示
        if self.db_path and os.path.exists(self.db_path):
            self._init_with_db()
        else:
            self.label_db_path.setText("（请选择数据库文件）")
            self.statusBar().showMessage("就绪 — 请点击「更换」选择数据库文件")

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

        # 导航列表
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("nav_list")
        nav_items = [
            ("\U0001f3e0 \u9996\u9875", "home"),
            ("\U0001f4ca \u6570\u636e\u8d28\u91cf\u8bc4\u4f30", "quality"),
            ("\u2699 \u529f\u80fd\u8bbe\u7f6e", "settings"),
        ]
        for label, key in nav_items:
            item_text = f"  {label}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, key)
            self.nav_list.addItem(item)
        self.nav_list.setCurrentRow(0)
        self.nav_list.itemClicked.connect(self._on_nav_clicked)
        nav_layout.addWidget(self.nav_list)

        # ---- 帮助及常见问题 ----
        help_section = QGroupBox("帮助及常见问题")
        help_section.setObjectName("help_section")
        help_layout = QVBoxLayout(help_section)
        help_layout.setContentsMargins(4, 8, 4, 4)
        help_layout.setSpacing(4)

        # 搜索框
        self.faq_search = QLineEdit()
        self.faq_search.setObjectName("faq_search")
        self.faq_search.setPlaceholderText("搜索问题...")
        self.faq_search.setClearButtonEnabled(True)
        self.faq_search.textChanged.connect(self._on_faq_search_changed)
        help_layout.addWidget(self.faq_search)

        self.faq_list = QListWidget()
        self.faq_list.setObjectName("faq_list")
        self.faq_list.setMaximumHeight(120)
        self.faq_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        help_layout.addWidget(self.faq_list)

        # 填充 FAQ 列表
        self._populate_faq_list()
        self.faq_list.itemClicked.connect(self._on_faq_clicked)

        nav_layout.addWidget(help_section)

        nav_spacer = QWidget()
        nav_spacer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        nav_layout.addWidget(nav_spacer)

        main_layout.addWidget(nav_panel)

        # ===== 右侧内容区（页面栈）=====
        content = QWidget()
        content.setObjectName("content_widget")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)

        self.content_stack = QStackedWidget()
        self.content_stack.setObjectName("content_stack")
        self.content_stack.addWidget(self._create_home_page())
        self.content_stack.addWidget(self._create_predict_page())
        self.content_stack.addWidget(self._create_quality_page())
        self.content_stack.addWidget(self._create_settings_page())
        self.content_stack.setCurrentIndex(0)
        content_layout.addWidget(self.content_stack)

        main_layout.addWidget(content, stretch=1)

        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.hide()


    # ========== 页面创建：首页 ==========
    def _create_home_page(self):
        """创建首页——聚合三大预测维度入口"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(50, 50, 50, 50)
        layout.setSpacing(30)

        title = QLabel("📊 动销预测系统")
        title.setStyleSheet("font-size: 28px; font-weight: bold; color: #1a1a2e; padding: 0;")
        layout.addWidget(title)

        subtitle = QLabel("请选择预测维度开始分析")
        subtitle.setStyleSheet("font-size: 15px; color: #6c757d;")
        layout.addWidget(subtitle)

        cards_layout = QHBoxLayout()
        cards_layout.setSpacing(24)

        dims = [
            ("📈", "渠道型号", "最细粒度\n按具体产品型号预测", "model"),
            ("📊", "渠道细分类", "中等粒度\n按产品子类预测", "subcategory"),
            ("📋", "渠道大类", "粗粒度\n按产品大类预测", "category"),
        ]

        for icon, name, desc, key in dims:
            card = QPushButton()
            card.setObjectName("home_card")
            card.setMinimumSize(220, 200)
            card.setCursor(Qt.PointingHandCursor)
            card.setStyleSheet("""
                QPushButton#home_card {
                    background-color: #ffffff;
                    border: 2px solid #dee2e6;
                    border-radius: 12px;
                    font-size: 13px;
                }
                QPushButton#home_card:hover {
                    border-color: #89b4fa;
                    background-color: #f0f6ff;
                }
            """)
            card_text = f"{icon}\n\n{name}\n\n{desc}"
            card.setText(card_text)
            card.clicked.connect(lambda checked, k=key: self._open_dimension(k))
            cards_layout.addWidget(card)

        layout.addLayout(cards_layout)

        tip = QLabel("右侧导航栏也可以切换功能页面")
        tip.setAlignment(Qt.AlignCenter)
        tip.setStyleSheet("font-size: 12px; color: #adb5bd; padding-top: 20px;")
        layout.addWidget(tip)

        layout.addStretch()
        return page

    # ========== 页面创建：预测页（原筛选+表格）==========
    def _create_predict_page(self):
        """创建预测页面——包含筛选区和结果表格"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        header_layout = QHBoxLayout()
        self.dim_title = QLabel("🔮 动销预测率 — 渠道型号")
        self.dim_title.setObjectName("dim_title")
        header_layout.addWidget(self.dim_title)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        self.db_bar_widget = QWidget()
        db_bar_layout = QHBoxLayout(self.db_bar_widget)
        db_bar_layout.setContentsMargins(0, 0, 0, 0)
        db_bar_layout.setSpacing(8)
        db_label = QLabel("数据库:")
        db_label.setStyleSheet("color: #6c757d; font-weight: bold;")
        db_bar_layout.addWidget(db_label)
        self.label_db_path = QLabel(self.db_path)
        self.label_db_path.setStyleSheet("color: #495057;")
        self.label_db_path.setWordWrap(True)
        db_bar_layout.addWidget(self.label_db_path, stretch=1)
        self.btn_switch_db = QPushButton("更换")
        self.btn_switch_db.setObjectName("btn_default")
        self.btn_switch_db.setMaximumWidth(60)
        self.btn_switch_db.clicked.connect(self._on_switch_db)
        db_bar_layout.addWidget(self.btn_switch_db)
        layout.addWidget(self.db_bar_widget)

        filter_box = QGroupBox("筛选条件")
        filter_layout = QGridLayout(filter_box)
        filter_layout.setSpacing(10)

        filter_layout.addWidget(QLabel("渠道:"), 0, 0)
        self.channel_btn_group = {}
        ch_btn_layout = QHBoxLayout()
        ch_btn_layout.setSpacing(8)
        for i, ch_option in enumerate(self.CHANNEL_OPTIONS):
            btn = QPushButton(ch_option)
            btn.setObjectName("ch_btn")
            btn.setCheckable(True)
            if i == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda checked, opt=ch_option: self._on_channel_button_clicked(opt))
            self.channel_btn_group[ch_option] = btn
            ch_btn_layout.addWidget(btn)
        filter_layout.addLayout(ch_btn_layout, 0, 1, 1, 2)

        filter_layout.addWidget(QLabel("品类:"), 0, 3)
        self.combo_category = QComboBox()
        self.combo_category.setMinimumWidth(120)
        self.combo_category.setMaxVisibleItems(10)
        self.combo_category.currentTextChanged.connect(self._on_category_changed)
        filter_layout.addWidget(self.combo_category, 0, 4)

        filter_layout.addWidget(QLabel("细分类:"), 1, 0)
        self.combo_subcategory = QComboBox()
        self.combo_subcategory.setMinimumWidth(130)
        self.combo_subcategory.setMaxVisibleItems(10)
        filter_layout.addWidget(self.combo_subcategory, 1, 1)

        filter_layout.addWidget(QLabel("型号:"), 1, 2)
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.setPlaceholderText("输入/选择型号关键词...")
        self.combo_model.setMinimumWidth(180)
        self.combo_model.setMaxVisibleItems(10)
        self.combo_model.lineEdit().returnPressed.connect(self._on_model_enter_pressed)
        self.combo_model.currentTextChanged.connect(self._on_model_text_changed)
        self.combo_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        model_completer = self.combo_model.completer()
        if model_completer:
            model_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            model_completer.setFilterMode(Qt.MatchContains)
            model_completer.setCaseSensitivity(Qt.CaseInsensitive)
        filter_layout.addWidget(self.combo_model, 1, 3, 1, 2)

        filter_layout.addWidget(QLabel("预测时间:"), 2, 0)
        time_widget = QWidget()
        time_layout = QHBoxLayout(time_widget)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(4)
        self.combo_year_start = QComboBox()
        self.combo_year_start.setMinimumWidth(62)
        self.combo_year_start.setMaximumWidth(72)
        time_layout.addWidget(self.combo_year_start)
        self.combo_month_start = QComboBox()
        self.combo_month_start.setMinimumWidth(52)
        self.combo_month_start.setMaximumWidth(60)
        time_layout.addWidget(self.combo_month_start)
        time_layout.addWidget(QLabel("至"))
        self.combo_year_end = QComboBox()
        self.combo_year_end.setMinimumWidth(62)
        self.combo_year_end.setMaximumWidth(72)
        time_layout.addWidget(self.combo_year_end)
        self.combo_month_end = QComboBox()
        self.combo_month_end.setMinimumWidth(52)
        self.combo_month_end.setMaximumWidth(60)
        time_layout.addWidget(self.combo_month_end)
        for m in range(1, 13):
            self.combo_month_start.addItem(f"{m}月")
            self.combo_month_end.addItem(f"{m}月")
        self.combo_month_start.setCurrentIndex(0)
        self.combo_month_end.setCurrentIndex(4)
        self.combo_year_start.currentTextChanged.connect(self._on_time_range_changed)
        self.combo_month_start.currentTextChanged.connect(self._on_time_range_changed)
        self.combo_year_end.currentTextChanged.connect(self._on_time_range_changed)
        self.combo_month_end.currentTextChanged.connect(self._on_time_range_changed)
        filter_layout.addWidget(time_widget, 2, 1, 1, 2)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
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
        filter_layout.addLayout(btn_layout, 3, 0, 1, 5)
        layout.addWidget(filter_box)

        self.table = QTableView()
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.setShowGrid(True)
        self.table.setSortingEnabled(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # 挂载模型 + HTML 委托
        self.table_model = PredictionTableModel(self)
        self.table.setModel(self.table_model)
        self.table.setItemDelegate(HTMLDelegate(self))
        layout.addWidget(self.table, stretch=1)

        return page

    # ========== 页面创建：功能设置页 ==========
    def _create_settings_page(self):
        """创建功能设置页面"""
        page = QWidget()
        page.setStyleSheet("background-color: #f8f9fa;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea { border: none; background-color: #f8f9fa; }
            QScrollArea > QWidget { background-color: #f8f9fa; }
            QScrollBar:vertical { width: 8px; background: #f0f0f0; }
            QScrollBar::handle:vertical { background: #c0c0c0; border-radius: 4px; }
        """)

        widget = QWidget()
        widget.setStyleSheet("background-color: #f8f9fa;")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(30, 25, 30, 25)
        layout.setSpacing(22)

        title = QLabel("⚙ 功能设置")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #1a1a2e;")
        layout.addWidget(title)

        # ==== 1. 预测算法 ====
        algo_group = QGroupBox("预测算法")
        algo_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; color: #2c3e50; border: 1px solid #dee2e6; border-radius: 8px; margin-top: 12px; padding: 16px 12px 10px 12px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }")
        algo_layout = QVBoxLayout(algo_group)
        self.algo_checkboxes = {}
        algo_desc = {
            "Naive": "朴素法", "SMA": "移动平均", "Median": "中位数",
            "HW": "指数平滑", "Croston": "间歇需求",
            "SARIMA": "季节ARIMA", "XGBoost": "梯度提升", "LightGBM": "轻量GBDT",
        }
        algo_names = ["Naive", "SMA", "Median", "HW", "Croston",
                      "SARIMA", "XGBoost", "LightGBM"]
        # 分两行展示
        algo_row1 = QHBoxLayout()
        algo_row1.setSpacing(16)
        algo_row2 = QHBoxLayout()
        algo_row2.setSpacing(16)
        for i, algo in enumerate(algo_names):
            cb = QCheckBox(f"{algo} ({algo_desc.get(algo, '')})")
            cb.setChecked(algo in self.app_settings.get("enabled_algorithms", DEFAULT_SETTINGS["enabled_algorithms"]))
            self.algo_checkboxes[algo] = cb
            if i < 5:
                algo_row1.addWidget(cb)
            else:
                algo_row2.addWidget(cb)
        algo_row1.addStretch()
        algo_row2.addStretch()
        algo_layout.addLayout(algo_row1)
        algo_layout.addLayout(algo_row2)

        # 应用按钮
        self.apply_algo_btn = QPushButton("应用算法设置")
        self.apply_algo_btn.setStyleSheet(
            "padding: 6px 16px; background: #0d6efd; color: white; border: none; "
            "border-radius: 4px; font-size: 13px;"
        )
        self.apply_algo_btn.setMaximumWidth(140)
        self.apply_algo_btn.clicked.connect(self._on_apply_algorithms)
        algo_layout.addWidget(self.apply_algo_btn)
        layout.addWidget(algo_group)

        # ==== 2. 运行优化 ====
        opt_group = QGroupBox("运行优化")
        opt_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; color: #2c3e50; border: 1px solid #dee2e6; border-radius: 8px; margin-top: 12px; padding: 16px 12px 10px 12px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }")
        opt_layout = QVBoxLayout(opt_group)
        opt_layout.setSpacing(8)

        self.opt_forecast_range = QCheckBox("预测时间限制（最长 24 个月，超限弹窗提示并阻止执行）")
        self.opt_forecast_range.setChecked(self.app_settings.get("forecast_range_limit", True))
        opt_layout.addWidget(self.opt_forecast_range)

        self.opt_auto_downgrade = QCheckBox("重型算法自动降速（型号维度 > 50 组 / 细分类 > 50 组时自动关闭重型算法）")
        self.opt_auto_downgrade.setChecked(self.app_settings.get("auto_downgrade", True))
        opt_layout.addWidget(self.opt_auto_downgrade)

        self.opt_table_row_limit = QCheckBox("展示上限 500 行（超出截断并提示导出，关闭后显示全量数据）")
        self.opt_table_row_limit.setChecked(self.app_settings.get("table_row_limit", True))
        opt_layout.addWidget(self.opt_table_row_limit)

        # 应用按钮
        self.apply_opt_btn = QPushButton("应用配置")
        self.apply_opt_btn.setStyleSheet(
            "padding: 6px 16px; background: #0d6efd; color: white; border: none; "
            "border-radius: 4px; font-size: 13px;"
        )
        self.apply_opt_btn.setMaximumWidth(120)
        self.apply_opt_btn.clicked.connect(self._on_apply_optimization)
        opt_layout.addWidget(self.apply_opt_btn)
        layout.addWidget(opt_group)

        # ==== 3. 界面功能 ====
        ui_group = QGroupBox("界面功能")
        ui_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; color: #2c3e50; border: 1px solid #dee2e6; border-radius: 8px; margin-top: 12px; padding: 16px 12px 10px 12px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }")
        ui_layout = QVBoxLayout(ui_group)
        self.ui_checkboxes = {}
        ui_labels = {
            "channel": "渠道筛选",
            "category": "品类筛选",
            "subcategory": "细分类筛选",
            "model": "型号搜索",
            "time_range": "预测时间",
            "db_bar": "数据库信息栏",
        }
        ui_row = QHBoxLayout()
        ui_row.setSpacing(16)
        for key, label in ui_labels.items():
            cb = QCheckBox(label)
            cb.setChecked(self.app_settings.get("visible_elements", {}).get(key, True))
            cb.stateChanged.connect(self._on_settings_changed)
            self.ui_checkboxes[key] = cb
            ui_row.addWidget(cb)
        ui_row.addStretch()
        ui_layout.addLayout(ui_row)
        layout.addWidget(ui_group)

        # ==== 3. 导出设置 ====
        export_group = QGroupBox("导出设置")
        export_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; color: #2c3e50; border: 1px solid #dee2e6; border-radius: 8px; margin-top: 12px; padding: 16px 12px 10px 12px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }")
        export_layout = QHBoxLayout(export_group)
        export_layout.setSpacing(10)
        export_layout.addWidget(QLabel("默认导出目录:"))
        self.export_dir_edit = QLineEdit()
        self.export_dir_edit.setPlaceholderText("留空则每次手动选择...")
        self.export_dir_edit.setText(self.app_settings.get("export_default_dir", ""))
        self.export_dir_edit.textChanged.connect(self._on_settings_changed)
        self.export_dir_edit.setStyleSheet("padding: 6px 10px; border: 1px solid #ced4da; border-radius: 4px; font-size: 13px;")
        export_layout.addWidget(self.export_dir_edit, stretch=1)
        self.export_dir_btn = QPushButton("选择...")
        self.export_dir_btn.setFixedWidth(80)
        self.export_dir_btn.setStyleSheet("padding: 6px 12px; background: #6c757d; color: white; border: none; border-radius: 4px; font-size: 13px;")
        self.export_dir_btn.clicked.connect(self._on_select_export_dir)
        export_layout.addWidget(self.export_dir_btn)
        layout.addWidget(export_group)

        # ==== 4. 数据库管理 ====
        db_group = QGroupBox("数据库管理")
        db_group.setStyleSheet("QGroupBox { font-weight: bold; font-size: 14px; color: #2c3e50; border: 1px solid #dee2e6; border-radius: 8px; margin-top: 12px; padding: 16px 12px 10px 12px; } QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; }")
        db_layout = QVBoxLayout(db_group)
        self.db_list_widget = QListWidget()
        self.db_list_widget.setMaximumHeight(120)
        self.db_list_widget.setStyleSheet("QListWidget { border: 1px solid #ced4da; border-radius: 4px; font-size: 12px; }")
        self._refresh_db_list()
        db_layout.addWidget(self.db_list_widget)
        db_btn_layout = QHBoxLayout()
        db_btn_layout.setSpacing(8)
        self.add_db_btn = QPushButton("+ 添加数据库")
        self.add_db_btn.setStyleSheet("padding: 6px 14px; background: #0d6efd; color: white; border: none; border-radius: 4px; font-size: 13px;")
        self.add_db_btn.clicked.connect(self._on_add_db_from_settings)
        db_btn_layout.addWidget(self.add_db_btn)
        self.del_db_btn = QPushButton("删除选中")
        self.del_db_btn.setStyleSheet("padding: 6px 14px; background: #dc3545; color: white; border: none; border-radius: 4px; font-size: 13px;")
        self.del_db_btn.clicked.connect(self._on_del_db_from_settings)
        db_btn_layout.addWidget(self.del_db_btn)
        db_btn_layout.addStretch()
        db_layout.addLayout(db_btn_layout)
        layout.addWidget(db_group)

        layout.addStretch()
        scroll.setWidget(widget)

        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.addWidget(scroll)
        return page

    # ========== 首页按钮：跳转到预测页 ==========
    def _open_dimension(self, key: str):
        """从首页点击维度卡片后，跳转到预测页并设置维度"""
        if self.engine is None:
            QMessageBox.warning(self, "\u63d0\u793a", "\u8bf7\u5148\u52a0\u8f7d\u6570\u636e\u5e93\u3002")
            return
        self.current_dimension = key
        dim_info = self.engine.DIMENSIONS.get(key, self.engine.DIMENSIONS.get('model', {}))
        label = dim_info.get('label', key)
        self.dim_title.setText(f"🔮 动销预测率 — {label}")
        self.content_stack.setCurrentIndex(1)
        self.statusBar().showMessage(f"已切换至: {label} 维度")

    # ========== 设置页：事件处理 ==========
    def _on_settings_changed(self):
        """设置项变更时自动保存（不含算法，算法需点击'应用'按钮）"""
        # 算法不在此处保存，由 _on_apply_algorithms 处理
        visible = {k: cb.isChecked() for k, cb in self.ui_checkboxes.items()}
        export_dir = self.export_dir_edit.text().strip()
        self.app_settings["visible_elements"] = visible
        self.app_settings["export_default_dir"] = export_dir
        save_app_settings(self.app_settings)

    def _on_apply_algorithms(self):
        """应用算法设置——保存勾选的算法并提示"""
        enabled = [a for a, cb in self.algo_checkboxes.items() if cb.isChecked()]
        self.app_settings["enabled_algorithms"] = enabled
        save_app_settings(self.app_settings)
        names = ", ".join(enabled)
        self.statusBar().showMessage(f"算法设置已应用：{names}（共 {len(enabled)} 种）")
        QMessageBox.information(self, "算法设置",
            f"以下 {len(enabled)} 种算法将在后续预测中使用：\n\n{names}\n\n"
            "提示：Naive（朴素预测）为兜底算法，建议保持勾选。")

    def _on_apply_optimization(self):
        """应用运行优化配置"""
        self.app_settings["forecast_range_limit"] = self.opt_forecast_range.isChecked()
        self.app_settings["auto_downgrade"] = self.opt_auto_downgrade.isChecked()
        self.app_settings["table_row_limit"] = self.opt_table_row_limit.isChecked()
        save_app_settings(self.app_settings)

        parts = []
        parts.append("✓" if self.opt_forecast_range.isChecked() else "✗")
        parts.append(" 预测时间限制")
        parts.append("✓" if self.opt_auto_downgrade.isChecked() else "✗")
        parts.append(" 自动降速")
        parts.append("✓" if self.opt_table_row_limit.isChecked() else "✗")
        parts.append(" 展示上限")
        self.statusBar().showMessage(f"运行优化已应用：" + " | ".join(parts))

        QMessageBox.information(self, "运行优化",
            f"配置已生效：\n\n"
            f"• 预测时间限制（24个月）：{'启用' if self.opt_forecast_range.isChecked() else '关闭'}\n"
            f"• 重型算法自动降速：{'启用' if self.opt_auto_downgrade.isChecked() else '关闭'}\n"
            f"• 展示上限 500 行：{'启用' if self.opt_table_row_limit.isChecked() else '关闭'}\n\n"
            "提示：关闭限制可能导致程序响应变慢，建议谨慎操作。")

    def _on_select_export_dir(self):
        """选择默认导出目录"""
        d = QFileDialog.getExistingDirectory(
            self, "选择默认导出目录",
            self.export_dir_edit.text() or os.path.expanduser("~"),
            QFileDialog.ShowDirsOnly
        )
        if d:
            self.export_dir_edit.setText(d)
            self.app_settings["export_default_dir"] = d
            save_app_settings(self.app_settings)

    def _on_add_db_from_settings(self):
        """从设置页添加数据库"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据库",
            os.path.expanduser("~"),
            "SQLite 数据库 (*.sqlite *.db);;所有文件 (*)",
        )
        if path and os.path.exists(path):
            self.db_manager.add(path)
            self._refresh_db_list()

    def _on_del_db_from_settings(self):
        """从设置页删除选中的数据库"""
        item = self.db_list_widget.currentItem()
        if not item:
            QMessageBox.information(self, "提示", "请先选择要删除的数据库地址。")
            return
        path = item.data(Qt.UserRole)
        if path == self.db_path:
            QMessageBox.warning(self, "提示", "不能删除当前正在使用的数据库。")
            return
        self.db_manager.remove(path)
        self._refresh_db_list()

    def _refresh_db_list(self):
        """刷新设置页的数据库列表"""
        if not hasattr(self, 'db_list_widget'):
            return
        self.db_list_widget.clear()
        for p in self.db_manager.list_paths():
            item = QListWidgetItem(p)
            item.setData(Qt.UserRole, p)
            if p == self.db_path:
                item.setText(f"{p}  (当前)")
            self.db_list_widget.addItem(item)

    # ========== QComboBox 下拉弹窗样式修复 ==========
    def _fix_combo_popup_frames(self):
        """设置下拉列表滚动条为浅色主题，与右侧内容区统一"""
        scrollbar_style = """
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
        """
        combos = [self.combo_category, self.combo_subcategory, self.combo_model,
                  self.combo_month_start, self.combo_month_end,
                  self.combo_year_start, self.combo_year_end]
        for combo in combos:
            view = combo.view()
            if view:
                # 不修改 frame，完全依靠 CSS 控制边框样式
                view.verticalScrollBar().setStyleSheet(scrollbar_style)

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
            years = self.data_loader.get_years()

            # 品类
            self.combo_category.blockSignals(True)
            self.combo_category.clear()
            self.combo_category.addItem('\u5168\u90e8')
            self.combo_category.addItems(categories)
            self.combo_category.blockSignals(False)

            # 细分类（初始加载全部，后续按品类联动过滤）
            self.combo_subcategory.clear()
            self.combo_subcategory.addItem('\u5168\u90e8')
            self.combo_subcategory.addItems(subcategories)

            # 型号（填入下拉列表 + 历史记录）
            self._populate_model_combo(models)

            period_list = self.dimensions_data.get('periods', [])

            # ---- 设置数据时间边界 ----
            if period_list:
                first = period_list[0]
                last = period_list[-1]
                self.data_min_year = int(first[:4])
                self.data_max_year = int(last[:4])
                self.data_min_month = int(first[4:6])
                self.data_max_month = int(last[4:6])
                self.statusBar().showMessage(
                    f"\u6570\u636e\u52a0\u8f7e\u5b8c\u6210 | \u8d26\u671f\u8303\u56f4: {period_list[0]} ~ {period_list[-1]}"
                )
            else:
                self.data_min_year = 2018; self.data_max_year = 2026
                self.data_min_month = 1; self.data_max_month = 12

            # 更新预测时间选择器
            self.all_years = years  # 保留参考用
            self._filter_time_combos()
            # 默认选中数据最后一个月的当年
            idx = self.combo_year_start.findText(str(self.data_max_year))
            if idx >= 0:
                self.combo_year_start.setCurrentIndex(idx)
                self.combo_year_end.setCurrentIndex(idx)
            self.combo_month_start.blockSignals(True)
            self.combo_month_end.blockSignals(True)
            self.combo_month_start.setCurrentIndex(self.data_max_month - 1)
            self.combo_month_end.setCurrentIndex(self.data_max_month - 1)
            self.combo_month_start.blockSignals(False)
            self.combo_month_end.blockSignals(False)
            self._filter_time_combos()
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

    # ========== 数据库切换 ==========
    def _init_with_db(self):
        """用当前 db_path 初始化数据引擎"""
        if not self.db_path or not os.path.exists(self.db_path):
            return
        self.db_manager.add(self.db_path)
        self.data_loader = DataLoader(self.db_path)
        self.engine = ForecastEngine(self.data_loader)
        self.label_db_path.setText(self.db_path)
        self._load_initial_data()

    def prompt_select_database(self):
        """弹出文件对话框让用户选择数据库（UI 启动后调用）"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 OMS 销售数据库",
            os.path.expanduser("~"),
            "SQLite 数据库 (*.sqlite *.db);;所有文件 (*)",
        )
        if path and os.path.exists(path):
            self.db_path = path
            self._init_with_db()
        else:
            self.statusBar().showMessage("未选择数据库，请点击「更换」按钮加载数据")

    def _on_switch_db(self):
        """切换数据库"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择数据库文件",
            os.path.dirname(self.db_path) if self.db_path else os.path.expanduser("~"),
            "SQLite 数据库 (*.sqlite *.db);;所有文件 (*)",
        )
        if not path or not os.path.exists(path):
            return

        self.db_path = path
        self._init_with_db()
        self.table_model.clear()

    def _reload_with_db(self, new_db_path: str):
        """用新数据库路径重新加载整个应用"""
        self.db_path = new_db_path
        self._init_with_db()
        self.table_model.clear()

    # ========== 页面创建：数据质量评估 ==========
    def _create_quality_page(self):
        """创建数据质量评估页面"""
        page = QWidget()
        page.setObjectName("quality_page")
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        inner = QWidget()
        inner.setObjectName("quality_inner")
        inner.setStyleSheet("QWidget#quality_inner { background-color: #f5f5f7; }")
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        title = QLabel("\U0001f4ca \u6570\u636e\u8d28\u91cf\u8bc4\u4f30")
        title.setObjectName("quality_title")
        title.setStyleSheet("font-size: 22px; font-weight: bold; color: #333;")
        layout.addWidget(title)

        desc = QLabel("\u57fa\u4e8e OMS \u6570\u636e\u5e93\u5168\u91cf\u626b\u63cf\uff0c\u8bc4\u4f30\u6570\u636e\u53ef\u9884\u6d4b\u6027\u3001\u5b8c\u6574\u6027\u53ca\u6f5c\u5728\u95ee\u9898")
        desc.setStyleSheet("font-size: 13px; color: #888; margin-bottom: 4px;")
        layout.addWidget(desc)

        # ── 概览卡片容器 ──
        self.q_overview_container = QWidget()
        self.q_overview_layout = QHBoxLayout(self.q_overview_container)
        self.q_overview_layout.setContentsMargins(0, 0, 0, 0)
        self.q_overview_layout.setSpacing(12)
        layout.addWidget(self.q_overview_container)

        # ── 数据完整性 ──
        self.q_completeness = QGroupBox("\u25ce \u6570\u636e\u5b8c\u6574\u6027")
        self.q_completeness_layout = QVBoxLayout(self.q_completeness)
        self.q_completeness_layout.setSpacing(6)
        layout.addWidget(self.q_completeness)

        # ── 可预测性 ──
        self.q_predictability = QGroupBox("\u25ce \u53ef\u9884\u6d4b\u6027\u8bc4\u4f30")
        self.q_predictability_layout = QVBoxLayout(self.q_predictability)
        self.q_predictability_layout.setSpacing(6)
        layout.addWidget(self.q_predictability)

        # ── 潜在问题 ──
        self.q_issues = QGroupBox("\u25ce \u6f5c\u5728\u95ee\u9898\u4e0e\u8b66\u544a")
        self.q_issues_layout = QVBoxLayout(self.q_issues)
        self.q_issues_layout.setSpacing(4)
        layout.addWidget(self.q_issues)

        layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

        # 初始占位
        placeholder = QLabel("\u8bf7\u52a0\u8f7d\u6570\u636e\u5e93\u540e\u67e5\u770b\u8bc4\u4f30\u7ed3\u679c")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("color: #aaa; font-size: 16px; padding: 60px;")
        self.q_completeness_layout.addWidget(placeholder)

        return page

    def _refresh_quality_page(self):
        """刷新数据质量评估页面内容（后台线程）"""
        if not self.data_loader:
            return
        # 清空旧内容
        for layout in [self.q_overview_layout, self.q_completeness_layout,
                       self.q_predictability_layout, self.q_issues_layout]:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
                elif item.layout():
                    self._clear_layout(item.layout())

        # 显示加载中
        loading = QLabel("\u23f3 \u6b63\u5728\u5206\u6790\u6570\u636e\u5e93\uff0c\u8bf7\u7a0d\u5019...")
        loading.setAlignment(Qt.AlignCenter)
        loading.setStyleSheet("color: #888; font-size: 14px; padding: 40px;")
        self.q_completeness_layout.addWidget(loading)

        self.loading_overlay.show()
        QApplication.processEvents()

        # 启动后台线程
        self._quality_worker = QualityWorker(self.data_loader, self.db_path)
        self._quality_worker.finished_data.connect(self._on_quality_data_ready)
        self._quality_worker.finished_error.connect(self._on_quality_data_error)
        self._quality_worker.start()

    def _on_quality_data_ready(self, data):
        """后台数据采集完成，渲染页面"""
        self.loading_overlay.hide()
        # 再次清空（移除加载提示）
        for layout in [self.q_overview_layout, self.q_completeness_layout,
                       self.q_predictability_layout, self.q_issues_layout]:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()

        if not data:
            return

        # ── 概览卡片 ──
        cards_data = [
            ("\u603b\u8bb0\u5f55\u6570", f"{data['total_rows']:,}", "\u6761\u9500\u552e\u8bb0\u5f55"),
            ("\u65f6\u95f4\u8303\u56f4", f"{data['period_min']} ~ {data['period_max']}",
             f"\u5171 {data['total_months']} \u4e2a\u6708"),
            ("\u54c1\u7c7b\u6570", str(data['category_count']), "\u4e2a\u5927\u7c7b"),
            ("\u578b\u53f7\u6570", str(data['model_count']), "\u4e2a\u578b\u53f7"),
            ("\u6570\u636e\u5e93\u6587\u4ef6", data['db_size_mb'], "MB"),
        ]
        for label, value, unit in cards_data:
            card = QFrame()
            card.setStyleSheet(
                "QFrame { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 12px; }"
            )
            cl = QVBoxLayout(card)
            cl.setSpacing(4)
            lbl = QLabel(label)
            lbl.setStyleSheet("font-size: 12px; color: #888;")
            cl.addWidget(lbl)
            val = QLabel(value)
            val.setStyleSheet("font-size: 22px; font-weight: bold; color: #333;")
            cl.addWidget(val)
            unt = QLabel(unit)
            unt.setStyleSheet("font-size: 11px; color: #aaa;")
            cl.addWidget(unt)
            self.q_overview_layout.addWidget(card)

        # ── 数据完整性 ──
        comp = data['completeness']
        summary = QLabel(
            f"\u2714 \u5b8c\u6574\u7ec4\uff1a{comp['full_groups']} \u4e2a  |  "
            f"\u26a0 \u6709\u7f3a\u5931\u7ec4\uff1a{comp['missing_groups']} \u4e2a  |  "
            f"\u274c \u65e0\u6570\u636e\u7ec4\uff1a{comp['empty_groups']} \u4e2a  |  "
            f"\u5e73\u5747\u8986\u76d6\u7387\uff1a{comp['avg_coverage']:.1f}%"
        )
        summary.setStyleSheet("font-size: 13px; color: #555; padding: 4px 0;")
        self.q_completeness_layout.addWidget(summary)

        if comp['missing_group_details']:
            detail = QLabel("\u4e3b\u8981\u7f3a\u5931\u7ec4\uff1a" + "  \u3000".join(
                f"{g['name']}(\u7f3a{g['missing']}\u6708)" for g in comp['missing_group_details'][:8]
            ))
            detail.setWordWrap(True)
            detail.setStyleSheet("font-size: 11px; color: #999;")
            self.q_completeness_layout.addWidget(detail)

        # ── 可预测性评估 ──
        pred = data['predictability']
        pred_summary = QLabel(
            f"\U0001f7e2 \u6613\u9884\u6d4b\uff1a{pred['easy']} \u7ec4  |  "
            f"\U0001f7e1 \u4e2d\u7b49\uff1a{pred['medium']} \u7ec4  |  "
            f"\U0001f534 \u56f0\u96be\uff1a{pred['hard']} \u7ec4  |  "
            f"\u26a0 \u6570\u636e\u4e0d\u8db3\uff1a{pred['insufficient']} \u7ec4"
        )
        pred_summary.setStyleSheet("font-size: 13px; color: #555; padding: 4px 0;")
        self.q_predictability_layout.addWidget(pred_summary)

        desc_text = (
            "\u8bc4\u4f30\u6807\u51c6\uff1a\u6613\u9884\u6d4b = CV<0.5 \u4e14\u96f6\u503c\u6bd4<30%  |  "
            "\u4e2d\u7b49 = CV<1.0 \u4e14\u96f6\u503c\u6bd4<60%  |  "
            "\u56f0\u96be = \u5176\u4ed6  |  \u6570\u636e\u4e0d\u8db3 = \u6709\u6548\u67082\u4e2a\u6708"
        )
        desc_lbl = QLabel(desc_text)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet("font-size: 11px; color: #aaa;")
        self.q_predictability_layout.addWidget(desc_lbl)

        # ── 潜在问题 ──
        issues = data['issues']
        if issues:
            for issue in issues:
                icon_map = {'error': '\u274c', 'warning': '\u26a0\ufe0f', 'info': '\u2139\ufe0f'}
                color_map = {'error': '#dc3545', 'warning': '#fd7e14', 'info': '#0d6efd'}
                icon = icon_map.get(issue['level'], '\u26a0\ufe0f')
                color = color_map.get(issue['level'], '#fd7e14')
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 2, 0, 2)
                rl.setSpacing(8)
                icon_lbl = QLabel(f"{icon} {issue['title']}")
                icon_lbl.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color};")
                rl.addWidget(icon_lbl)
                if issue.get('detail'):
                    det = QLabel(issue['detail'])
                    det.setStyleSheet("font-size: 12px; color: #777;")
                    rl.addWidget(det)
                rl.addStretch()
                self.q_issues_layout.addWidget(row)
        else:
            ok = QLabel("\u2705 \u672a\u68c0\u6d4b\u5230\u660e\u663e\u95ee\u9898\uff0c\u6570\u636e\u8d28\u91cf\u826f\u597d")
            ok.setStyleSheet("font-size: 13px; color: #198754; padding: 4px;")
            self.q_issues_layout.addWidget(ok)

    def _on_quality_data_error(self, msg):
        """后台采集出错"""
        self.loading_overlay.hide()
        for layout in [self.q_overview_layout, self.q_completeness_layout,
                       self.q_predictability_layout, self.q_issues_layout]:
            while layout.count():
                item = layout.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
        err = QLabel(f"\u26a0\ufe0f {msg}")
        err.setStyleSheet("color: #dc3545; padding: 10px;")
        self.q_completeness_layout.addWidget(err)

    def _clear_layout(self, layout):
        """递归清空 layout"""
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                self._clear_layout(item.layout())

    # ========== 导航/维度切换 ==========
    def _on_nav_clicked(self, item):
        """左侧导航点击事件 —— 首页 / 数据质量评估 / 功能设置"""
        dim_key = item.data(Qt.UserRole)
        if dim_key == 'home':
            self.content_stack.setCurrentIndex(0)
            self.statusBar().showMessage("\U0001f3e0 \u9996\u9875")
            return

        if dim_key == 'quality':
            self._refresh_quality_page()
            self.content_stack.setCurrentIndex(2)
            self.statusBar().showMessage("\U0001f4ca \u6570\u636e\u8d28\u91cf\u8bc4\u4f30")
            return

        if dim_key == 'settings':
            self._refresh_db_list()
            self.content_stack.setCurrentIndex(3)
            self.statusBar().showMessage("\u2699 \u529f\u80fd\u8bbe\u7f6e")
            return

    # ========== 帮助及常见问题 ==========
    def _populate_faq_list(self, filter_text: str = ""):
        """根据搜索关键词填充 FAQ 列表，优先匹配相似问题"""
        self.faq_list.clear()
        filter_text = filter_text.strip().lower()

        if not filter_text:
            # 无搜索时按顺序显示
            scored = [(i, 0, q, a) for i, (q, a) in enumerate(FAQ_DATA)]
        else:
            scored = []
            for i, (q, a) in enumerate(FAQ_DATA):
                q_lower = q.lower()
                # 相似度评分（越小越匹配）
                if filter_text == q_lower:
                    score = 0  # 完全匹配
                elif filter_text in q_lower:
                    score = 1  # 子串匹配
                elif self._is_subsequence(filter_text, q_lower):
                    score = 2  # 字符子序列匹配
                elif self._is_subsequence(filter_text, a.lower()):
                    score = 3  # 在答案中子序列匹配
                else:
                    score = 99  # 不匹配
                if score < 99:
                    scored.append((i, score, q, a))
            # 按评分排序（匹配度优先），再按原序号
            scored.sort(key=lambda x: (x[1], x[0]))

        for idx, score, question, answer in scored:
            display = f"{idx + 1}. {question[:20]}{'...' if len(question) > 20 else ''}"
            item = QListWidgetItem(display)
            item.setData(Qt.UserRole, question)
            item.setToolTip(question)
            self.faq_list.addItem(item)

    @staticmethod
    def _is_subsequence(s: str, t: str) -> bool:
        """判断 s 是否是 t 的子序列（保持顺序）"""
        it = iter(t)
        return all(c in it for c in s)

    def _on_faq_search_changed(self, text: str):
        """搜索框文本变化时刷新 FAQ 列表"""
        self._populate_faq_list(text)

    def _on_faq_clicked(self, item):
        """点击 FAQ 问题，弹出详情弹窗"""
        question = item.data(Qt.UserRole)
        # 查找对应答案
        answer = None
        for q, a in FAQ_DATA:
            if q == question:
                answer = a
                break
        if answer is None:
            return

        dlg = FaqDialog(self, question, answer, self.faq_list)
        dlg.show()

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

    # ========== 品类-细分类联动 ==========
    def _on_category_changed(self, text: str):
        """当品类选择变化时，更新细分类下拉列表"""
        if not self.data_loader:
            return
        self.combo_subcategory.blockSignals(True)
        self.combo_subcategory.clear()
        self.combo_subcategory.addItem('全部')
        subcats = self.data_loader.get_subcategories_by_category(text if text != '全部' else None)
        self.combo_subcategory.addItems(subcats)
        self.combo_subcategory.blockSignals(False)

    # ========== 预测时间区间处理 ==========
    def _get_month_num(self, combo) -> int:
        """从月份下拉框读取数值"""
        text = combo.currentText().strip()
        m = re.search(r'(\d+)', text)
        return max(1, min(12, int(m.group(1)))) if m else 1

    def _parse_ym(self, year_combo, month_combo) -> tuple:
        """读取年+月，返回 (year, month) 元组（combos 未初始化时返回默认值）"""
        yt = year_combo.currentText().strip()
        y = int(yt) if yt else 2026
        m = self._get_month_num(month_combo)
        return y, m

    def _on_time_range_changed(self, *args):
        """时间范围变化时更新过滤和约束"""
        # 年份控件尚未填充时跳过
        if not self.combo_year_start.currentText().strip():
            return
        self._filter_time_combos()

    def _filter_time_combos(self):
        """根据当前选择和联动约束，动态过滤年/月下拉列表"""
        # 保存当前选择（用文本精确还原，避免重建后索引错位）
        sel_start_y = self.combo_year_start.currentText().strip()
        sel_start_m_text = self.combo_month_start.currentText()
        sel_end_y = self.combo_year_end.currentText().strip()
        sel_end_m_text = self.combo_month_end.currentText()

        sy = int(sel_start_y) if sel_start_y else self.data_max_year

        # 年份选择范围：起始不低于 DB 最早年，结束向后扩展
        min_year = self.data_min_year
        max_year = self.data_max_year + YEAR_RANGE_FUTURE_MARGIN
        all_selectable_years = list(range(min_year, max_year + 1))

        self.combo_year_start.blockSignals(True)
        self.combo_month_start.blockSignals(True)
        self.combo_year_end.blockSignals(True)
        self.combo_month_end.blockSignals(True)

        # ---- 起始年（不受 DB 限制） ----
        self.combo_year_start.clear()
        self.combo_year_start.addItems([str(y) for y in all_selectable_years])

        # ---- 起始月（≥ DB 最早月，同年时；始终可用 1~12 月） ----
        self.combo_month_start.clear()
        sm_min = self.data_min_month if sy == self.data_min_year else 1
        for m in range(sm_min, 13):
            self.combo_month_start.addItem(f"{m}\u6708")
        if sel_start_m_text:
            idx = self.combo_month_start.findText(sel_start_m_text)
            self.combo_month_start.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            self.combo_month_start.setCurrentIndex(0)

        # ---- 结束年（>= 起始年） ----
        self.combo_year_end.clear()
        self.combo_year_end.addItems([str(y) for y in all_selectable_years if y >= sy])

        # ---- 结束月（>= 起始月当同年；不同年 1~12） ----
        # 注意：addItems 后 currentText 可能变成第一项，必须用保存的 sel_end_y
        ey = int(sel_end_y) if sel_end_y else sy
        self.combo_month_end.clear()
        em_min = 1
        if sy == ey:
            sm_now = self.combo_month_start.currentIndex() + 1
            em_min = max(em_min, sm_now)
        for m in range(em_min, 13):
            self.combo_month_end.addItem(f"{m}\u6708")
        if sel_end_m_text:
            idx = self.combo_month_end.findText(sel_end_m_text)
            self.combo_month_end.setCurrentIndex(idx if idx >= 0 else self.combo_month_end.count() - 1)
        else:
            self.combo_month_end.setCurrentIndex(self.combo_month_end.count() - 1)

        # 恢复年份选择
        idx_sy = self.combo_year_start.findText(sel_start_y)
        if idx_sy >= 0:
            self.combo_year_start.setCurrentIndex(idx_sy)
        idx_ey = self.combo_year_end.findText(sel_end_y)
        if idx_ey >= 0:
            self.combo_year_end.setCurrentIndex(idx_ey)

        self.combo_year_start.blockSignals(False)
        self.combo_month_start.blockSignals(False)
        self.combo_year_end.blockSignals(False)
        self.combo_month_end.blockSignals(False)

    def _get_forecast_months(self) -> int:
        """根据起止年/月计算预测月数"""
        sy, sm = self._parse_ym(self.combo_year_start, self.combo_month_start)
        ey, em = self._parse_ym(self.combo_year_end, self.combo_month_end)
        total = (ey - sy) * 12 + (em - sm) + 1
        return max(1, total)


    # ========== 搜索/预测 ==========
    def _on_search(self):
        """执行搜索和预测（启动后台线程）"""
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(1000)

        if not self.engine or not self.data_loader:
            QMessageBox.warning(self, "\u63d0\u793a", "\u8bf7\u5148\u9009\u62e9\u6570\u636e\u5e93\u6587\u4ef6")
            self.prompt_select_database()
            return

        if not self.current_dimension:
            QMessageBox.information(self, "\u63d0\u793a", "\u8bf7\u5148\u5728\u5de6\u4fa7\u5bfc\u822a\u680f\u9009\u62e9\u4e00\u4e2a\u9884\u6d4a\u7ef4\u5ea6")
            return

        channel = self._get_selected_channel()
        category = self.combo_category.currentText() if self.combo_category.currentIndex() > 0 else None
        subcat = self.combo_subcategory.currentText() if self.combo_subcategory.currentIndex() > 0 else None
        model_kw = self.combo_model.currentText().strip() or None
        months = self._get_forecast_months()

        # 从 UI 组合框计算用户选择的目标期区间
        sy, sm = self._parse_ym(self.combo_year_start, self.combo_month_start)
        ey, em = self._parse_ym(self.combo_year_end, self.combo_month_end)

        # 预测区间上限保护：超过 24 个月会导致拟合失准 + 表格渲染内存爆炸
        MAX_FORECAST_RANGE = 24
        if self.app_settings.get("forecast_range_limit", True) and months > MAX_FORECAST_RANGE:
            QMessageBox.warning(
                self, "预测区间过长",
                f"当前选择的预测范围为 {months} 个月，超过上限 {MAX_FORECAST_RANGE} 个月。\n\n"
                "预测区间过大会导致：\n"
                "1. 算法拟合失真（短序列预测长步长无意义）\n"
                "2. 表格渲染内存溢出（列数 × 行数 过大）\n\n"
                "请缩小预测时间范围后重试。"
            )
            return
        start_period = f"{sy}{sm:02d}"
        end_period = f"{ey}{em:02d}"

        # 起始月训练数据保护：必须留出最少训练窗口，否则算法无数据可用
        MIN_TRAIN_MONTHS = 6
        data_min_period = f"{self.data_min_year}{self.data_min_month:02d}"
        min_start_period = self.engine._period_add(data_min_period, MIN_TRAIN_MONTHS)
        if int(start_period) < int(min_start_period):
            QMessageBox.warning(
                self, "预测起始时间过早",
                f"数据库最早数据为 {data_min_period}，"
                f"预测起始月至少需要留出 {MIN_TRAIN_MONTHS} 个月作为训练数据（即不早于 {min_start_period}）。\n\n"
                f"当前选择 {start_period} 会导致训练数据为空或不足，请调整起始时间。"
            )
            return

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
        start_ym = f"{self.combo_year_start.currentText()}\u5e74{self.combo_month_start.currentText()}"
        end_ym = f"{self.combo_year_end.currentText()}\u5e74{self.combo_month_end.currentText()}"
        self.loading_overlay.set_sub_text(
            f"\u7ef4\u5ea6: {dim_label}  |  \u9884\u6d4b: {start_ym} \u81f3 {end_ym}  |  \u6e20\u9053: {ch_display}"
        )
        self.loading_overlay.set_estimated_time(est_time)
        self.loading_overlay.set_progress(5)
        self.loading_overlay.show()
        self.loading_overlay.raise_()

        self.btn_search.setEnabled(False)
        self.statusBar().showMessage("\u6b63\u5728\u540e\u53f0\u9884\u6d4a\uff0c\u754c\u9762\u53ef\u81ea\u7531\u64cd\u4f5c...")

        # 启动后台线程
        # 读取用户勾选的算法
        enabled_algos = self.app_settings.get("enabled_algorithms",
                                               DEFAULT_SETTINGS["enabled_algorithms"])
        self.worker = ForecastWorker(
            engine=self.engine,
            dimension=self.current_dimension,
            months=months,
            ch=channel, cat=category, subcat=subcat, model_kw=model_kw,
            start_period=start_period,
            end_period=end_period,
            algorithm_filter=enabled_algos if enabled_algos != DEFAULT_SETTINGS["enabled_algorithms"] else None,
            auto_downgrade=self.app_settings.get("auto_downgrade", True),
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
        """将预测结果填充到表格（虚拟化模型，按需查询）"""
        self.table.setUpdatesEnabled(False)

        if df is None or df.empty or len(df) == 0:
            self.table_model.clear()
            self.table.setUpdatesEnabled(True)
            return

        # ---- 去掉所有值均为空的列 ----
        cols_to_keep = []
        for col in df.columns:
            if len(df) <= 1:
                cols_to_keep.append(col)
            else:
                data_vals = df[col].iloc[1:].dropna()
                data_vals = data_vals[data_vals != '']
                if len(data_vals) > 0:
                    cols_to_keep.append(col)
        df = df[cols_to_keep].copy()

        # ---- 数据行按状态排序 ----
        if len(df) > 1 and '数据状态' in df.columns:
            status_order = {'良好': 0, '一般': 1, '偏低': 2, '质量差': 3, '充足': 4, '不足': 5, '严重不足': 6}
            df_data = df.iloc[1:].copy()
            df_data['_sort_key'] = df_data['数据状态'].map(status_order).fillna(9)
            df_data = df_data.sort_values('_sort_key')
            df_data = df_data.drop(columns=['_sort_key'])
            df = pd.concat([df.iloc[[0]], df_data], ignore_index=True)

        # ---- 行数上限 ----
        original_rows = len(df)
        truncated = False
        if self.app_settings.get("table_row_limit", True) and original_rows > MAX_TABLE_ROWS:
            truncated = True
            df = pd.concat([df.iloc[[0]], df.iloc[1:MAX_TABLE_ROWS]], ignore_index=True)
            hint_row = pd.Series({col: '' for col in df.columns})
            hint_row[df.columns[0]] = f'... 还有 {original_rows - MAX_TABLE_ROWS} 条记录未显示，请导出查看完整数据 ...'
            df = pd.concat([df, pd.DataFrame([hint_row])], ignore_index=True)

        # ---- 计算固定列数 ----
        columns = list(df.columns)
        fixed_cols = 0
        for col in columns:
            if col and str(col)[0].isdigit():
                break
            fixed_cols += 1

        # ---- 设置模型数据（虚拟化，0 个对象创建） ----
        self.table_model.set_dataframe(df, fixed_cols, truncated)

        # ---- 列宽 ----
        col_widths = {'渠道': 55, '型号': 130, '细分类': 90, '品类': 90, '大类': 90,
                      '预测算法': 65, '准确率': 70, '数据状态': 65}
        for ci, col in enumerate(columns):
            w = col_widths.get(col, 190)
            if ci >= fixed_cols:
                w = max(w, 170)
            self.table.setColumnWidth(ci, w)

        self.table.setUpdatesEnabled(True)

        if truncated:
            self.statusBar().showMessage(
                f"预测完成 | 仅显示前 {MAX_TABLE_ROWS-1} 条数据，"
                f"完整 {original_rows-1} 条请导出查看"
            )

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
        # 品类变更后自动触发 _on_category_changed 刷新细分类
        self.combo_subcategory.setCurrentIndex(0)
        self.combo_model.setCurrentText('')
        self.combo_model.lineEdit().setPlaceholderText("\u8f93\u5165/\u9009\u62e9\u578b\u53f7\u5173\u952e\u8bcd...")

        # 重置预测时间到默认
        if self.combo_year_start.count() > 0:
            last_idx = self.combo_year_start.count() - 1
            self.combo_year_start.setCurrentIndex(last_idx)
            self.combo_year_end.setCurrentIndex(last_idx)
        self.combo_month_start.setCurrentIndex(0)
        self.combo_month_end.setCurrentIndex(4)

        self.table_model.clear()
        self.current_result_df = None
        self.statusBar().showMessage("\u5df2\u91cd\u7f6e\u7b5b\u9009\u6761\u4ef6")

    # ========== 导出 ==========
    def _on_export(self):
        """导出为 CSV"""
        if self.current_result_df is None or self.current_result_df.empty:
            QMessageBox.information(self, "\u63d0\u793a", "\u6682\u65e0\u6570\u636e\u53ef\u5bfc\u51fa\uff0c\u8bf7\u5148\u6267\u884c\u641c\u7d22\u3002")
            return

        # 使用默认导出目录（如果设置了的话）
        default_dir = self.app_settings.get("export_default_dir", "")
        default_name = f"\u52a8\u9500\u9884\u6d4b_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        if default_dir and os.path.isdir(default_dir):
            default_path = os.path.join(default_dir, default_name)
        else:
            default_path = default_name

        path, _ = QFileDialog.getSaveFileName(
            self, "\u5bfc\u51fa\u9884\u6d4b\u7ed3\u679c",
            default_path,
            "CSV \u6587\u4ef6 (*.csv);;\u6240\u6709\u6587\u4ef6 (*)",
        )
        if not path:
            return

        try:
            # 将元组列转为可读格式再导出
            export_df = self.current_result_df.copy()
            for col in export_df.columns:
                if export_df[col].apply(lambda x: isinstance(x, tuple)).any():
                    export_df[col] = export_df[col].apply(
                        lambda x: f"{int(x[0])}/{x[1]}/{x[2]:.2f}%" if isinstance(x, tuple) and x[0] is not None and len(x) >= 3 and x[2] is not None
                        else f"{int(x[0])}/{x[1]}" if isinstance(x, tuple) and x[0] is not None
                        else f"{x[1]}" if isinstance(x, tuple)
                        else x
                    )
            export_df.to_csv(path, index=False, encoding='utf-8-sig')
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
        try:
            current_model = self.combo_model.currentText().strip()
            if current_model:
                self.model_history.add(current_model)
        except Exception:
            pass
        if self.data_loader:
            self.data_loader.close()
        super().closeEvent(event)


# ============================================================
#  入口
# ============================================================

def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    # 命令行参数优先
    db_path = None
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        db_path = sys.argv[1]
    else:
        # 尝试历史记录
        mgr = DbPathManager()
        default = mgr.get_default()
        if default and os.path.exists(default):
            db_path = default

    window = SalesForecastWindow(db_path)
    window.show()

    # 如果没有有效数据库，启动后立即弹出选择对话框
    if not db_path or not os.path.exists(db_path):
        window.prompt_select_database()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
