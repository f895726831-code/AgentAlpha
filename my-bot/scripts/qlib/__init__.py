"""
AgentAlpha Qlib 桥接层 — 薄封装，底层调用 signal-factory

所有功能从 D:\AgentAlpha\signal-factory\src\ 导入。
此模块从 QSF 导入等价实现，保持 AgentAlpha API 兼容。

版本: 0.2.0 → 桥接层 (底层: QSF)
"""

__version__ = "0.2.0-bridge"
__description__ = "AgentAlpha Qlib 桥接层 (底层: signal-factory)"

# 数据桥接
from .data_bridge import (
    convert_json_files,
    dump_to_qlib_bin,
    verify_qlib_data,
    windcode_to_qlib_symbol,
    DEFAULT_WIND_JSON_DIR,
    DEFAULT_QLIB_CSV_DIR,
    DEFAULT_QLIB_BIN_DIR,
)

# 模型配置
from .model_configs import (
    PRESET_MODELS,
    HANDLER_CONFIGS,
    get_model_config,
    get_handler_config,
    get_dataset_config,
    list_available_models,
    get_handler_name_for_model,
)

# Alpha 信号
from .alpha_signals import (
    run_alpha,
    train_model,
    predict_alpha,
    format_alpha_output,
    save_alpha_output,
    load_cached_model,
    get_model_info,
    init_qlib,
    load_config,
    get_alpha_config,
    get_data_config,
)

# 策略映射
from .strategy_mapper import (
    STRATEGIES,
    get_strategy_info,
    generate_signal,
    get_qlib_signal_from_rules,
    map_strategy_to_qlib,
    list_strategies,
    SingleStockSignalStrategy,
)

# 回测适配器
from .backtest_adapter import (
    init_qlib_for_backtest,
    run_qlib_backtest,
)

# Gate 1 评分
from .gate1_scorer import (
    score_gate1,
    load_risk_policy,
)