"""
T4 均线金叉策略 — 短期均线上穿长期均线, 成交量配合确认。

参考实现:
  - signal-factory: golden_cross (MA20 上穿 MA60 + 放量)

策略逻辑:
  1. 前一日: MA_short ≤ MA_long (尚未金叉)
  2. 当日: MA_short > MA_long (金叉触发)
  3. 成交量确认: 当日量 ≥ 5 日均量 × volume_confirm_ratio

用法:
    from strategies.golden_cross import GoldenCrossStrategy
    strategy = GoldenCrossStrategy(StrategyParams(
        strategy_type="T4_golden_cross",
        mode="normal", bias="right_priority",
        filters={"ma_short": 20, "ma_long": 60},
    ))
"""

from typing import Any, Dict, Optional

from .base import BaseStrategy, Signal, StrategyParams


class GoldenCrossStrategy(BaseStrategy):
    """
    T4 均线金叉策略。

    参数 (StrategyParams.filters):
        ma_short: int = 20
            短期均线周期。

        ma_long: int = 60
            长期均线周期。

        volume_confirm_ratio: float = 1.2
            量比确认下限。当日成交量 / 5 日均量 ≥ 此值。
            设为 0 可跳过成交量确认。

        price_above_ma_long: bool = True
            是否要求收盘价在长期均线之上。
            防止均线在价格下方无效金叉。

        stop_loss_pct: float = 5.0
        take_profit_pct: float = 15.0
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "ma_short": 20,
        "ma_long": 60,
        "volume_confirm_ratio": 1.2,
        "price_above_ma_long": True,
        "stop_loss_pct": 5.0,
        "take_profit_pct": 15.0,
    }

    @property
    def strategy_type(self) -> str:
        return "T4_golden_cross"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查均线金叉条件。

        市场数据要求:
            close, volume, avg_volume_5d,
            ma_20, ma_20_prev (前一日 MA20),
            ma_60, ma_60_prev (前一 MA60)

        条件:
            1. 前一日: MA_short ≤ MA_long (未金叉)
            2. 当日: MA_short > MA_long (金叉触发)
            3. 成交量确认 (可选)
            4. 价格在长期均线之上 (可选)
        """
        ma_s = self._get_filter("ma_short")
        ma_l = self._get_filter("ma_long")
        vol_confirm = self._get_filter("volume_confirm_ratio")
        price_above = self._get_filter("price_above_ma_long")

        # ── MA 数据 ──
        ma_s_key = f"ma_{ma_s}"
        ma_l_key = f"ma_{ma_l}"
        ma_s_prev_key = f"{ma_s_key}_prev"
        ma_l_prev_key = f"{ma_l_key}_prev"

        ma_s_now = market_data.get(ma_s_key, None)
        ma_l_now = market_data.get(ma_l_key, None)
        ma_s_prev = market_data.get(ma_s_prev_key, None)
        ma_l_prev = market_data.get(ma_l_prev_key, None)

        if ma_s_now is None or ma_l_now is None:
            return False

        # ── 条件 1 & 2: 金叉确认 ──
        # 前一日: 短 ≤ 长 (未金叉)
        if ma_s_prev is not None and ma_l_prev is not None:
            if ma_s_prev > ma_l_prev:
                return False  # 之前已经金叉, 非首次

        # 当日: 短 > 长 (金叉触发)
        if ma_s_now <= ma_l_now:
            return False

        # ── 条件 3: 成交量确认 ──
        if vol_confirm > 0:
            volume = market_data.get("volume", 0)
            avg_vol_5d = market_data.get("avg_volume_5d", 0)
            if avg_vol_5d > 0 and volume < avg_vol_5d * vol_confirm:
                return False

        # ── 条件 4: 价格在长期均线之上 ──
        if price_above:
            close = market_data.get("close", 0)
            if close <= ma_l_now:
                return False

        return True

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")

        # 止损设在长期均线或百分比止损
        ma_l_key = f"ma_{self._get_filter('ma_long')}"
        ma_long = market_data.get(ma_l_key, 0)
        technical_stop = ma_long if ma_long > 0 else close * (1 - stop_loss_pct / 100)
        pct_stop = close * (1 - stop_loss_pct / 100)
        stop_loss = max(technical_stop, pct_stop)

        trigger_price = close
        take_profit = [close * (1 + take_profit_pct / 100)]

        confidence = self.get_confidence(market_data)

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
            win_rate=0.48,  # 金叉胜率一般, 依靠趋势持仓获利
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_5d, 1),
                "stop_loss_pct": (trigger_price - stop_loss) / trigger_price * 100,
                "take_profit_pct": take_profit_pct,
                "generated_by": "TiMi_GoldenCrossStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        conf = self.params.confidence_threshold

        # 量比评分
        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)
        if avg_vol_5d > 0:
            vol_ratio = volume / avg_vol_5d
            if vol_ratio >= 2.0:
                conf += 0.15
            elif vol_ratio >= 1.5:
                conf += 0.08
            elif vol_ratio >= 1.2:
                conf += 0.03

        # MA 发散程度
        ma_s = self._get_filter("ma_short")
        ma_l = self._get_filter("ma_long")
        ma_s_now = market_data.get(f"ma_{ma_s}", 0)
        ma_l_now = market_data.get(f"ma_{ma_l}", 0)
        if ma_l_now > 0:
            spread = (ma_s_now - ma_l_now) / ma_l_now * 100
            if spread > 2.0:
                conf += 0.10  # 均线明显发散, 趋势强劲
            elif spread > 1.0:
                conf += 0.05

        # 价格在短期均线之上
        close = market_data.get("close", 0)
        if ma_s_now > 0 and close > ma_s_now:
            conf += 0.05

        return max(0.0, min(conf, 1.0))


def make_golden_cross(mode: str = "normal", bias: str = "right_priority", **overrides) -> GoldenCrossStrategy:
    filters = {**GoldenCrossStrategy.DEFAULT_FILTERS, **overrides}
    return GoldenCrossStrategy(StrategyParams(
        strategy_type="T4_golden_cross", mode=mode, bias=bias, filters=filters,
    ))