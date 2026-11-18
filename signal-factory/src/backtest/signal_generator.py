"""
策略信号生成器 — PIT 安全的信号生成

从 AgentAlpha 迁移的策略信号逻辑，修复了所有未来函数问题：
  - breakout_60: 回踩确认从"前瞻"改为"后移信号"（方案A）或"触价即入"（方案B）
  - pullback_low: 验证通过（无未来函数）
  - vcp_breakout: 验证通过（无未来函数）
  - oversold_bounce: 验证通过（无未来函数）
  - golden_cross: 验证通过（无未来函数）
  - limit_up_chase: 入场价从最低价改为开盘价
"""

from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

# ─── 策略定义库 ───

STRATEGIES = {
    "breakout_60": {
        "name": "放量突破60元回踩",
        "description": "股价从60元下方放量突破60元，触价即入（已移除前瞻回踩确认）",
        "params": {
            "breakout_price": 60.0,
            "volume_threshold": 1.5,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 12.0,
        },
        "param_count": 4,
        "pit_fixed": True,  # 已修复未来函数
        "fix_description": "移除了需要未来3天数据的回踩确认条件，改为触价即入+止损",
    },
    "pullback_low": {
        "name": "回调低吸",
        "description": "上升趋势中回调至MA20附近，缩量企稳后入场",
        "params": {
            "ma_period": 20,
            "pullback_pct": 0.05,
            "volume_shrink_pct": 0.7,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 10.0,
        },
        "param_count": 5,
        "pit_fixed": True,  # 原始实现已正确
    },
    "vcp_breakout": {
        "name": "VCP收缩突破",
        "description": "波动收缩形态末端放量突破",
        "params": {
            "vcp_periods": 3,
            "contraction_ratio": 0.5,
            "breakout_volume_ratio": 1.5,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 15.0,
        },
        "param_count": 5,
        "pit_fixed": True,
    },
    "oversold_bounce": {
        "name": "超跌反弹",
        "description": "RSI超卖(<30)后反弹，配合缩量止跌确认",
        "params": {
            "rsi_period": 14,
            "rsi_oversold": 30,
            "volume_shrink_pct": 0.6,
            "stop_loss_pct": 3.0,
            "take_profit_pct": 8.0,
        },
        "param_count": 5,
        "pit_fixed": True,
    },
    "golden_cross": {
        "name": "均线金叉",
        "description": "MA20上穿MA60，配合成交量放大确认",
        "params": {
            "ma_short": 20,
            "ma_long": 60,
            "volume_confirm_ratio": 1.2,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 15.0,
        },
        "param_count": 5,
        "pit_fixed": True,
    },
    "limit_up_chase": {
        "name": "涨停追击",
        "description": "涨停次日高开回调买入，博弈连板",
        "params": {
            "limit_up_pct": 9.8,
            "next_day_open_threshold": 0.03,
            "entry_pullback_pct": 0.02,
            "stop_loss_pct": 5.0,
            "take_profit_pct": 10.0,
        },
        "param_count": 5,
        "pit_fixed": True,
        "fix_description": "入场价从最低价改为开盘价，不再假设完美成交在最低点",
    },
}


def get_strategy_info(strategy_name: str) -> dict:
    """获取策略元信息"""
    if strategy_name not in STRATEGIES:
        raise ValueError(f"未知策略: {strategy_name}. 可用: {list(STRATEGIES.keys())}")
    return STRATEGIES[strategy_name]


def list_strategies() -> List[dict]:
    """列出所有可用策略及 PIT 合规状态"""
    return [
        {
            "key": name,
            "name": s["name"],
            "description": s["description"],
            "param_count": s["param_count"],
            "pit_safe": s.get("pit_fixed", False),
            "fix_note": s.get("fix_description", ""),
        }
        for name, s in STRATEGIES.items()
    ]


# ─── 指标计算 ───

def calc_ma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = (-delta.clip(upper=0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calc_atr(high, low, close, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


# ─── 信号生成函数（PIT 安全版本） ───

def generate_breakout_60(data: pd.DataFrame, params: dict) -> pd.Series:
    """
    放量突破60元 — PIT 安全版本。

    修复: 移除了 close.iloc[i+1:i+4] 的前瞻回踩确认。
    改为: 突破当天触价即入，止损控制假突破风险。
    符合方法论: [[entry-confirmation-must-filter-losses]]
    """
    close = data["close"]
    volume = data["volume"]
    avg_vol_5 = volume.rolling(5).mean()

    signal = pd.Series(0, index=data.index)

    for i in range(30, len(data)):
        # 前30日收盘价均低于60元
        past_30 = close.iloc[max(0, i - 30) : i]
        if past_30.max() >= params["breakout_price"]:
            continue

        # 当日收盘价突破60元
        if close.iloc[i] <= params["breakout_price"]:
            continue

        # 成交量确认
        if volume.iloc[i] < avg_vol_5.iloc[i] * params["volume_threshold"]:
            continue

        # 触价即入（不再前瞻确认回踩）
        signal.iloc[i] = 1

    return signal


def generate_pullback_low(data: pd.DataFrame, params: dict) -> pd.Series:
    """回调低吸 — PIT 安全（原始实现已正确）"""
    close = data["close"]
    volume = data["volume"]
    ma = calc_ma(close, params["ma_period"])
    avg_vol_5 = volume.rolling(5).mean()

    signal = pd.Series(0, index=data.index)

    for i in range(params["ma_period"], len(data)):
        if pd.isna(ma.iloc[i]):
            continue

        # 价格回调至MA下方 pullback_pct 以内
        if close.iloc[i] > ma.iloc[i] or close.iloc[i] < ma.iloc[i] * (1 - params["pullback_pct"]):
            continue

        # 缩量确认
        if volume.iloc[i] > avg_vol_5.iloc[i] * params["volume_shrink_pct"]:
            continue

        signal.iloc[i] = 1

    return signal


def generate_vcp_breakout(data: pd.DataFrame, params: dict) -> pd.Series:
    """VCP收缩突破 — PIT 安全（原始实现已正确）"""
    close = data["close"]
    volume = data["volume"]
    avg_vol_5 = volume.rolling(5).mean()

    signal = pd.Series(0, index=data.index)

    for i in range(60, len(data)):
        period_close = close.iloc[i - 60 : i]
        if len(period_close) < 60:
            continue

        first_half_vol = period_close.iloc[:30].std()
        second_half_vol = period_close.iloc[30:].std()

        if first_half_vol == 0:
            continue

        contraction = second_half_vol / first_half_vol
        if contraction > params["contraction_ratio"]:
            continue

        if close.iloc[i] < period_close.max():
            continue

        if volume.iloc[i] < avg_vol_5.iloc[i] * params["breakout_volume_ratio"]:
            continue

        signal.iloc[i] = 1

    return signal


def generate_oversold_bounce(data: pd.DataFrame, params: dict) -> pd.Series:
    """超跌反弹 — PIT 安全（原始实现已正确）"""
    close = data["close"]
    volume = data["volume"]
    rsi = calc_rsi(close, params["rsi_period"])
    avg_vol_5 = volume.rolling(5).mean()

    signal = pd.Series(0, index=data.index)

    for i in range(params["rsi_period"] + 5, len(data)):
        if pd.isna(rsi.iloc[i]):
            continue

        if rsi.iloc[i] >= params["rsi_oversold"]:
            continue

        if rsi.iloc[i - 1] >= params["rsi_oversold"]:
            continue

        if volume.iloc[i] > avg_vol_5.iloc[i] * params["volume_shrink_pct"]:
            continue

        if close.iloc[i] <= close.iloc[i - 1]:
            continue

        signal.iloc[i] = 1

    return signal


def generate_golden_cross(data: pd.DataFrame, params: dict) -> pd.Series:
    """均线金叉 — PIT 安全（原始实现已正确）"""
    close = data["close"]
    volume = data["volume"]
    ma_short = calc_ma(close, params["ma_short"])
    ma_long = calc_ma(close, params["ma_long"])
    avg_vol_5 = volume.rolling(5).mean()

    signal = pd.Series(0, index=data.index)

    for i in range(params["ma_long"] + 1, len(data)):
        if pd.isna(ma_short.iloc[i]) or pd.isna(ma_long.iloc[i]):
            continue

        if ma_short.iloc[i] <= ma_long.iloc[i]:
            continue
        if ma_short.iloc[i - 1] >= ma_long.iloc[i - 1]:
            continue

        if volume.iloc[i] < avg_vol_5.iloc[i] * params["volume_confirm_ratio"]:
            continue

        signal.iloc[i] = 1

    return signal


def generate_limit_up_chase(data: pd.DataFrame, params: dict) -> pd.Series:
    """
    涨停追击 — PIT 安全版本。

    修复: 入场价从最低价 (low) 改为开盘价 (open)。
    原始代码假设限价单完美成交在日内最低点，这在实盘中不可靠。
    现改为以开盘价买入，更接近实盘可执行性。
    """
    close = data["close"]
    open_p = data["open"]

    signal = pd.Series(0, index=data.index)

    for i in range(60, len(data)):
        # 前一日涨停
        prev_return = (close.iloc[i - 1] - close.iloc[i - 2]) / close.iloc[i - 2]
        if prev_return < params["limit_up_pct"] / 100:
            continue

        # 次日高开
        gap = open_p.iloc[i] / close.iloc[i - 1] - 1
        if gap < params["next_day_open_threshold"]:
            continue

        # 高开幅度在阈值内 → 以开盘价入场
        if gap <= params["entry_pullback_pct"] + params["next_day_open_threshold"]:
            signal.iloc[i] = 1

    return signal


# ─── 信号生成调度 ───

def generate_signal(
    symbol: str,
    strategy_name: str,
    price_data: pd.DataFrame,
    override_params: Optional[dict] = None,
) -> pd.Series:
    """
    根据策略规则生成每日交易信号。

    参数:
        symbol: 标的代码
        strategy_name: 策略名称
        price_data: DataFrame with [open, high, low, close, volume], index=datetime
        override_params: 覆盖默认策略参数

    返回:
        pd.Series with MultiIndex (instrument, datetime), 1=buy, 0=neutral
    """
    strategy = get_strategy_info(strategy_name)
    params = {**strategy["params"], **(override_params or {})}

    generators = {
        "breakout_60": generate_breakout_60,
        "pullback_low": generate_pullback_low,
        "vcp_breakout": generate_vcp_breakout,
        "oversold_bounce": generate_oversold_bounce,
        "golden_cross": generate_golden_cross,
        "limit_up_chase": generate_limit_up_chase,
    }

    if strategy_name not in generators:
        raise ValueError(f"策略 {strategy_name} 的信号生成尚未实现")

    signal = generators[strategy_name](price_data, params)

    # 转换为 MultiIndex 格式 (Qlib 期望 instrument 在前)
    if isinstance(signal, pd.Series) and not isinstance(signal.index, pd.MultiIndex):
        idx = pd.MultiIndex.from_arrays(
            [[symbol] * len(signal), signal.index],
            names=["instrument", "datetime"],
        )
        signal = pd.Series(signal.values, index=idx)

    # Only keep buy signals (value > 0); set non-buy days to NaN
    # TopkDropoutStrategy ranks by score, so 0-score days would still trigger
    # trades. NaN values are ignored by the strategy.
    signal = signal.replace(0, np.nan).dropna()

    return signal


def get_signal_from_qlib(
    symbol: str,
    strategy_name: str,
    start_time: str,
    end_time: str,
    override_params: Optional[dict] = None,
) -> pd.Series:
    """
    从 Qlib 数据加载价格并生成信号。

    参数:
        symbol: 标的代码
        strategy_name: 策略名称
        start_time: 起始日期
        end_time: 结束日期
        override_params: 覆盖默认策略参数

    返回:
        pd.Series with MultiIndex (datetime, instrument)
    """
    from qlib_compat import get_features_raw

    fields = ["$open", "$high", "$low", "$close", "$volume"]
    df = get_features_raw([symbol], fields, start_time=start_time, end_time=end_time)

    if df.empty:
        raise ValueError(f"无数据: {symbol} ({start_time} ~ {end_time})")

    df = df.reset_index()
    df = df.rename(columns={
        "$open": "open", "$high": "high", "$low": "low",
        "$close": "close", "$volume": "volume",
    })
    df = df.set_index("datetime")

    return generate_signal(symbol, strategy_name, df, override_params)


# ─── PIT 自检 ───

def self_check_pit() -> Dict[str, bool]:
    """对所有策略进行 PIT 合规自检"""
    import inspect
    from src.data.pit_handler import detect_pit_violation

    results = {}
    generators = {
        "breakout_60": generate_breakout_60,
        "pullback_low": generate_pullback_low,
        "vcp_breakout": generate_vcp_breakout,
        "oversold_bounce": generate_oversold_bounce,
        "golden_cross": generate_golden_cross,
        "limit_up_chase": generate_limit_up_chase,
    }

    for name, func in generators.items():
        try:
            source = inspect.getsource(func)
            violations = detect_pit_violation(source, name)
            results[name] = {
                "passed": len([v for v in violations if v["severity"] in ("critical", "high")]) == 0,
                "violations": violations,
            }
        except Exception:
            results[name] = {"passed": False, "violations": [], "error": "无法获取源码"}

    return results