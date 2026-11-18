"""
配置加载器 — 统一读取 config/data.yaml

所有模块通过此模块获取配置，不再硬编码路径。
环境变量优先级高于 YAML 配置。

用法:
    from src.data.config_loader import get_config, get_qlib_csv_dir, get_wind_json_dir

    csv_dir = get_qlib_csv_dir()
    wind_dir = get_wind_json_dir()
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

# 模块级缓存
_config_cache: Optional[Dict[str, Any]] = None
_config_loaded: bool = False


def _find_project_root() -> Path:
    """查找项目根目录（包含 config/data.yaml 的目录）"""
    # 从当前文件向上查找
    current = Path(__file__).resolve().parent.parent.parent  # src/data/ -> src/ -> project/
    markers = ["config/data.yaml", "config/data.yml", "pyproject.toml", "setup.py"]
    for _ in range(4):
        for m in markers:
            if (current / m).exists():
                return current
        current = current.parent
    return Path.cwd()


def _load_config() -> Dict[str, Any]:
    """加载 config/data.yaml，失败时返回空配置"""
    global _config_cache, _config_loaded

    if _config_loaded:
        return _config_cache or {}

    _config_loaded = True

    project_root = _find_project_root()
    config_path = project_root / "config" / "data.yaml"

    if not config_path.exists():
        config_path = project_root / "config" / "data.yml"
    if not config_path.exists():
        _config_cache = {}
        return {}

    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            _config_cache = yaml.safe_load(f) or {}
    except Exception:
        _config_cache = {}

    return _config_cache or {}


def get_config() -> Dict[str, Any]:
    """获取完整配置（首次调用时加载并缓存）"""
    return _load_config()


def get_data_config() -> Dict[str, Any]:
    """获取 data 配置段"""
    config = _load_config()
    return config.get("data", {}) if config else {}


def get_qlib_config() -> Dict[str, Any]:
    """获取 qlib 配置段"""
    data = get_data_config()
    return data.get("qlib", {}) if data else {}


def get_wind_config() -> Dict[str, Any]:
    """获取 wind 配置段"""
    data = get_data_config()
    return data.get("wind", {}) if data else {}


def get_sync_config() -> Dict[str, Any]:
    """获取 sync 配置段"""
    data = get_data_config()
    return data.get("sync", {}) if data else {}


def get_pit_config() -> Dict[str, Any]:
    """获取 pit 配置段"""
    data = get_data_config()
    return data.get("pit", {}) if data else {}


# ─── 便捷访问函数 ───


def get_qlib_csv_dir() -> str:
    """获取 Qlib CSV 源数据目录"""
    env_val = os.environ.get("QLIB_CSV_DIR")
    if env_val:
        return env_val
    qlib = get_qlib_config()
    return qlib.get("csv_dir", "D:/qlib/qlib_data/source/cn_1d")


def get_qlib_bin_dir() -> str:
    """获取 Qlib bin 数据目录"""
    env_val = os.environ.get("QLIB_DATA_DIR")
    if env_val:
        return env_val
    qlib = get_qlib_config()
    return qlib.get("bin_dir", "D:/qlib/qlib_data/cn_data")


def get_qlib_region() -> str:
    """获取 Qlib 区域"""
    qlib = get_qlib_config()
    return qlib.get("region", "cn")


def get_qlib_freq() -> str:
    """获取 Qlib 数据频率"""
    qlib = get_qlib_config()
    return qlib.get("freq", "day")


def get_qlib_calendar_path() -> str:
    """获取 Qlib 日历文件路径"""
    return os.path.join(get_qlib_bin_dir(), "calendars", "day.txt")


def get_qlib_instruments_dir() -> str:
    """获取 Qlib instruments 目录路径"""
    return os.path.join(get_qlib_bin_dir(), "instruments")


def get_qlib_features_dir() -> str:
    """获取 Qlib features 目录路径"""
    return os.path.join(get_qlib_bin_dir(), "features")


def get_qlib_local_path() -> str:
    """获取 Qlib 本地源码路径（用于 Windows 兼容性补丁）"""
    env_val = os.environ.get("QLIB_LOCAL_PATH")
    if env_val:
        return env_val
    qlib = get_qlib_config()
    return qlib.get("local_path", "D:/qlib")


def get_wind_json_dir() -> str:
    """获取 Wind MCP JSON 数据目录

    已弃用 (2026-09-14): Wind MCP 自动化管线已迁移至 akshare。
    此函数仅保留用于历史脚本兼容。
    """
    env_val = os.environ.get("WIND_JSON_DIR")
    if env_val:
        return env_val
    wind = get_wind_config()
    return wind.get("json_dir", "D:/AgentAlpha/my-bot/data/wind")


def get_wind_symbols_file() -> str:
    """获取 Wind 标的列表文件路径

    已弃用 (2026-09-14): Wind MCP 自动化管线已迁移至 akshare。
    此函数仅保留用于历史脚本兼容。
    """
    wind = get_wind_config()
    return wind.get("symbols_file", "D:/AgentAlpha/my-bot/data/watchlist.json")


def get_pit_enabled() -> bool:
    """获取 PIT 引擎是否启用"""
    pit = get_pit_config()
    return pit.get("enabled", True)


def get_pit_release_delays() -> Dict[str, int]:
    """获取 PIT 数据发布延迟配置"""
    pit = get_pit_config()
    return pit.get("release_delays", {"financial": 30, "market": 0})


def get_pit_forbidden_operations() -> list:
    """获取 PIT 禁止的操作列表"""
    pit = get_pit_config()
    return pit.get("forbidden_operations", [])


def get_sync_schedule() -> str:
    """获取数据同步计划"""
    sync = get_sync_config()
    return sync.get("schedule", "daily_after_close")


def get_sync_retention_days() -> int:
    """获取数据保留天数"""
    sync = get_sync_config()
    return sync.get("retention_days", 1825)


def get_sync_auto_dump_bin() -> bool:
    """获取是否自动转换为 bin 格式"""
    sync = get_sync_config()
    return sync.get("auto_dump_bin", True)


# ─── Layer 0 & 1 新增配置访问器（2026-09-09）───


def get_macro_config() -> Dict[str, Any]:
    """获取 Layer 0 宏观资产配置"""
    config = get_config()
    return config.get("macro_allocation", {})


def get_sector_config() -> Dict[str, Any]:
    """获取 Layer 1 板块轮动配置"""
    config = get_config()
    return config.get("sector_rotation", {})


def get_macro_data_dir() -> str:
    """获取宏观资产数据目录"""
    macro = get_macro_config()
    data = macro.get("data", {})
    return data.get("cache_dir", "data/macro_assets")


def get_sector_data_dir() -> str:
    """获取板块数据目录"""
    sector = get_sector_config()
    data = sector.get("data", {})
    return data.get("cache_dir", "data/sector")


def get_macro_output_dir() -> str:
    """获取宏观资产输出目录"""
    macro = get_macro_config()
    output = macro.get("output", {})
    return output.get("allocation_file", "data/output/asset_allocation.json")


def get_sector_output_dir() -> str:
    """获取板块信号输出目录"""
    sector = get_sector_config()
    output = sector.get("output", {})
    return output.get("signal_file", "data/output/sector_signals.json")


def reload_config():
    """强制重新加载配置（用于配置更新后）"""
    global _config_cache, _config_loaded
    _config_cache = None
    _config_loaded = False
    return _load_config()