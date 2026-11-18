# SPDX-License-Identifier: MIT
# sector_model.py — 板块 ML 模型生成器
#
# 对应 AlphaSignalGenerator 的板块级版本。
# 使用 GBDT/XGBoost 预测 31 个板块的 2 周前向排名。
# 模型配置通过 sector_gbdt / sector_xgboost 扩展 ModelConfig。
#
# 关键设计决策：
#   - 31 板块截面小 → 加强正则化（L1/L2, min_data_in_leaf=5）
#   - 训练频率：weekly
#   - 标签：rank_2w（2周前向收益的截面排名）
#   - 不依赖 Qlib（使用 sklearn 兼容接口）

import json
import pickle
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.metrics import mean_squared_error
from scipy.stats import spearmanr, pearsonr

from src.sector_rotation.sector_data import (
    SectorDataCollector,
    SectorDataHandler,
    SW_SECTOR_CODES,
    get_sector_collector,
)
from src.sector_rotation.sector_features import (
    SectorFeatureEngine,
    SECTOR_FEATURE_GROUPS,
    get_sector_feature_engine,
)


@dataclass
class SectorModelConfig:
    """板块模型配置（对应 ModelConfig 的简化版）"""
    name: str
    model_type: str  # gbdt / xgboost / catboost
    model_kwargs: Dict[str, Any] = field(default_factory=dict)
    train_start: str = "2010-01-01"
    valid_ratio: float = 0.2
    oos_ratio: float = 0.1
    train_frequency: str = "weekly"
    label_horizon: int = 10  # 2周 = 10个交易日
    label_type: str = "rank_2w"  # rank_2w / ret_1w / excess_2w
    min_train_samples: int = 252
    description: str = ""


# ── 模型配置 ────────────────────────────────────────────────────

SECTOR_MODEL_CONFIGS: Dict[str, SectorModelConfig] = {
    "sector_gbdt": SectorModelConfig(
        name="sector_gbdt",
        model_type="gbdt",
        model_kwargs={
            "n_estimators": 200,
            "learning_rate": 0.03,
            "max_depth": 5,
            "num_leaves": 31,
            "min_child_samples": 5,
            "reg_alpha": 0.05,   # L1
            "reg_lambda": 0.05,  # L2
            "subsample": 0.8,
            "colsample_bytree": 0.8,
        },
        train_start="2010-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        train_frequency="weekly",
        label_horizon=10,
        label_type="excess_2w",
        description="LightGBM 板块轮动模型，加强正则化",
    ),
    "sector_xgboost": SectorModelConfig(
        name="sector_xgboost",
        model_type="xgboost",
        model_kwargs={
            "n_estimators": 200,
            "learning_rate": 0.03,
            "max_depth": 5,
            "reg_alpha": 0.05,
            "reg_lambda": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
        },
        train_start="2010-01-01",
        valid_ratio=0.2,
        label_type="excess_2w",
        description="XGBoost 板块轮动模型",
    ),
}


class SectorModelGenerator:
    """板块 ML 模型生成器

    训练/预测/集成的完整流程。
    不依赖 Qlib，使用 sklearn 兼容接口。
    """

    def __init__(
        self,
        feature_engine: Optional[SectorFeatureEngine] = None,
        collector: Optional[SectorDataCollector] = None,
        models_dir: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ):
        self.feature_engine = feature_engine or get_sector_feature_engine()
        self.collector = collector or get_sector_collector()
        self.models_dir = models_dir or Path("data/output/sector_models")
        self.output_dir = output_dir or Path("data/output")
        self.models_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def train(
        self,
        model_name: str = "sector_gbdt",
        force_retrain: bool = False,
    ) -> Dict[str, Any]:
        """训练板块模型

        Args:
            model_name: 模型名称
            force_retrain: 是否强制重训

        Returns:
            训练结果字典
        """
        cfg = SECTOR_MODEL_CONFIGS.get(model_name)
        if cfg is None:
            raise ValueError(f"Unknown model: {model_name}")

        model_path = self.models_dir / f"{model_name}.pkl"

        # 检查是否已训练
        if model_path.exists() and not force_retrain:
            return {"status": "cached", "model_path": str(model_path)}

        # 1. 加载数据
        prices = self.collector.build_price_matrix()
        if prices.empty:
            raise ValueError("No sector price data available")

        # 加载沪深300基准
        try:
            bench_df = self.collector.load_cache("000300")
            if bench_df is not None and "close" in bench_df.columns:
                bench_df = bench_df.set_index("date") if "date" in bench_df.columns else bench_df
                benchmark = bench_df["close"]
            else:
                benchmark = None
        except Exception:
            benchmark = None

        # 2. 计算特征
        features = self.feature_engine.compute_all_features(prices, benchmark)
        features = self.feature_engine.cross_sectional_normalize(features)

        # 3. 计算标签
        labels = self._compute_labels(prices, cfg.label_horizon, cfg.label_type)

        # 4. 对齐特征和标签
        X, y, dates, sectors = self._align_features_labels(features, labels)

        if len(X) < cfg.min_train_samples:
            raise ValueError(
                f"Insufficient training samples: {len(X)} < {cfg.min_train_samples}"
            )

        # 5. 时间序列分割
        n = len(X)
        train_end = int(n * (1 - cfg.valid_ratio - cfg.oos_ratio))
        valid_end = int(n * (1 - cfg.oos_ratio))

        X_train, y_train = X[:train_end], y[:train_end]
        X_valid, y_valid = X[train_end:valid_end], y[train_end:valid_end]
        X_oos, y_oos = X[valid_end:], y[valid_end:]

        # 6. 训练模型
        model = self._build_model(cfg)
        model.fit(X_train, y_train)

        # 7. 评估
        train_pred = model.predict(X_train)
        valid_pred = model.predict(X_valid)
        oos_pred = model.predict(X_oos)

        train_ic = self._compute_ic(train_pred, y_train)
        valid_ic = self._compute_ic(valid_pred, y_valid)
        oos_ic = self._compute_ic(oos_pred, y_oos)

        # 8. 保存模型
        with open(model_path, "wb") as f:
            pickle.dump({
                "model": model,
                "config": cfg,
                "feature_names": self.feature_engine.get_feature_names(),
                "train_date": datetime.now().isoformat(),
                "metrics": {
                    "train_ic": train_ic,
                    "valid_ic": valid_ic,
                    "oos_ic": oos_ic,
                    "train_rmse": np.sqrt(mean_squared_error(y_train, train_pred)),
                    "valid_rmse": np.sqrt(mean_squared_error(y_valid, valid_pred)),
                    "oos_rmse": np.sqrt(mean_squared_error(y_oos, oos_pred)),
                },
            }, f)

        return {
            "status": "trained",
            "model_name": model_name,
            "model_path": str(model_path),
            "train_samples": len(X_train),
            "valid_samples": len(X_valid),
            "oos_samples": len(X_oos),
            "train_ic": train_ic,
            "valid_ic": valid_ic,
            "oos_ic": oos_ic,
            "oos_ic_check": oos_ic.get("pearson_ic", 0) > 0.05,
        }

    def predict(
        self,
        model_name: str = "sector_gbdt",
        date: Optional[str] = None,
    ) -> pd.DataFrame:
        """预测板块排名

        Args:
            model_name: 模型名称
            date: 预测日期

        Returns:
            DataFrame with columns: sector_code, sector_name, score, rank
        """
        # 加载模型
        model_path = self.models_dir / f"{model_name}.pkl"
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}. Train first.")

        with open(model_path, "rb") as f:
            saved = pickle.load(f)

        model = saved["model"]
        cfg = saved["config"]

        # 计算最新特征
        prices = self.collector.build_price_matrix()

        # 加载基准
        try:
            bench_df = self.collector.load_cache("000300")
            if bench_df is not None and "close" in bench_df.columns:
                bench_df = bench_df.set_index("date") if "date" in bench_df.columns else bench_df
                benchmark = bench_df["close"]
            else:
                benchmark = None
        except Exception:
            benchmark = None

        features = self.feature_engine.compute_all_features(prices, benchmark)
        features = self.feature_engine.cross_sectional_normalize(features)

        if date is not None:
            target = pd.Timestamp(date)
            features = features.loc[features.index.get_level_values("date") <= target]

        latest_date = features.index.get_level_values("date").max()
        latest_features = features.loc[latest_date]

        # 预测
        X = latest_features.values
        scores = model.predict(X)

        # 构建结果
        result = pd.DataFrame({
            "sector_code": latest_features.index,
            "score": scores,
        })
        result["sector_name"] = result["sector_code"].map(
            lambda c: SW_SECTOR_CODES.get(c, {}).get("name", c)
        )
        result["category"] = result["sector_code"].map(
            lambda c: SW_SECTOR_CODES.get(c, {}).get("category", "unknown")
        )
        result = result.sort_values("score", ascending=False)
        result["rank"] = range(1, len(result) + 1)

        return result

    def predict_ensemble(
        self,
        model_names: Optional[List[str]] = None,
        date: Optional[str] = None,
        method: str = "mean",
    ) -> pd.DataFrame:
        """集成预测

        Args:
            model_names: 模型名称列表，默认全部
            date: 预测日期
            method: 集成方法 (mean / rank_mean)

        Returns:
            DataFrame with ensemble scores
        """
        if model_names is None:
            model_names = list(SECTOR_MODEL_CONFIGS.keys())

        all_predictions = []
        for name in model_names:
            try:
                pred = self.predict(name, date)
                pred = pred.set_index("sector_code")
                all_predictions.append(pred["score"])
            except Exception as e:
                print(f"[WARN] Model {name} prediction failed: {e}")

        if not all_predictions:
            raise ValueError("No model predictions available")

        scores_df = pd.DataFrame(all_predictions).T

        if method == "mean":
            ensemble_scores = scores_df.mean(axis=1)
        elif method == "rank_mean":
            ensemble_scores = scores_df.rank(pct=True).mean(axis=1)
        else:
            raise ValueError(f"Unknown method: {method}")

        result = pd.DataFrame({
            "sector_code": ensemble_scores.index,
            "score": ensemble_scores.values,
        })
        result["sector_name"] = result["sector_code"].map(
            lambda c: SW_SECTOR_CODES.get(c, {}).get("name", c)
        )
        result["category"] = result["sector_code"].map(
            lambda c: SW_SECTOR_CODES.get(c, {}).get("category", "unknown")
        )
        result = result.sort_values("score", ascending=False)
        result["rank"] = range(1, len(result) + 1)

        return result

    def get_top_sectors(
        self,
        n: int = 5,
        model_name: str = "sector_gbdt",
        date: Optional[str] = None,
        exclude_categories: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """获取 Top N 板块

        Args:
            n: 返回板块数
            model_name: 模型名称
            date: 预测日期
            exclude_categories: 排除的板块类别

        Returns:
            Top N 板块列表
        """
        pred = self.predict(model_name, date)

        if exclude_categories:
            pred = pred[~pred["category"].isin(exclude_categories)]

        top = pred.head(n)
        return top.to_dict("records")

    # ── 内部方法 ──────────────────────────────────────────────

    def _build_model(self, cfg: SectorModelConfig):
        """构建模型实例"""
        if cfg.model_type == "gbdt":
            try:
                import lightgbm as lgb
                return lgb.LGBMRegressor(**cfg.model_kwargs)
            except ImportError:
                # Fallback to sklearn GradientBoosting
                from sklearn.ensemble import GradientBoostingRegressor
                return GradientBoostingRegressor(
                    n_estimators=cfg.model_kwargs.get("n_estimators", 200),
                    learning_rate=cfg.model_kwargs.get("learning_rate", 0.03),
                    max_depth=cfg.model_kwargs.get("max_depth", 5),
                    subsample=cfg.model_kwargs.get("subsample", 0.8),
                )
        elif cfg.model_type == "xgboost":
            try:
                import xgboost as xgb
                return xgb.XGBRegressor(**cfg.model_kwargs)
            except ImportError:
                from sklearn.ensemble import GradientBoostingRegressor
                return GradientBoostingRegressor(
                    n_estimators=cfg.model_kwargs.get("n_estimators", 200),
                    learning_rate=cfg.model_kwargs.get("learning_rate", 0.03),
                    max_depth=cfg.model_kwargs.get("max_depth", 5),
                )
        elif cfg.model_type == "catboost":
            try:
                from catboost import CatBoostRegressor
                return CatBoostRegressor(**cfg.model_kwargs)
            except ImportError:
                from sklearn.ensemble import GradientBoostingRegressor
                return GradientBoostingRegressor(
                    n_estimators=cfg.model_kwargs.get("n_estimators", 200),
                    learning_rate=cfg.model_kwargs.get("learning_rate", 0.03),
                    max_depth=cfg.model_kwargs.get("max_depth", 5),
                )
        else:
            raise ValueError(f"Unknown model type: {cfg.model_type}")

    def _compute_labels(
        self,
        prices: pd.DataFrame,
        horizon: int,
        label_type: str,
    ) -> pd.DataFrame:
        """计算标签

        Args:
            prices: 价格矩阵 (date × sector_code)
            horizon: 前向天数
            label_type: 标签类型

        Returns:
            DataFrame with same index as prices, values = labels
        """
        if label_type == "rank_2w":
            # 2周前向收益的截面排名
            fwd_returns = prices.shift(-horizon) / prices - 1
            labels = fwd_returns.rank(axis=1, pct=True)  # 截面排名 0-1
        elif label_type == "ret_1w":
            labels = prices.shift(-5) / prices - 1
        elif label_type == "excess_2w":
            fwd_returns = prices.shift(-horizon) / prices - 1
            avg_return = fwd_returns.mean(axis=1)
            labels = fwd_returns.sub(avg_return, axis=0)
        else:
            raise ValueError(f"Unknown label type: {label_type}")

        return labels

    def _align_features_labels(
        self,
        features: pd.DataFrame,
        labels: pd.DataFrame,
    ) -> Tuple[np.ndarray, np.ndarray, List, List]:
        """对齐特征和标签"""
        X_list = []
        y_list = []
        dates_list = []
        sectors_list = []

        for date in features.index.get_level_values("date").unique():
            if date not in labels.index:
                continue

            feat_date = features.loc[date]
            label_date = labels.loc[date]

            common_sectors = feat_date.index.intersection(label_date.index)
            if len(common_sectors) == 0:
                continue

            for sector in common_sectors:
                feat_vec = feat_date.loc[sector].values
                label_val = label_date[sector]

                if not np.any(np.isnan(feat_vec)) and not np.isnan(label_val):
                    X_list.append(feat_vec)
                    y_list.append(label_val)
                    dates_list.append(date)
                    sectors_list.append(sector)

        return np.array(X_list), np.array(y_list), dates_list, sectors_list

    def _compute_ic(self, predictions: np.ndarray, labels: np.ndarray) -> Dict[str, float]:
        """计算 IC 指标"""
        pearson_ic, pearson_p = pearsonr(predictions, labels)
        rank_ic, rank_p = spearmanr(predictions, labels)

        return {
            "pearson_ic": float(pearson_ic),
            "pearson_ic_pval": float(pearson_p),
            "rank_ic": float(rank_ic),
            "rank_ic_pval": float(rank_p),
            "ic_positive_rate": float(np.mean(predictions * labels > 0)),
        }


# ── 模块级单例 ──────────────────────────────────────────────────

_generator: Optional[SectorModelGenerator] = None


def get_sector_model_generator() -> SectorModelGenerator:
    global _generator
    if _generator is None:
        _generator = SectorModelGenerator()
    return _generator