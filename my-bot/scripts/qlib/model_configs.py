"""
FM_trade 模型配置 — 桥接层 (底层: quant-signal-factory)

从 QSF src/factors/model_configs.py 导入，保持 FM_trade API 兼容。
"""

from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import sys
from pathlib import Path

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

from src.factors.model_configs import (
    ModelConfig,
    MODEL_CONFIGS as _QSF_MODEL_CONFIGS,
    get_model_config as _qsf_get_model_config,
    list_models as _qsf_list_models,
    get_active_models,
    get_gpu_models,
    get_daily_models,
    get_monthly_models,
    get_model_ensemble_configs,
    export_configs,
)

# ─── FM_trade 兼容层 ───

# 模型名映射: FM_trade name → QSF name
_MODEL_NAME_MAP = {
    "gbdt_alpha158": "gbdt",
    "xgboost_alpha158": "xgboost",
    "gbdt_alpha360": "gbdt_large",
    "tabnet_alpha158": "tabnet",
    "lstm_alpha158": "lstm",
}

DEFAULT_HANDLER_CLASS = "Alpha158"
DEFAULT_HANDLER_MODULE = "qlib.contrib.data.handler"
DEFAULT_DATASET_CLASS = "DatasetH"
DEFAULT_DATASET_MODULE = "qlib.data.dataset"

# 从 QSF ModelConfig 生成 FM_trade 兼容的 dict 格式
def _qsf_to_preset_dict(cfg: ModelConfig) -> Dict[str, Any]:
    """将 QSF ModelConfig 转为 FM_trade PRESET_MODELS dict 格式"""
    return {
        "class": cfg.model_class,
        "module_path": cfg.module_path,
        "kwargs": cfg.model_kwargs.copy(),
    }

PRESET_MODELS: Dict[str, Dict[str, Any]] = {
    "gbdt_alpha158": _qsf_to_preset_dict(_QSF_MODEL_CONFIGS["gbdt"]),
    "xgboost_alpha158": _qsf_to_preset_dict(_QSF_MODEL_CONFIGS["xgboost"]),
    "gbdt_alpha360": _qsf_to_preset_dict(_QSF_MODEL_CONFIGS["gbdt_large"]),
    "tabnet_alpha158": _qsf_to_preset_dict(_QSF_MODEL_CONFIGS["tabnet"]),
    "lstm_alpha158": _qsf_to_preset_dict(_QSF_MODEL_CONFIGS["lstm"]),
}

HANDLER_CONFIGS: Dict[str, Dict[str, Any]] = {
    "Alpha158": {
        "class": "Alpha158",
        "module_path": "qlib.contrib.data.handler",
    },
    "Alpha360": {
        "class": "Alpha360",
        "module_path": "qlib.contrib.data.handler",
    },
}


def get_model_config(
    model_name: str = "gbdt_alpha158",
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """获取模型配置字典（与 Qlib init_instance_by_config 兼容）"""
    if model_name not in PRESET_MODELS:
        raise ValueError(
            f"Unknown model '{model_name}'. Available: {list(PRESET_MODELS.keys())}"
        )
    cfg = PRESET_MODELS[model_name].copy()
    cfg["kwargs"] = cfg.get("kwargs", {}).copy()
    if overrides:
        cfg["kwargs"].update(overrides)
    return cfg


def get_handler_config(
    handler_name: str = "Alpha158",
    instruments: str = "csi300",
    start_time: str = "2018-01-01",
    end_time: str = "2026-12-31",
    fit_start_time: str = "<dataset.kwargs.segments.train.0>",
    fit_end_time: str = "<dataset.kwargs.segments.train.1>",
) -> Dict[str, Any]:
    """获取数据处理器的配置字典"""
    if handler_name not in HANDLER_CONFIGS:
        raise ValueError(
            f"Unknown handler '{handler_name}'. Available: {list(HANDLER_CONFIGS.keys())}"
        )
    cfg = HANDLER_CONFIGS[handler_name].copy()
    cfg["kwargs"] = {
        "start_time": start_time,
        "end_time": end_time,
        "fit_start_time": fit_start_time,
        "fit_end_time": fit_end_time,
        "instruments": instruments,
    }
    return cfg


def get_dataset_config(
    handler_name: str = "Alpha158",
    instruments: str = "csi300",
    train: Tuple[str, str] = ("2018-01-01", "2025-12-31"),
    valid: Tuple[str, str] = ("2026-01-01", "2026-06-30"),
    test: Tuple[str, str] = ("2026-07-01", "2026-12-31"),
    data_start: str = "2018-01-01",
    data_end: str = "2026-12-31",
) -> Dict[str, Any]:
    """获取完整 Dataset 配置字典"""
    handler_cfg = get_handler_config(
        handler_name=handler_name,
        instruments=instruments,
        start_time=data_start,
        end_time=data_end,
    )
    return {
        "class": "DatasetH",
        "module_path": "qlib.data.dataset",
        "kwargs": {
            "handler": handler_cfg,
            "segments": {
                "train": train,
                "valid": valid,
                "test": test,
            },
        },
    }


def list_available_models() -> Dict[str, str]:
    """列出所有可用的预置模型及其描述"""
    return {
        "gbdt_alpha158": "LightGBM + Alpha158 — 日常 alpha 基线，快速稳定",
        "xgboost_alpha158": "XGBoost + Alpha158 — 备选方案，与 GBDT 取均值",
        "gbdt_alpha360": "LightGBM + Alpha360 — 360 维特征，更全面的因子覆盖",
        "tabnet_alpha158": "TabNet + Alpha158 — 特征交互，适合财报季",
        "lstm_alpha158": "LSTM + Alpha158 — 时序动量捕捉，需 GPU",
    }


def get_handler_name_for_model(model_name: str) -> str:
    """从模型名称推断对应的处理器名称"""
    if "_alpha360" in model_name:
        return "Alpha360"
    return "Alpha158"