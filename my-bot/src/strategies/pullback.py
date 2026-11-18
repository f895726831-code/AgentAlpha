"""
T2 回调低吸策略 — 上升趋势中回调至均线支撑位后缩量企稳入场。

参考实现:
  - signal-factory: pullback_low (回调至 MA20 + 缩量确认)
  - risk-policy.json: left_priority 下 T2×1.5, right_priority 下 T2×0.5

策略逻辑:
  1. 趋势确认: 中长期均线向上 (MA60 斜率 > 0)
  2. 价格回调: close 在 MA20 下方但未破 MA60
  3. 缩量企稳: 当日成交量 < 5 日均量 × volume_shrink_ratio
  4. 支撑位确认: close 在支撑位上方 (MA20 / MA60 / Fib 回撤)

TiMi 离线编译参数 (LLM 可调):
  由 StrategyParams.filters 承载所有策略特有参数。

用法:
    from strategies.pullback import PullbackStrategy
    from strategies.base import StrategyParams

    params = StrategyParams(
        strategy_type="T2_pullback",
        mode="normal",
        bias="left_priority",
        filters={
            "trend_period": 60,
            "ma_period": 20,
            "pullback_pct_min": 2.0,
            "pullback_pct_max": 8.0,
            "volume_shrink_ratio": 0.7,
            "support_levels": ["ma20", "ma60"],
        },
    )
    strategy = PullbackStrategy(params)
"""

from typing import Any, Dict, List, Optional

from .base import BaseStrategy, Signal, StrategyParams


class PullbackStrategy(BaseStrategy):
    """
    T2 回调低吸策略 — 趋势中的回调缩量企稳入场。

    参数 (全部通过 StrategyParams.filters 传入):
        trend_period: int = 60
            趋势判断 MA 周期。MA_trend 方向向上且价格在其上方。

        ma_period: int = 20
            回调目标 MA 周期。价格回调至此 MA 附近。

        pullback_pct_min: float = 2.0
            最小回调幅度 (%)。从近期高点的回落至少达到此幅度。
            过滤掉噪音级别的微调。

        pullback_pct_max: float = 8.0
            最大回调幅度 (%)。回调过深 (> 8%) 可能趋势已坏。
            超过此值视为转势而非回调。

        volume_shrink_ratio: float = 0.7
            缩量比例。当日成交量 / 5 日均量 ≤ 此值。
            0.7 = 缩量 30%，表示抛压衰竭。

        support_levels: List[str] = ["ma20"]
            支撑位类型。close 必须在至少一个支撑位上方。
            可选: "ma20", "ma60", "fib38", "fib50", "fib62"

        stop_loss_pct: float = 5.0
            止损百分比。

        take_profit_pct: float = 10.0
            止盈百分比。

        consecutive_drop_days_max: int = 5
            最大连跌天数。连跌超过此天数视为趋势破坏。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "trend_period": 60,
        "ma_period": 20,
        "pullback_pct_min": 2.0,
        "pullback_pct_max": 8.0,
        "volume_shrink_ratio": 0.7,
        "support_levels": ["ma60"],
        "stop_loss_pct": 5.0,
        "take_profit_pct": 10.0,
        "consecutive_drop_days_max": 5,
    }

    @property
    def strategy_type(self) -> str:
        return "T2_pullback"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查回调低吸条件 (PIT 安全)。

        市场数据要求:
            symbol, close, open, high, low, volume,
            avg_volume_5d,
            ma_20, ma_60 (或 trend_period/ma_period 对应的 MA),
            recent_high_Nd (近期 N 日高点),
            consecutive_down_days (连续下跌天数)

        条件:
            1. 趋势确认: close > MA_trend 且 MA_trend 斜率向上
            2. 回调确认: close 在 MA 下方但未破趋势 MA
            3. 缩量确认: volume ≤ avg_vol_5d × volume_shrink_ratio
            4. 回调幅度: pullback_pct_min ≤ 幅度 ≤ pullback_pct_max
            5. 支撑位: close 在任一支撑位上方
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)

        trend_period = self._get_filter("trend_period")
        ma_period = self._get_filter("ma_period")
        vol_shrink = self._get_filter("volume_shrink_ratio")
        pb_min = self._get_filter("pullback_pct_min")
        pb_max = self._get_filter("pullback_pct_max")
        support_levels: List[str] = self._get_filter("support_levels")
        max_drop_days = self._get_filter("consecutive_drop_days_max")

        # ── 条件 1: 趋势确认 ──
        ma_trend_key = f"ma_{trend_period}"
        ma_trend = market_data.get(ma_trend_key, None)
        ma_trend_prev = market_data.get(f"{ma_trend_key}_prev", None)

        if ma_trend is None:
            return False

        # 价格在趋势 MA 之上
        if close <= ma_trend:
            return False

        # MA 斜率向上 (可选，如果没有 prev 则跳过)
        if ma_trend_prev is not None and ma_trend <= ma_trend_prev:
            return False

        # ── 条件 2: 回调确认 (价格在 MA20 之下) ──
        ma_key = f"ma_{ma_period}"
        ma_val = market_data.get(ma_key, None)
        if ma_val is None:
            return False

        # close 需要在 MA 下方 (正在回调中)
        if close >= ma_val:
            return False

        # 但不能跌破趋势 MA (趋势未破)
        if close <= ma_trend:
            return False

        # ── 条件 3: 缩量确认 ──
        avg_vol_5d = market_data.get("avg_volume_5d", 0)
        if avg_vol_5d > 0 and volume > avg_vol_5d * vol_shrink:
            return False

        # ── 条件 4: 回调幅度 ──
        recent_high = market_data.get("recent_high_20d", None)
        if recent_high is not None and recent_high > 0:
            drawdown_pct = (recent_high - close) / recent_high * 100
            if drawdown_pct < pb_min:
                return False  # 回调幅度太小，噪音
            if drawdown_pct > pb_max:
                return False  # 回调太深，趋势可能已坏

        # ── 条件 5: 支撑位确认 ──
        if not self._check_support(market_data, close, support_levels):
            return False

        # ── 条件 6: 连跌天数 ──
        consecutive_down = market_data.get("consecutive_down_days", 0)
        if consecutive_down > max_drop_days:
            return False

        return True

    def _check_support(
        self,
        market_data: Dict[str, Any],
        close: float,
        support_levels: List[str],
        tolerance_pct: float = 2.0,
    ) -> bool:
        """检查 close 是否在任一支撑位附近 (做多支撑位在价格附近/下方)"""
        for level in support_levels:
            if level == "ma20":
                ma20 = market_data.get("ma_20", 0)
                if ma20 > 0 and close >= ma20 * (1 - tolerance_pct / 100):
                    return True
            elif level == "ma60":
                ma60 = market_data.get("ma_60", 0)
                if ma60 > 0 and close >= ma60 * (1 - tolerance_pct / 100):
                    return True
            elif level.startswith("fib"):
                fib_key = f"fib_{level[3:]}"
                fib_val = market_data.get(fib_key, 0)
                if fib_val > 0 and close >= fib_val * (1 - tolerance_pct / 100):
                    return True
            elif level == "prev_low":
                prev_low = market_data.get("prev_swing_low", 0)
                if prev_low > 0 and close >= prev_low * (1 - tolerance_pct / 100):
                    return True
        return False

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成回调低吸信号。
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")

        # 止损设在趋势 MA 下方或百分比止损
        trend_period = self._get_filter("trend_period")
        ma_trend = market_data.get(f"ma_{trend_period}", 0)
        technical_stop = ma_trend if ma_trend > 0 else close * (1 - stop_loss_pct / 100)
        pct_stop = close * (1 - stop_loss_pct / 100)
        stop_loss = max(technical_stop, pct_stop)  # 取更保守的（更紧的止损）

        trigger_price = close
        take_profit = [close * (1 + take_profit_pct / 100)]

        confidence = self.get_confidence(market_data)

        # Edge: reward/risk
        risk = (trigger_price - stop_loss) / trigger_price
        reward = take_profit_pct / 100
        edge = reward / max(risk, 0.001)

        return Signal(
            symbol=str(market_data.get("symbol", "")),
            strategy_type=self.strategy_type,
            direction="long",
            confidence=confidence,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            edge=edge,
            win_rate=0.55,   # 回调低吸胜率通常略高于突破
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_5d, 1),
                "stop_loss_pct": (trigger_price - stop_loss) / trigger_price * 100,
                "take_profit_pct": take_profit_pct,
                "generated_by": "TiMi_PullbackStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        """
        回调信号置信度 (0-1)。

        评分:
            - 缩量程度: vol_ratio ≤ 0.5 → +0.15, ≤ 0.7 → +0.05
            - 回调幅度适中 (3-6%): +0.10
            - 多支撑位共振: 2+ → +0.10
            - 连跌 < 3 天: +0.05
            - 长下影线 (close > low + 0.5*(high-low)): +0.05
        """
        conf = self.params.confidence_threshold

        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)
        vol_shrink = self._get_filter("volume_shrink_ratio")

        # 缩量评分
        if avg_vol_5d > 0:
            vol_ratio = volume / avg_vol_5d
            if vol_ratio <= 0.5:
                conf += 0.15
            elif vol_ratio <= vol_shrink:
                conf += 0.05

        # 回调幅度评分
        recent_high = market_data.get("recent_high_20d", None)
        close = market_data.get("close", 0)
        if recent_high and close > 0:
            dd_pct = (recent_high - close) / recent_high * 100
            if 3.0 <= dd_pct <= 6.0:
                conf += 0.10

        # 支撑位共振
        support_levels: List[str] = self._get_filter("support_levels")
        supported = sum(
            1 for level in support_levels
            if self._check_support(market_data, close, [level])
        )
        if supported >= 2:
            conf += 0.10

        # 连跌天数
        consecutive_down = market_data.get("consecutive_down_days", 0)
        if consecutive_down < 3:
            conf += 0.05

        # 下影线
        high = market_data.get("high", 0)
        low = market_data.get("low", 0)
        if high > low:
            lower_wick_pct = (close - low) / (high - low)
            if lower_wick_pct > 0.5:
                conf += 0.05

        return max(0.0, min(conf, 1.0))


def make_pullback(
    mode: str = "normal",
    bias: str = "left_priority",
    **overrides,
) -> PullbackStrategy:
    """创建 PullbackStrategy 的便捷函数"""
    filters = {**PullbackStrategy.DEFAULT_FILTERS, **overrides}
    params = StrategyParams(
        strategy_type="T2_pullback",
        mode=mode,
        bias=bias,
        filters=filters,
    )
    return PullbackStrategy(params)