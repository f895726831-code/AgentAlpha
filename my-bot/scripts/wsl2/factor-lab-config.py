"""
factor-lab 配置模块 — WSL2 原生环境

通过环境变量覆盖默认路径，与 signal-factory 的 config_loader.py 保持接口兼容。

用法:
    from config import get_qlib_bin_dir, get_qlib_region, get_qlib_csv_dir

    bin_dir = get_qlib_bin_dir()
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

# ─── 默认配置（WSL2 环境） ───

_DEFAULTS: Dict[str, Any] = {
    "qlib": {
        "bin_dir": "/mnt/d/qlib/qlib_data/cn_data",
        "csv_dir": "/mnt/d/qlib/qlib_data/source/cn_1d",
        "region": "cn",
        "freq": "day",
        "local_path": "/mnt/d/qlib",
    },
    "pit": {
        "enabled": True,
        "release_delays": {"financial": 30, "market": 0},
        "forbidden_operations": [],
    },
}


def get_qlib_bin_dir() -> str:
    """获取 Qlib bin 数据目录"""
    return os.environ.get("QLIB_DATA_DIR", _DEFAULTS["qlib"]["bin_dir"])


def get_qlib_csv_dir() -> str:
    """获取 Qlib CSV 源数据目录"""
    return os.environ.get("QLIB_CSV_DIR", _DEFAULTS["qlib"]["csv_dir"])


def get_qlib_region() -> str:
    """获取 Qlib 区域"""
    return os.environ.get("QLIB_REGION", _DEFAULTS["qlib"]["region"])


def get_qlib_freq() -> str:
    """获取 Qlib 数据频率"""
    return os.environ.get("QLIB_FREQ", _DEFAULTS["qlib"]["freq"])


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
    """获取 Qlib 本地源码路径"""
    return os.environ.get("QLIB_LOCAL_PATH", _DEFAULTS["qlib"]["local_path"])


def get_pit_enabled() -> bool:
    """获取 PIT 引擎是否启用"""
    return os.environ.get("PIT_ENABLED", "true").lower() == "true"


def get_pit_release_delays() -> Dict[str, int]:
    """获取 PIT 数据发布延迟配置"""
    return _DEFAULTS["pit"]["release_delays"]


def get_pit_forbidden_operations() -> list:
    """获取 PIT 禁止的操作列表"""
    return _DEFAULTS["pit"]["forbidden_operations"]