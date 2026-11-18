"""
Qlib API 兼容层 — 屏蔽 Qlib 0.8.x 与 0.9.7 之间的 API 差异

signal-factory 版本：从 src.data.config_loader 读取配置。

Qlib 0.9.7 的 API 变化：
  - D.instruments() 返回 dict 而非 list
  - D.features() 字段格式变化
  - DataHandlerLP 构造函数参数变化

本模块提供与 0.8.x 兼容的封装，内部调用 0.9.7 API。
"""

import os
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

# ─── Qlib 初始化 ───

_qlib_initialized = False


def init_qlib(provider_uri: str = None, region: str = "cn") -> None:
    """
    初始化 Qlib（幂等，多次调用不重复初始化）。
    """
    global _qlib_initialized
    if _qlib_initialized:
        return

    import qlib
    from src.data.config_loader import get_qlib_bin_dir, get_qlib_region

    uri = provider_uri or get_qlib_bin_dir()
    reg = region or get_qlib_region()

    qlib.init(provider_uri=uri, region=reg)
    _qlib_initialized = True


def get_instruments(market: str = "all") -> List[str]:
    """
    获取标的列表（兼容 D.instruments(market='all')）。
    Qlib 0.9.7 的 D.instruments() 返回 dict，本函数统一返回 list。
    """
    from src.data.config_loader import get_qlib_instruments_dir

    instruments_dir = Path(get_qlib_instruments_dir())
    market_file = instruments_dir / f"{market}.txt"

    if not market_file.exists():
        market_file = instruments_dir / "all.txt"

    if market_file.exists():
        df = pd.read_csv(market_file, sep="\t", header=None, names=['symbol', 'start', 'end'])
        stocks = df[~df['symbol'].str.contains('raw|filter', na=False)]['symbol'].tolist()
        return stocks

    # 回退：尝试 Qlib 0.9.7 API
    try:
        from qlib.data.data import LocalDatasetProvider
        lp = LocalDatasetProvider()
        result = lp.get_instruments_d(instruments=market, freq="day")
        if isinstance(result, dict):
            return list(result.keys())
        elif isinstance(result, list):
            return result
    except Exception:
        pass

    return []


def get_features(
    instruments: Union[str, List[str]],
    fields: List[str],
    start_time: str = None,
    end_time: str = None,
) -> pd.DataFrame:
    """
    获取特征数据（兼容 D.features()）。
    返回 MultiIndex DataFrame (instrument, datetime)，列名去掉 $ 前缀。
    """
    init_qlib()

    if isinstance(instruments, str):
        instruments = get_instruments(instruments)

    if not instruments:
        return pd.DataFrame()

    from qlib.data.data import LocalDatasetProvider

    lp = LocalDatasetProvider()

    normalized_fields = []
    for f in fields:
        if not f.startswith("$"):
            normalized_fields.append(f"${f}")
        else:
            normalized_fields.append(f)

    df = lp.dataset(
        instruments=instruments,
        fields=normalized_fields,
        start_time=start_time,
        end_time=end_time,
    )

    # 重命名列：去掉 $ 前缀以保持与 0.8.x 兼容
    rename_map = {f"${f}": f.lstrip("$") for f in fields if f"${f}" in df.columns}
    if rename_map:
        df = df.rename(columns=rename_map)

    return df


def get_features_raw(
    instruments: Union[str, List[str]],
    fields: List[str],
    start_time: str = None,
    end_time: str = None,
) -> pd.DataFrame:
    """
    获取特征数据（保留 $ 前缀的原始列名）。
    """
    init_qlib()

    if isinstance(instruments, str):
        instruments = get_instruments(instruments)

    if not instruments:
        return pd.DataFrame()

    from qlib.data.data import LocalDatasetProvider

    lp = LocalDatasetProvider()

    normalized_fields = []
    for f in fields:
        if not f.startswith("$"):
            normalized_fields.append(f"${f}")
        else:
            normalized_fields.append(f)

    return lp.dataset(
        instruments=instruments,
        fields=normalized_fields,
        start_time=start_time,
        end_time=end_time,
    )


def get_data_handler(
    instruments: Union[str, List[str]],
    start_time: str,
    end_time: str,
    handler_type: str = "alpha158",
):
    """
    获取 DataHandler（兼容 DataHandlerLP）。
    """
    init_qlib()

    from qlib.data.dataset.handler import DataHandlerLP

    if isinstance(instruments, str):
        instruments = get_instruments(instruments)

    return DataHandlerLP(
        instruments=instruments,
        start_time=start_time,
        end_time=end_time,
    )


def get_qlib_version() -> str:
    """获取 Qlib 版本号"""
    try:
        import qlib
        return getattr(qlib, "__version__", "unknown")
    except Exception:
        return "unknown"


def is_qlib_available() -> bool:
    """检查 Qlib 是否可用"""
    try:
        init_qlib()
        return True
    except Exception:
        return False


def get_calendar_dates() -> List[str]:
    """获取交易日历"""
    from src.data.config_loader import get_qlib_calendar_path
    cal_path = Path(get_qlib_calendar_path())
    if cal_path.exists():
        with open(cal_path) as f:
            return [l.strip() for l in f if l.strip()]
    return []


def get_latest_date() -> str:
    """获取最新交易日"""
    dates = get_calendar_dates()
    return dates[-1] if dates else ""


def forward_returns(
    instruments: Union[str, List[str]],
    periods: List[int] = [1, 5, 20],
    start_time: str = None,
    end_time: str = None,
) -> pd.DataFrame:
    """
    计算前向收益率（用于 IC 计算）。
    """
    init_qlib()

    if isinstance(instruments, str):
        instruments = get_instruments(instruments)

    from qlib.data.data import LocalDatasetProvider
    lp = LocalDatasetProvider()

    fields = ["$close"]
    for p in periods:
        fields.append(f"Ref($close, -{p})/$close - 1")

    df = lp.dataset(
        instruments=instruments,
        fields=fields,
        start_time=start_time,
        end_time=end_time,
    )

    col_names = ["close"] + [f"fwd_{p}d" for p in periods]
    df.columns = col_names

    return df