"""
Point-in-Time 数据处理器

确保在任意历史时刻 T，只能访问 T 时刻及之前可用的数据。
这是防止未来函数的第一道防线。

核心原则：
  1. 行情数据: T 日收盘后可用，T+1 日开盘前可用
  2. 财务数据: T 日财报发布后可用，发布延迟通常 30-90 天
  3. 因子计算: factor[t] 只能依赖 data[:t]，不得使用 data[t+1:]
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Union
import warnings

import numpy as np
import pandas as pd


class PITDataHandler:
    """
    Point-in-Time 数据处理器。

    包装 Qlib 的 D.features() 调用，增加时间门禁：
    - 获取 T 时刻的数据时，自动过滤 T 时刻之后的数据
    - 支持按数据类别设置发布延迟
    - 提供便捷方法检查数据可用性

    用法:
        handler = PITDataHandler(release_delays={"financial": 30, "market": 0})
        data = handler.get_features(
            instruments=["sh600519"],
            fields=["$close", "$volume"],
            as_of="2026-08-01",
        )
    """

    # 默认发布延迟（天）
    DEFAULT_RELEASE_DELAYS = {
        "market": 0,       # 行情数据 T+0
        "financial": 30,   # 财报数据 T+30 (保守估计)
        "index_weight": 0, # 指数权重 T+0
        "analyst": 7,      # 分析师预测 T+7
    }

    def __init__(
        self,
        release_delays: Optional[Dict[str, int]] = None,
        bin_dir: Optional[str] = None,
    ):
        self.release_delays = {**self.DEFAULT_RELEASE_DELAYS, **(release_delays or {})}
        self.bin_dir = bin_dir

        self._qlib_initialized = False
        self._data_cache: Dict[str, pd.DataFrame] = {}

    def _init_qlib(self):
        """延迟初始化 Qlib"""
        if self._qlib_initialized:
            return
        from qlib_compat import init_qlib
        init_qlib()
        self._qlib_initialized = True

    def get_features(
        self,
        instruments: Union[str, List[str]],
        fields: List[str],
        as_of: str,
        lookback_days: int = 252,
    ) -> pd.DataFrame:
        """
        获取 as_of 时刻可用的数据。

        参数:
            instruments: 标的代码或列表
            fields: 字段列表 (如 ["$close", "$volume", "$open"])
            as_of: 截止日期 (YYYY-MM-DD)，只返回这一天及之前的数据
            lookback_days: 回溯天数

        返回:
            pd.DataFrame with MultiIndex (datetime, instrument)
        """
        self._init_qlib()
        from qlib_compat import get_features_raw

        as_of_dt = pd.Timestamp(as_of)
        start_dt = as_of_dt - pd.Timedelta(days=lookback_days)

        if isinstance(instruments, str):
            instruments = [instruments]

        # 获取原始数据
        df = get_features_raw(
            instruments,
            fields,
            start_time=str(start_dt),
            end_time=str(as_of_dt),
        )

        if df.empty:
            return df

        # 按字段分类，应用发布延迟
        df = self._apply_release_delays(df, fields, as_of_dt)

        return df

    def _apply_release_delays(
        self,
        df: pd.DataFrame,
        fields: List[str],
        as_of: pd.Timestamp,
    ) -> pd.DataFrame:
        """
        根据字段类型应用发布延迟。

        财务数据 ($fs_, $roe, $pe 等) 在 as_of 时刻可能尚未发布，
        需要回退到 as_of - release_delay 之前的数据。
        """
        # 分类字段
        financial_fields = []
        for f in fields:
            f_name = f.replace("$", "").lower()
            if any(prefix in f_name for prefix in ("fs_", "roe", "pe", "pb", "eps", "bps", "cf_")):
                financial_fields.append(f)

        if not financial_fields:
            return df  # 纯行情数据，无需调整

        # 对财务字段应用延迟
        delay = self.release_delays.get("financial", 30)
        cutoff = as_of - pd.Timedelta(days=delay)

        # 将 cutoff 之后的财务数据设为 NaN
        for f in financial_fields:
            if f in df.columns:
                mask = df.index.get_level_values("datetime") > cutoff
                df.loc[mask, f] = np.nan

        return df

    def checkpoint(self, as_of: str) -> "PITCheckpoint":
        """
        创建一个 PIT 检查点，用于后续的因子计算和回测。

        检查点确保了所有后续计算都基于 as_of 时刻可用的数据。
        """
        return PITCheckpoint(as_of=as_of, handler=self)


class PITCheckpoint:
    """
    PIT 时间检查点。

    代表回测中的一个时间快照。所有在此检查点上的计算
    都只能访问 as_of 及之前的数据。

    用法:
        cp = handler.checkpoint("2026-08-01")
        prices = cp.get_prices("sh600519")
        # 这里 prices 只包含 2026-08-01 及之前的数据
    """

    def __init__(self, as_of: str, handler: PITDataHandler):
        self.as_of = pd.Timestamp(as_of)
        self.handler = handler

    def get_prices(self, instrument: str, lookback: int = 252) -> pd.DataFrame:
        """获取截至 as_of 的价格数据"""
        return self.handler.get_features(
            instruments=instrument,
            fields=["$open", "$high", "$low", "$close", "$volume"],
            as_of=str(self.as_of),
            lookback_days=lookback,
        )

    def __repr__(self):
        return f"PITCheckpoint(as_of={self.as_of})"


def detect_pit_violation(
    code: str,
    strategy_name: str = "unknown",
) -> List[Dict]:
    """
    检测代码中的 PIT 违规（未来函数）。

    使用正则表达式和简单的模式匹配来检测常见的前瞻操作。

    参数:
        code: 要检查的 Python 源代码
        strategy_name: 策略名称（用于错误报告）

    返回:
        List[Dict]: 违规列表，每项包含 {line, pattern, severity, suggestion}
    """
    violations = []

    # 违规模式定义
    patterns = [
        {
            "pattern": r"\.iloc\[\w+\s*\+\s*\d+\]",
            "severity": "critical",
            "name": "向前索引",
            "suggestion": "使用 .iloc[i-1] 而非 .iloc[i+1]。信号只能基于过去的数据。",
            "example_bad": "close.iloc[i + 3]",
            "example_good": "close.iloc[i - 1]",
        },
        {
            "pattern": r"\.shift\(\s*-\d+\s*\)",
            "severity": "critical",
            "name": "负向 shift",
            "suggestion": "shift(-1) 会使用未来数据。只能使用 shift(1) 或 shift(0)。",
            "example_bad": "close.shift(-1)",
            "example_good": "close.shift(1)",
        },
        {
            "pattern": r"iloc\[\w+\s*\+\s*[1-9]\d*\s*:",
            "severity": "critical",
            "name": "前瞻切片",
            "suggestion": "close.iloc[i+1:...] 使用了未来数据。确认条件必须在信号发出前完成，或将信号后移到确认完成日。",
            "example_bad": "close.iloc[i + 1 : i + 4]",
            "example_good": "# 方案A: 信号后移\nsignal.iloc[i + 3] = 1  # 确认完成后发信号\n# 方案B: 触价即入\nsignal.iloc[i] = 1  # 突破即入场，止损控制风险",
        },
        {
            "pattern": r"look_ahead|lookahead|peek_forward|future_data",
            "severity": "high",
            "name": "前瞻变量名",
            "suggestion": "变量名暗示使用了未来数据。请检查逻辑。",
            "example_bad": "look_ahead = i + 3",
            "example_good": "# 使用 lag 变量名\nlag = i - 1",
        },
        {
            "pattern": r"\.rolling\(.*\)\.mean\(\)\.shift\(\s*-\d+\s*\)",
            "severity": "critical",
            "name": "滚动均值前移",
            "suggestion": "rolling().mean().shift(-1) 将 t+1 时刻的均值放到了 t 时刻。应使用 rolling().mean() 或 rolling().mean().shift(1)。",
            "example_bad": "ma.rolling(20).mean().shift(-1)",
            "example_good": "ma.rolling(20).mean()",
        },
        {
            "pattern": r"\.max\(\)|\.min\(\)",
            "severity": "warning",
            "name": "全区间统计",
            "suggestion": "对全区间使用 .max()/.min() 可能引入未来信息。确认是否应该限制在 [0:i] 范围内。",
            "example_bad": "close.max()  # 包含了 i 之后的数据",
            "example_good": "close.iloc[:i].max()  # 只使用 i 之前的数据",
        },
    ]

    lines = code.split("\n")

    in_docstring = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # 跟踪多行文档字符串状态
        if stripped.startswith('"""') or stripped.startswith("'''"):
            in_docstring = not in_docstring
            # 单行文档字符串 ("""...""")
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                in_docstring = False
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue

        for p in patterns:
            import re
            if re.search(p["pattern"], stripped):
                violations.append({
                    "line": i,
                    "code": stripped[:120],
                    "pattern_name": p["name"],
                    "severity": p["severity"],
                    "suggestion": p["suggestion"],
                    "strategy": strategy_name,
                })

    return violations


def validate_strategy_pit(
    code: str,
    strategy_name: str = "unknown",
    strict: bool = True,
) -> Tuple[bool, List[Dict]]:
    """
    验证策略代码是否通过 PIT 合规检查。

    参数:
        code: 策略源代码
        strategy_name: 策略名称
        strict: True=发现任何违规即返回 False，False=仅 critical 级别返回 False

    返回:
        (passed, violations): 是否通过 + 违规列表
    """
    violations = detect_pit_violation(code, strategy_name)

    if strict:
        passed = len(violations) == 0
    else:
        passed = not any(v["severity"] == "critical" for v in violations)

    return passed, violations