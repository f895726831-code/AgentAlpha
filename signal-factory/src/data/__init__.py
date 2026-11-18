"""数据层 — PIT 引擎 + Qlib 初始化 + AST 泄漏检测 + Wind 数据桥 + OOS 访问控制 + 配置"""
from src.data.pit_handler import PITDataHandler, PITCheckpoint, detect_pit_violation, validate_strategy_pit
from src.data.leakage_checker import (
    analyze_ast,
    check_factor_code,
    batch_check_factors,
    generate_ast_report,
    PITLeakageVisitor,
)
from src.data.wind_bridge import (
    WindBridge,
    sync_wind_to_qlib,
    get_wind_data_summary,
)
from src.data.oos_guard import (
    OOSGuard,
    OOSAccessResult,
    get_guard,
)
from src.data.config_loader import (
    get_config,
    get_qlib_csv_dir,
    get_qlib_bin_dir,
    get_qlib_region,
    get_wind_json_dir,
    get_pit_config,
    reload_config,
)

__all__ = [
    "PITDataHandler",
    "PITCheckpoint",
    "detect_pit_violation",
    "validate_strategy_pit",
    "analyze_ast",
    "check_factor_code",
    "batch_check_factors",
    "generate_ast_report",
    "PITLeakageVisitor",
    "WindBridge",
    "sync_wind_to_qlib",
    "get_wind_data_summary",
    "OOSGuard",
    "OOSAccessResult",
    "get_guard",
    "get_config",
    "get_qlib_csv_dir",
    "get_qlib_bin_dir",
    "get_qlib_region",
    "get_wind_json_dir",
    "get_pit_config",
    "reload_config",
]