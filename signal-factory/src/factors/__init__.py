"""因子层 — ML 模型 + alpha 信号生成 + 三道验证关（RD-Agent 因子挖掘已迁移至 factor-lab）"""

from src.factors.model_configs import (
    ModelConfig,
    MODEL_CONFIGS,
    get_model_config,
    list_models,
    get_active_models,
    get_model_ensemble_configs,
)
from src.factors.alpha_signals import (
    AlphaSignalGenerator,
    generate_alpha_signals,
    generate_ensemble_signals,
    get_latest_alpha_signals,
)

# factor-lab 客户端（替代直接调用 RD-Agent）
from src.factor_lab_client import FactorLabClient, get_client, run_paper_mining_sync

# Phase 2: 三道验证关
from src.factors.validator import (
    FactorValidator,
    ValidationResult,
    GateResult,
    get_validator,
    validate_factor,
)
from src.factors.hypothesis_registry import (
    HypothesisRegistry,
    Hypothesis,
    get_registry,
)
from src.factors.validation_logger import (
    ValidationLogger,
    ValidationEntry,
    get_logger,
    get_validation_log_dir,
)

__all__ = [
    # ML 模型
    "ModelConfig",
    "MODEL_CONFIGS",
    "get_model_config",
    "list_models",
    "get_active_models",
    "get_model_ensemble_configs",
    # Alpha 信号
    "AlphaSignalGenerator",
    "generate_alpha_signals",
    "generate_ensemble_signals",
    "get_latest_alpha_signals",
    # factor-lab 客户端
    "FactorLabClient",
    "get_client",
    "run_paper_mining_sync",
    # Phase 2: 三道验证关
    "FactorValidator",
    "ValidationResult",
    "GateResult",
    "get_validator",
    "validate_factor",
    "HypothesisRegistry",
    "Hypothesis",
    "get_registry",
    "ValidationLogger",
    "ValidationEntry",
    "get_logger",
    "get_validation_log_dir",
]