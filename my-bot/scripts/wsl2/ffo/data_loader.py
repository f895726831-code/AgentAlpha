"""
FFO 数据加载器 — 通过 Qlib API 加载 A 股市场数据

使用 qlib_compat 模块的 get_features() 加载 Qlib bin 数据。
返回 MultiIndex DataFrame: (instrument, datetime) → features
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


# ─── CSI500 最早有效日期 ───
CSI500_MIN_DATE = "2015-01-05"


# ─── 内存缓存 ───

_cache: Dict[str, pd.DataFrame] = {}


def clear_cache():
    """清除数据缓存"""
    _cache.clear()


# ─── Qlib 数据加载 ───

def _init_qlib():
    """初始化 Qlib（幂等）"""
    try:
        from qlib_compat import init_qlib
        init_qlib()
    except ImportError:
        import qlib
        from factor_lab_config import get_qlib_bin_dir, get_qlib_region
        qlib.init(provider_uri=get_qlib_bin_dir(), region=get_qlib_region())


def _get_instruments(universe: str) -> List[str]:
    """获取股票列表"""
    try:
        from qlib_compat import get_instruments
        return get_instruments(universe)
    except ImportError:
        pass

    # Fallback: read instruments file directly
    from factor_lab_config import get_qlib_bin_dir
    instruments_dir = Path(get_qlib_bin_dir()) / "instruments"
    market_file = instruments_dir / f"{universe}.txt"
    if not market_file.exists():
        market_file = instruments_dir / "all.txt"
    if not market_file.exists():
        return []

    stocks = []
    with open(market_file) as f:
        for line in f:
            parts = line.strip().split("\t")
            if parts and parts[0]:
                stocks.append(parts[0])
    return stocks


def load_qlib_data(
    data_dir: str = None,
    universe: str = "csi500",
    start: str = None,
    end: str = None,
    fields: List[str] = None,
) -> pd.DataFrame:
    """
    从 Qlib bin 加载市场数据。

    参数:
        data_dir: Qlib bin 目录 (未使用，由 qlib_compat 管理)
        universe: 股票池 (csi500 / csi300 / all)
        start:    起始日期 (YYYY-MM-DD)
        end:      结束日期 (YYYY-MM-DD)
        fields:   加载的字段列表 (默认: 全量 OHLCV)

    返回:
        pd.DataFrame with MultiIndex (instrument, datetime), columns = features
    """
    _init_qlib()

    # 防止加载 CSI500 启动前的垃圾数据
    if start and universe == "csi500":
        min_dt = pd.Timestamp(CSI500_MIN_DATE)
        start_dt = pd.Timestamp(start)
        if start_dt < min_dt:
            start = CSI500_MIN_DATE

    if fields is None:
        fields = ["$close", "$open", "$high", "$low", "$volume", "$vwap",
                  "$roe", "$eps", "$bps", "$gross_margin"]

    cache_key = f"{universe}:{start}:{end}:{','.join(sorted(fields))}"
    if cache_key in _cache:
        return _cache[cache_key].copy()

    try:
        from qlib_compat import get_features
        df = get_features(
            instruments=universe,
            fields=fields,
            start_time=start,
            end_time=end,
        )
    except ImportError:
        # Direct Qlib API fallback
        import qlib
        from qlib.data.data import LocalDatasetProvider

        instruments = _get_instruments(universe)
        if not instruments:
            raise ValueError(f"未找到 {universe} 的股票列表")

        lp = LocalDatasetProvider()
        df = lp.dataset(
            instruments=instruments,
            fields=fields,
            start_time=start,
            end_time=end,
        )

    if df is None or df.empty:
        raise ValueError(f"未能加载 {universe} 的数据 ({start} → {end})")

    # 标准化列名: 去掉 $ 前缀
    rename_map = {c: c.lstrip("$").lower() for c in df.columns if c.startswith("$")}
    if rename_map:
        df = df.rename(columns=rename_map)

    # 自动计算衍生字段
    # $returns 不是 Qlib 原生字段，需从 close 计算
    # 条件: 没有 returns 列，或者 returns 列全是 NaN（Qlib 返回了无效列）
    if "close" in df.columns:
        if "returns" not in df.columns or df["returns"].notna().sum() == 0:
            close_matrix = df["close"].unstack().T
            returns_matrix = close_matrix.pct_change(fill_method=None)
            df["returns"] = returns_matrix.stack().reorder_levels([1, 0])

    # VWAP fallback: if vwap is all NaN, compute from (high+low+close)/3
    if "vwap" in df.columns and df["vwap"].notna().sum() == 0:
        if all(c in df.columns for c in ["high", "low", "close"]):
            df["vwap"] = (df["high"] + df["low"] + df["close"]) / 3.0

    _cache[cache_key] = df
    return df.copy()


# ─── 便捷接口 ───

def get_field_matrix(df: pd.DataFrame, field: str) -> pd.DataFrame:
    """
    从 MultiIndex DataFrame 提取单字段的面板数据。

    输入: (instrument, datetime) → features
    输出: datetime × instrument matrix
    """
    if field not in df.columns:
        raise KeyError(f"字段 '{field}' 不存在。可用: {list(df.columns)}")

    return df[field].unstack().T  # (instrument, date) → (date, instrument)


def get_returns_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """获取收益率面板数据。返回 date × instrument matrix"""
    if "close" in df.columns:
        close_matrix = get_field_matrix(df, "close")
        return close_matrix.pct_change(fill_method=None)
    raise KeyError("无法计算收益率: 缺少 close 字段")


# ─── 路径探测 ───

def _find_qlib_data_dir(hint: str = None) -> Optional[str]:
    """自动探测 Qlib bin 数据目录"""
    try:
        from factor_lab_config import get_qlib_bin_dir
        path = get_qlib_bin_dir()
        if Path(path).exists():
            return path
    except ImportError:
        pass

    candidates = [
        hint,
        os.environ.get("QLIB_DATA_DIR"),
        "/mnt/d/qlib/qlib_data/cn_data",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None