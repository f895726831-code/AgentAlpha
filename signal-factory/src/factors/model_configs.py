"""
模型配置定义 — GBDT / XGBoost / LSTM / TabNet

为 alpha_signals.py 提供模型超参数配置、训练窗口和特征集定义。
每个模型配置包含:
  - 模型类路径 (Qlib contrib model)
  - 超参数 (kwargs)
  - 训练参数 (num_boost_round, early_stopping_rounds 等)
  - 推荐数据窗口 (train_start, valid_ratio)
  - 特征集 (alpha158 / alpha360)

用法:
    from src.factors.model_configs import MODEL_CONFIGS, get_model_config, list_models

    cfg = get_model_config("gbdt")
    model = cfg.build_model()
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from pathlib import Path


@dataclass
class ModelConfig:
    """单个模型的配置"""

    # 标识
    name: str                          # 模型名称 (gbdt / xgboost / lstm / tabnet)
    display_name: str                  # 显示名称
    model_class: str                   # Qlib model 类路径
    module_path: str                   # Qlib model 模块路径

    # 超参数
    model_kwargs: Dict[str, Any] = field(default_factory=dict)

    # 训练参数
    num_boost_round: int = 300
    early_stopping_rounds: int = 50
    verbose_eval: int = 20
    loss: str = "mse"

    # 数据窗口
    train_start: str = "2015-01-01"    # 训练数据起始日
    valid_ratio: float = 0.2           # 验证集比例
    oos_ratio: float = 0.1             # 样本外测试比例

    # 特征集
    handler_class: str = "Alpha158"     # Qlib contrib data handler
    handler_module: str = "qlib.contrib.data.handler"
    handler_kwargs: Dict[str, Any] = field(default_factory=dict)

    # 训练频率
    train_frequency: str = "weekly"    # daily / weekly / monthly
    min_train_samples: int = 252       # 最少训练样本数

    # 元数据
    requires_gpu: bool = False
    description: str = ""

    def build_model(self):
        """构建 Qlib 模型实例"""
        import importlib
        module = importlib.import_module(self.module_path)
        model_cls = getattr(module, self.model_class)
        return model_cls(**self.model_kwargs)

    def build_handler(self, instruments: str = "csi500", **overrides) -> Any:
        """构建 Qlib DataHandler 实例"""
        import importlib
        module = importlib.import_module(self.handler_module)
        handler_cls = getattr(module, self.handler_class)
        kwargs = {**self.handler_kwargs, **overrides}
        # 始终使用调用方传入的 instruments，覆盖 handler_kwargs 中的默认值
        kwargs["instruments"] = instruments
        return handler_cls(**kwargs)

    def to_dict(self) -> Dict[str, Any]:
        """导出为字典（用于序列化）"""
        return {
            "name": self.name,
            "display_name": self.display_name,
            "model_class": self.model_class,
            "module_path": self.module_path,
            "model_kwargs": self.model_kwargs,
            "num_boost_round": self.num_boost_round,
            "early_stopping_rounds": self.early_stopping_rounds,
            "loss": self.loss,
            "train_start": self.train_start,
            "valid_ratio": self.valid_ratio,
            "handler_class": self.handler_class,
            "train_frequency": self.train_frequency,
            "requires_gpu": self.requires_gpu,
            "description": self.description,
        }


# ─── 模型配置定义 ───

MODEL_CONFIGS: Dict[str, ModelConfig] = {
    "gbdt": ModelConfig(
        name="gbdt",
        display_name="LightGBM (GBDT)",
        model_class="LGBModel",
        module_path="qlib.contrib.model.gbdt",
        model_kwargs={
            "loss": "mse",
            "num_leaves": 64,
            "learning_rate": 0.05,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 5,
            "min_data_in_leaf": 20,
            "lambda_l1": 0.01,
            "lambda_l2": 0.01,
        },
        num_boost_round=300,
        early_stopping_rounds=50,
        verbose_eval=20,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha158",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="LightGBM 梯度提升树，日常 alpha 基线模型。快速稳定，适合作为默认模型。",
    ),

    "xgboost": ModelConfig(
        name="xgboost",
        display_name="XGBoost",
        model_class="XGBModel",
        module_path="qlib.contrib.model.xgboost",
        model_kwargs={
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.01,
            "reg_lambda": 0.01,
            "objective": "reg:squarederror",
            "eval_metric": "rmse",
        },
        num_boost_round=300,
        early_stopping_rounds=50,
        verbose_eval=20,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha158",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="XGBoost 梯度提升树，备选方案。与 GBDT 取均值可获得更稳健的信号。",
    ),

    "lstm": ModelConfig(
        name="lstm",
        display_name="LSTM (时序)",
        model_class="LSTM",
        module_path="qlib.contrib.model.pytorch_lstm",
        model_kwargs={
            "d_feat": 158,             # Alpha158 特征数
            "hidden_size": 128,
            "num_layers": 2,
            "dropout": 0.2,
            "n_epochs": 100,
            "batch_size": 512,
            "lr": 0.001,
            "early_stop": 10,
            "loss": "mse",
            "optimizer": "adam",
        },
        num_boost_round=0,             # 不适用
        early_stopping_rounds=10,       # 用 PyTorch 的 early_stop
        verbose_eval=5,
        loss="mse",
        train_start="2018-01-01",      # 需要更多数据
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha158",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2018-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2018-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="monthly",
        requires_gpu=True,
        description="LSTM 时序神经网络，捕捉价格序列中的非线性时序依赖。适合动量策略。",
    ),

    "tabnet": ModelConfig(
        name="tabnet",
        display_name="TabNet",
        model_class="TabNetModel",
        module_path="qlib.contrib.model.pytorch_tabnet",
        model_kwargs={
            "d_feat": 158,
            "n_d": 32,
            "n_a": 32,
            "n_steps": 3,
            "gamma": 1.3,
            "n_epochs": 100,
            "batch_size": 512,
            "lr": 0.001,
            "early_stop": 10,
            "loss": "mse",
            "optimizer": "adam",
        },
        num_boost_round=0,
        early_stopping_rounds=10,
        verbose_eval=5,
        loss="mse",
        train_start="2018-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha158",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2018-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2018-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="monthly",
        requires_gpu=True,
        description="TabNet 注意力机制表格模型，自动选择重要特征交互。适合财报季等特征重要性变化大的场景。",
    ),

    "gbdt_large": ModelConfig(
        name="gbdt_large",
        display_name="LightGBM (大容量)",
        model_class="LGBModel",
        module_path="qlib.contrib.model.gbdt",
        model_kwargs={
            "loss": "mse",
            "num_leaves": 128,
            "learning_rate": 0.03,
            "feature_fraction": 0.7,
            "bagging_fraction": 0.7,
            "bagging_freq": 5,
            "min_data_in_leaf": 10,
            "lambda_l1": 0.005,
            "lambda_l2": 0.005,
        },
        num_boost_round=500,
        early_stopping_rounds=50,
        verbose_eval=20,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.15,
        handler_class="Alpha158",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="大容量 GBDT 变体，更多叶子、更多迭代。精度更高但训练更慢，适合月度重训。",
    ),

    "catboost": ModelConfig(
        name="catboost",
        display_name="CatBoost",
        model_class="CatBoostModel",
        module_path="qlib.contrib.model.catboost_model",
        model_kwargs={
            "loss": "RMSE",
            "learning_rate": 0.05,
            "max_depth": 6,
            "subsample": 0.8,
            "colsample_bylevel": 0.8,
            "iterations": 300,
            "random_seed": 42,
            "thread_count": 20,
        },
        num_boost_round=300,
        early_stopping_rounds=50,
        verbose_eval=20,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha158",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="CatBoost 有序梯度提升，对类别特征和缺失值天然友好，无需手动编码。GPU 加速训练。",
    ),

    # ── 线性基线 (W46 #7) ──
    # Ridge 作为过拟合检测器：若 Ridge IC ≈ 树模型 IC，则树模型在学线性关系
    # 复杂结构白费。使用 Alpha158 特征集（与 LSTM/TabNet 一致），秒级训练。
    "ridge": ModelConfig(
        name="ridge",
        display_name="Ridge 线性基线",
        model_class="LinearModel",
        module_path="qlib.contrib.model.linear",
        model_kwargs={
            "estimator": "ridge",
            "alpha": 1.0,
            "fit_intercept": False,
        },
        num_boost_round=0,             # 不适用
        early_stopping_rounds=0,       # 不适用
        verbose_eval=0,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha158",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="Ridge 线性回归基线。过拟合检测器：若 Ridge IC ≈ 树模型 IC，树模型的非线性结构是白费的。每周重训，CPU 秒级完成。",
    ),

    # ── Alpha360 特征集模型 (W46 #5) ──
    # 历史实验证实 Alpha360: IC=0.081, IR=0.939 vs Alpha158: IC~0.069
    # 360 特征集包含更多价格/成交量维度的技术因子
    # 超参数相对 Alpha158 版本增强正则化（更多特征→更高过拟合风险）
    "gbdt_360": ModelConfig(
        name="gbdt_360",
        display_name="LightGBM (Alpha360)",
        model_class="LGBModel",
        module_path="qlib.contrib.model.gbdt",
        model_kwargs={
            "loss": "mse",
            "num_leaves": 64,
            "learning_rate": 0.03,
            "feature_fraction": 0.6,     # 更多特征 → 更低的特征采样率
            "bagging_fraction": 0.7,
            "bagging_freq": 5,
            "min_data_in_leaf": 20,
            "lambda_l1": 0.05,           # 增强正则化
            "lambda_l2": 0.05,
        },
        num_boost_round=400,              # 更多特征需要更多迭代
        early_stopping_rounds=50,
        verbose_eval=20,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha360",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",     # 动态覆盖
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",  # 动态覆盖
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="LightGBM + Alpha360 特征集（360 维技术因子），IC 预期 0.08+。W46 #5 从 Alpha158 升级。",
    ),
    "xgboost_360": ModelConfig(
        name="xgboost_360",
        display_name="XGBoost (Alpha360)",
        model_class="XGBModel",
        module_path="qlib.contrib.model.xgboost",
        model_kwargs={
            "max_depth": 6,
            "learning_rate": 0.03,
            "subsample": 0.7,
            "colsample_bytree": 0.6,
            "reg_alpha": 0.05,
            "reg_lambda": 0.05,
            "objective": "reg:squarederror",
            "eval_metric": "rmse",
        },
        num_boost_round=400,
        early_stopping_rounds=50,
        verbose_eval=20,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha360",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="XGBoost + Alpha360，增强正则化抗过拟合。W46 #5 升级。",
    ),
    "catboost_360": ModelConfig(
        name="catboost_360",
        display_name="CatBoost (Alpha360)",
        model_class="CatBoostModel",
        module_path="qlib.contrib.model.catboost_model",
        model_kwargs={
            "loss": "RMSE",
            "learning_rate": 0.03,
            "max_depth": 6,
            "subsample": 0.7,
            "colsample_bylevel": 0.6,
            "iterations": 400,
            "random_seed": 42,
            "thread_count": 20,
        },
        num_boost_round=400,
        early_stopping_rounds=50,
        verbose_eval=20,
        loss="mse",
        train_start="2015-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="Alpha360",
        handler_module="qlib.contrib.data.handler",
        handler_kwargs={
            "start_time": "2015-01-01",
            "end_time": "2026-08-01",
            "fit_start_time": "2015-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "csi500",
            "label": ["Ref($close, -5) / $close - 1"],
        },
        train_frequency="weekly",
        description="CatBoost + Alpha360，有序梯度提升天然抗过拟合。W46 #5 升级。",
    ),

    # ── Layer 1: 板块轮动模型（2026-09-09 新增）──
    # 31 个申万一级行业板块的 ML 预测模型。
    # 截面小（31 个），加强正则化防过拟合。
    # 使用 SectorDataHandler 替代 Alpha158 handler。
    "sector_gbdt": ModelConfig(
        name="sector_gbdt",
        display_name="Sector GBDT (板块轮动)",
        model_class="LGBModel",
        module_path="qlib.contrib.model.gbdt",
        model_kwargs={
            "loss": "mse",
            "num_leaves": 31,
            "learning_rate": 0.03,
            "lambda_l1": 0.05,
            "lambda_l2": 0.05,
            "min_data_in_leaf": 5,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
        },
        num_boost_round=200,
        early_stopping_rounds=30,
        verbose_eval=20,
        loss="mse",
        train_start="2010-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="SectorDataHandler",
        handler_module="src.sector_rotation.sector_data",
        handler_kwargs={
            "start_time": "2010-01-01",
            "end_time": "2026-09-01",
            "fit_start_time": "2010-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "sw_sectors",
        },
        train_frequency="weekly",
        min_train_samples=252,
        requires_gpu=False,
        description="LightGBM 板块轮动模型，31 板块截面增强正则化。2 周前向排名预测。",
    ),
    "sector_xgboost": ModelConfig(
        name="sector_xgboost",
        display_name="Sector XGBoost (板块轮动)",
        model_class="XGBModel",
        module_path="qlib.contrib.model.xgboost",
        model_kwargs={
            "max_depth": 5,
            "learning_rate": 0.03,
            "reg_alpha": 0.05,
            "reg_lambda": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
        },
        num_boost_round=200,
        early_stopping_rounds=30,
        verbose_eval=20,
        loss="mse",
        train_start="2010-01-01",
        valid_ratio=0.2,
        oos_ratio=0.1,
        handler_class="SectorDataHandler",
        handler_module="src.sector_rotation.sector_data",
        handler_kwargs={
            "start_time": "2010-01-01",
            "end_time": "2026-09-01",
            "fit_start_time": "2010-01-01",
            "fit_end_time": "2024-12-31",
            "instruments": "sw_sectors",
        },
        train_frequency="weekly",
        min_train_samples=252,
        requires_gpu=False,
        description="XGBoost 板块轮动模型，31 板块截面增强正则化。2 周前向排名预测。",
    ),
}


# ─── 便捷函数 ───


def get_model_config(name: str) -> Optional[ModelConfig]:
    """获取指定模型的配置"""
    return MODEL_CONFIGS.get(name)


def list_models() -> List[str]:
    """列出所有可用模型"""
    return list(MODEL_CONFIGS.keys())


def get_active_models() -> List[str]:
    """获取默认活跃模型列表（用于日常预测）"""
    return ["gbdt", "xgboost"]


def get_gpu_models() -> List[str]:
    """获取需要 GPU 的模型列表"""
    return [name for name, cfg in MODEL_CONFIGS.items() if cfg.requires_gpu]


def get_daily_models() -> List[ModelConfig]:
    """获取适合日常推理的模型（轻量、快速）"""
    return [cfg for cfg in MODEL_CONFIGS.values() if not cfg.requires_gpu]


def get_monthly_models() -> List[ModelConfig]:
    """获取适合月度重训的模型（含 GPU 模型）"""
    return list(MODEL_CONFIGS.values())


def get_model_ensemble_configs(
    model_names: Optional[List[str]] = None,
) -> List[ModelConfig]:
    """
    获取用于集成预测的模型配置列表。

    参数:
        model_names: 模型名称列表，None 则使用默认活跃模型

    返回:
        List[ModelConfig]
    """
    names = model_names or get_active_models()
    configs = []
    for name in names:
        cfg = get_model_config(name)
        if cfg is not None:
            configs.append(cfg)
    return configs


def get_dynamic_dates(cal_path: Optional[str] = None) -> Tuple[str, str]:
    """
    从 Qlib 日历读取最新日期，动态计算 end_time 和 fit_end_time。

    避免硬编码日期过期导致模型训练/回测窗口滞后。

    参数:
        cal_path: 日历文件路径，None 则使用默认路径

    返回:
        (end_time, fit_end_time) — end_time 为日历最后一天，
        fit_end_time 为 end_time 往前推 2 年（用于 train/valid 分割）
    """
    if cal_path is None:
        cal_path = "D:/qlib/qlib_data/cn_data/calendars/day.txt"

    cal = Path(cal_path)
    fallback_end = "2026-08-01"
    fallback_fit = "2024-12-31"

    if not cal.exists():
        return fallback_end, fallback_fit

    with open(cal, "r", encoding="utf-8") as f:
        dates = [line.strip() for line in f if line.strip() and line.strip() != "1970-01-01"]

    if not dates:
        return fallback_end, fallback_fit

    end_time = dates[-1]
    try:
        end_dt = pd.Timestamp(end_time)
        fit_end = (end_dt - pd.DateOffset(months=3)).strftime("%Y-%m-%d")
    except Exception:
        return fallback_end, fallback_fit

    return end_time, fit_end


def _apply_dynamic_dates():
    """在模块加载时自动更新所有模型的 end_time / fit_end_time"""
    end_time, fit_end_time = get_dynamic_dates()
    for cfg in MODEL_CONFIGS.values():
        if "end_time" in cfg.handler_kwargs:
            cfg.handler_kwargs["end_time"] = end_time
        if "fit_end_time" in cfg.handler_kwargs:
            cfg.handler_kwargs["fit_end_time"] = fit_end_time


_apply_dynamic_dates()


def export_configs() -> Dict[str, Dict]:
    """导出所有模型配置为字典"""
    return {name: cfg.to_dict() for name, cfg in MODEL_CONFIGS.items()}