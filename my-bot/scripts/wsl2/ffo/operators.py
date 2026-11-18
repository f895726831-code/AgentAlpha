"""
FFO 操作符实现 — Alpha101 风格的截面与时序操作符

支持的操作符:

  截面操作符 (跨股票):
    Rank(expr)          — 截面排名 (0~1)
    Scale(expr)         — 截面标准化 (Z-score)
    Quantile(expr, N)   — 截面分位数分组

  时序操作符 (每股票独立):
    Ts_Mean(expr, d)    — d 日滚动均值
    Ts_Std(expr, d)     — d 日滚动标准差
    Ts_Max(expr, d)     — d 日滚动最大值
    Ts_Min(expr, d)     — d 日滚动最小值
    Ts_Rank(expr, d)    — d 日时序排名 (0~1)
    Ts_Sum(expr, d)     — d 日滚动和
    Ts_ArgMax(expr, d)  — d 日内最大值位置 (0~1)
    Ts_ArgMin(expr, d)  — d 日内最小值位置 (0~1)
    Ts_Corr(x, y, d)    — d 日滚动相关系数
    Ts_Cov(x, y, d)    — d 日滚动协方差

  滞后/差分:
    Delay(expr, d)      — d 日前值
    Delta(expr, d)      — expr - Delay(expr, d)
    Return(expr, d)     — (expr - Delay(expr,d)) / Delay(expr,d)

  逻辑/条件:
    If(cond, t, f)      — 条件选择
    Sign(expr)          — 符号函数
    Abs(expr)           — 绝对值
    Log(expr)           — 自然对数
    SignedPower(x, a)   — sign(x) * |x|^a
    Max(x, y)           — 逐元素最大值
    Min(x, y)           — 逐元素最小值
"""

import numpy as np
import pandas as pd
from typing import Any, Dict

from .parser import FieldRef, Number, BinOp, UnaryOp, FuncCall


# ─── 操作符注册表 ───

OPERATOR_REGISTRY: Dict[str, callable] = {}


def register(name: str):
    """装饰器: 注册操作符"""
    def decorator(func):
        OPERATOR_REGISTRY[name.lower()] = func
        return func
    return decorator


# ─── 内部辅助 ───

def _to_dataframe(data: Any) -> pd.DataFrame:
    """将 data 统一转换为 DataFrame (index=date, columns=stock)"""
    if isinstance(data, pd.DataFrame):
        return data
    if isinstance(data, pd.Series):
        return data.unstack()
    if isinstance(data, np.ndarray):
        return pd.DataFrame(data)
    raise TypeError(f"不支持的数据类型: {type(data)}")


def _align(data: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    """对齐 data 到 target 的 index/columns"""
    return data.reindex(index=target.index, columns=target.columns)


# ─── 截面操作符 ───

@register("rank")
def op_rank(data: pd.DataFrame, *args) -> pd.DataFrame:
    """截面排名。Rank(expr) — 跨股票排名，输出 0~1"""
    return data.rank(axis=1, pct=True)


@register("scale")
def op_scale(data: pd.DataFrame, *args) -> pd.DataFrame:
    """截面 Z-score 标准化。Scale(expr)"""
    mean = data.mean(axis=1)
    std = data.std(axis=1).replace(0, 1)
    return data.sub(mean, axis=0).div(std, axis=0)


@register("quantile")
def op_quantile(data: pd.DataFrame, *args) -> pd.DataFrame:
    """截面分位数分组。Quantile(expr, N)"""
    n_groups = int(args[0]) if args else 5
    return data.rank(axis=1, pct=True) * n_groups // 1 / n_groups


# ─── 时序操作符 ───

@register("ts_mean")
def op_ts_mean(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日滚动均值。Ts_Mean(expr, d)"""
    d = int(args[0])
    return data.rolling(window=d, min_periods=max(1, d//2)).mean()


@register("ts_std")
def op_ts_std(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日滚动标准差。Ts_Std(expr, d)"""
    d = int(args[0])
    return data.rolling(window=d, min_periods=max(1, d//2)).std()


@register("ts_max")
def op_ts_max(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日滚动最大值。Ts_Max(expr, d)"""
    d = int(args[0])
    return data.rolling(window=d, min_periods=max(1, d//2)).max()


@register("ts_min")
def op_ts_min(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日滚动最小值。Ts_Min(expr, d)"""
    d = int(args[0])
    return data.rolling(window=d, min_periods=max(1, d//2)).min()


@register("ts_rank")
def op_ts_rank(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日时序排名。Ts_Rank(expr, d) — 输出 0~1"""
    d = int(args[0])
    return data.rolling(window=d, min_periods=max(1, d//2)).apply(
        lambda x: x.rank(pct=True).iloc[-1], raw=False
    )


@register("ts_sum")
def op_ts_sum(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日滚动和。Ts_Sum(expr, d)"""
    d = int(args[0])
    return data.rolling(window=d, min_periods=max(1, d//2)).sum()


@register("ts_argmax")
def op_ts_argmax(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日内最大值位置。Ts_ArgMax(expr, d) — 输出 0~1"""
    d = int(args[0])
    def _argmax_pos(x):
        if len(x) < d // 2:
            return np.nan
        return np.argmax(x.values) / (len(x) - 1)
    return data.rolling(window=d, min_periods=max(1, d//2)).apply(_argmax_pos, raw=False)


@register("ts_argmin")
def op_ts_argmin(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日内最小值位置。Ts_ArgMin(expr, d) — 输出 0~1"""
    d = int(args[0])
    def _argmin_pos(x):
        if len(x) < d // 2:
            return np.nan
        return np.argmin(x.values) / (len(x) - 1)
    return data.rolling(window=d, min_periods=max(1, d//2)).apply(_argmin_pos, raw=False)


@register("ts_corr")
def op_ts_corr(x: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日滚动相关系数。Ts_Corr(x, y, d) — 需要特殊处理（两个参数）"""
    # 在计算机中特殊处理，此处占位
    raise NotImplementedError("Ts_Corr 在两参数模式下由计算机直接处理")


@register("ts_cov")
def op_ts_cov(x: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日滚动协方差。Ts_Cov(x, y, d) — 需要特殊处理"""
    raise NotImplementedError("Ts_Cov 在两参数模式下由计算机直接处理")


# ─── 滞后/差分 ───

@register("delay")
def op_delay(data: pd.DataFrame, *args) -> pd.DataFrame:
    """d 日前值。Delay(expr, d)"""
    d = int(args[0]) if args else 1
    return data.shift(d)


@register("delta")
def op_delta(data: pd.DataFrame, *args) -> pd.DataFrame:
    """Delta(expr, d) = expr - Delay(expr, d)"""
    d = int(args[0]) if args else 1
    return data - data.shift(d)


@register("return")
def op_return(data: pd.DataFrame, *args) -> pd.DataFrame:
    """Return(expr, d) = (expr - Delay(expr,d)) / Delay(expr,d)"""
    d = int(args[0]) if args else 1
    delayed = data.shift(d)
    return (data - delayed) / delayed.replace(0, np.nan)


# ─── 逻辑/条件 ───

@register("if")
def op_if(data: pd.DataFrame, *args) -> pd.DataFrame:
    """If(cond, t, f) — 需要特殊处理（三个参数）"""
    raise NotImplementedError("If 在三参数模式下由计算机直接处理")


@register("sign")
def op_sign(data: pd.DataFrame, *args) -> pd.DataFrame:
    """Sign(expr) — 符号函数"""
    return np.sign(data)


@register("abs")
def op_abs(data: pd.DataFrame, *args) -> pd.DataFrame:
    """Abs(expr) — 绝对值"""
    return data.abs()


@register("log")
def op_log(data: pd.DataFrame, *args) -> pd.DataFrame:
    """Log(expr) — 自然对数"""
    return np.log(data.replace(0, np.nan).clip(lower=1e-10))


@register("signedpower")
def op_signedpower(data: pd.DataFrame, *args) -> pd.DataFrame:
    """SignedPower(x, a) = sign(x) * |x|^a"""
    a = float(args[0]) if args else 1.0
    return np.sign(data) * (data.abs() ** a)


@register("max")
def op_max(data: pd.DataFrame, *args) -> pd.DataFrame:
    """Max(x, y) — 逐元素最大值"""
    # 需要两个参数，在计算机中特殊处理
    raise NotImplementedError("Max 在两参数模式下由计算机直接处理")


@register("min")
def op_min(data: pd.DataFrame, *args) -> pd.DataFrame:
    """Min(x, y) — 逐元素最小值"""
    raise NotImplementedError("Min 在两参数模式下由计算机直接处理")


# ─── 操作符辅助查询 ───

def get_registered_operators() -> list:
    """获取所有已注册的操作符名称"""
    return sorted(OPERATOR_REGISTRY.keys())


def is_operator(name: str) -> bool:
    """检查给定的名称是否是已注册的操作符"""
    return name.lower() in OPERATOR_REGISTRY


def call_operator(name: str, data: pd.DataFrame, *args) -> pd.DataFrame:
    """调用已注册的操作符"""
    name_lower = name.lower()
    if name_lower not in OPERATOR_REGISTRY:
        raise ValueError(f"未注册的操作符: '{name}'。可用: {get_registered_operators()}")
    return OPERATOR_REGISTRY[name_lower](data, *args)