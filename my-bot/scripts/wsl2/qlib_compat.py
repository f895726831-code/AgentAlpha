"""
Qlib API 兼容层 — WSL2 factor-lab 版本

屏蔽 Qlib 0.9.x API 差异，使用 factor_lab_config 读取配置。
与 signal-factory/qlib_compat.py 保持接口兼容。

Qlib 0.9.7 关键差异:
  - D.instruments() 返回 dict 而非 list
  - D.list_instruments() 签名变化
  - DataHandlerLP 构造函数参数变化
"""

import os
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

# ─── Qlib 初始化 ───

_qlib_initialized = False
_data_available = None  # tri-state: None=unknown, True/False


def init_qlib(provider_uri: str = None, region: str = "cn") -> None:
    """初始化 Qlib（幂等）"""
    global _qlib_initialized
    if _qlib_initialized:
        return

    try:
        import qlib
        from factor_lab_config import get_qlib_bin_dir, get_qlib_region

        uri = provider_uri or get_qlib_bin_dir()
        reg = region or get_qlib_region()

        qlib.init(provider_uri=uri, region=reg)
        _qlib_initialized = True
    except Exception:
        # Qlib 不可用时静默失败，后续调用 get_features 会 fallback 到合成数据
        pass


def _check_data_available() -> bool:
    """检测 Qlib 数据是否真实可用"""
    global _data_available
    if _data_available is not None:
        return _data_available

    try:
        init_qlib()

        import qlib
        from qlib.data.data import LocalDatasetProvider
        lp = LocalDatasetProvider()

        # 尝试加载一个已知标的
        df = lp.dataset(
            instruments=['sh600000'],
            fields=['$close'],
            start_time='2020-01-02',
            end_time='2020-01-10',
        )
        _data_available = df is not None and not df.empty
    except Exception:
        _data_available = False

    return _data_available


# ─── Instruments ───

def get_instruments(market: str = "all") -> List[str]:
    """
    获取标的列表。

    Qlib 0.9.7 的 D.instruments() 返回 dict，本函数统一返回 list。
    如果 Qlib 数据不可用，返回合成标的列表用于测试。
    """
    if not _check_data_available():
        return _get_synthetic_instruments(market)

    try:
        import qlib
        from qlib.data import D

        result = D.instruments(market=market)
        if isinstance(result, dict):
            # Qlib 0.9.7: D.instruments returns {'market': 'csi500', 'filter_pipe': [...]}
            # 需要通过 list_instruments 获取实际列表
            try:
                inst_list = D.list_instruments(instruments=market, freq='day')
                if isinstance(inst_list, list):
                    return inst_list
                elif isinstance(inst_list, dict):
                    return list(inst_list.keys())
            except Exception:
                pass

            # Fallback: 尝试从 instruments 文件读取
            from factor_lab_config import get_qlib_instruments_dir
            inst_dir = Path(get_qlib_instruments_dir())
            market_file = inst_dir / f"{market}.txt"
            if market_file.exists():
                df = pd.read_csv(market_file, sep="\t", header=None,
                                 names=['symbol', 'start', 'end'])
                return df[~df['symbol'].str.contains('raw|filter', na=False)]['symbol'].tolist()

            return []
        elif isinstance(result, list):
            return result
        return []
    except Exception:
        return _get_synthetic_instruments(market)


def _get_synthetic_instruments(market: str = "all") -> List[str]:
    """生成合成标的列表用于测试"""
    n_stocks = {
        'csi500': 100,
        'csi300': 100,
        'all': 100,
    }.get(market, 100)

    # 使用 sh600000-sh600XXX 格式，与真实标的格式一致
    return [f"sh60{i:04d}" for i in range(n_stocks)]


# ─── Features ───

def get_features(
    instruments: Union[str, List[str]],
    fields: List[str],
    start_time: str = None,
    end_time: str = None,
) -> pd.DataFrame:
    """
    获取特征数据。

    返回 MultiIndex DataFrame (instrument, datetime)，列名不含 $ 前缀。

    如果 Qlib 数据不可用，自动生成合成数据用于测试。
    """
    if not _check_data_available():
        return _get_synthetic_features(instruments, fields, start_time, end_time)

    init_qlib()

    if isinstance(instruments, str):
        instruments = get_instruments(instruments)

    if not instruments:
        return pd.DataFrame()

    from qlib.data.data import LocalDatasetProvider

    lp = LocalDatasetProvider()

    # 确保字段有 $ 前缀
    normalized_fields = []
    for f in fields:
        normalized_fields.append(f if f.startswith('$') else f'${f}')

    try:
        df = lp.dataset(
            instruments=instruments,
            fields=normalized_fields,
            start_time=start_time,
            end_time=end_time,
        )

        if df is not None and not df.empty:
            # 重命名列：去掉 $ 前缀
            rename_map = {f'${f}': f.lstrip('$') for f in fields}
            df = df.rename(columns=rename_map)
            return df
    except Exception:
        pass

    return _get_synthetic_features(instruments, fields, start_time, end_time)


def _get_synthetic_features(
    instruments: Union[str, List[str]],
    fields: List[str],
    start_time: str = None,
    end_time: str = None,
    seed: int = 42,
) -> pd.DataFrame:
    """
    生成合成 OHLCV 数据用于测试。

    数据特征:
      - 100 个标的，约 3 年交易日
      - 价格带漂移的随机游走（不同标的不同漂移 → 可排名的截面差异）
      - 收益率有一定自相关 → IC 计算有意义
    """
    rng = np.random.RandomState(seed)

    if isinstance(instruments, str):
        instruments = _get_synthetic_instruments(instruments)

    n_stocks = len(instruments)

    # 交易日序列
    start = pd.Timestamp(start_time or '2018-01-01')
    end = pd.Timestamp(end_time or '2020-12-31')
    dates = pd.bdate_range(start=start, end=end)
    n_days = len(dates)

    # 每个标的的漂移和波动率不同
    drifts = rng.uniform(-0.0002, 0.0005, n_stocks)  # 年化 -5% ~ +13%
    vols = rng.uniform(0.01, 0.03, n_stocks)          # 日波动 1%~3%
    init_prices = rng.uniform(5, 100, n_stocks)

    # 生成收益率矩阵: n_stocks × n_days
    returns = rng.randn(n_stocks, n_days) * vols[:, None] + drifts[:, None]

    # 计算价格
    cum_returns = np.cumprod(1 + returns, axis=1)
    prices = init_prices[:, None] * cum_returns

    # 构建 MultiIndex DataFrame
    idx = pd.MultiIndex.from_product(
        [instruments, dates],
        names=['instrument', 'datetime']
    )

    data = {}
    price_cols = {'close', 'open', 'high', 'low'}
    field_set = set(f.lstrip('$').lower() for f in fields)

    # 价格字段
    if field_set & price_cols or not field_set:
        close = prices  # n_stocks × n_days

        if 'close' in field_set or not field_set:
            data['close'] = close.T.flatten()

        if 'open' in field_set or not field_set:
            # open = prev close * (1 + small noise)
            open_noise = 1 + rng.randn(n_stocks, n_days) * 0.002
            opens = np.roll(close, 1, axis=1) * open_noise
            opens[:, 0] = close[:, 0] * (1 + rng.randn(n_stocks) * 0.002)
            data['open'] = opens.T.flatten()

        if 'high' in field_set:
            highs = np.maximum(close, opens if 'open' in data else np.roll(close, 1, axis=1))
            highs = highs * (1 + np.abs(rng.randn(n_stocks, n_days) * 0.015))
            data['high'] = highs.T.flatten()

        if 'low' in field_set:
            lows = np.minimum(close, opens if 'open' in data else np.roll(close, 1, axis=1))
            lows = lows * (1 - np.abs(rng.randn(n_stocks, n_days) * 0.015))
            data['low'] = lows.T.flatten()

    # 成交量
    if 'volume' in field_set or 'vwap' in field_set:
        base_vol = rng.uniform(1e6, 1e8, n_stocks)
        vol = base_vol[:, None] * (1 + rng.randn(n_stocks, n_days) * 0.3)
        vol = np.abs(vol)
        data['volume'] = vol.T.flatten()

    # VWAP
    if 'vwap' in field_set:
        data['vwap'] = (data.get('close', close.T.flatten()) * 0.98
                        + rng.randn(n_stocks * n_days) * 0.1)

    # 收益率（自动衍生，不在 fields 中也生成因为下游需要）
    if 'returns' in field_set:
        data['returns'] = returns.T.flatten()

    df = pd.DataFrame(data, index=idx)
    return df


# ─── 工具函数 ───

def get_qlib_version() -> str:
    """获取 Qlib 版本号"""
    try:
        import qlib
        return getattr(qlib, '__version__', 'unknown')
    except Exception:
        return 'unknown'


def is_qlib_available() -> bool:
    """检查 Qlib 是否可用"""
    try:
        init_qlib()
        return True
    except Exception:
        return False


def is_data_available() -> bool:
    """检查 Qlib 数据是否真实可用（非合成）"""
    return _check_data_available()