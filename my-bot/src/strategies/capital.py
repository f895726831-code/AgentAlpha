"""
T6 资金驱动策略 — 跟随主力资金持续流入的标的。

参考模板: market-wide-scanner T6
参考技能: northbound_capital_flow (加分项)

策略逻辑:
  1. 连续 N 日主力资金净流入 (capital_inflow_days_min)
  2. 同期价格确认上涨 (避免主力对倒出货)
  3. 放量确认 (资金流入伴随成交量放大)
  4. 市值过滤 (避免微盘庄股)
  5. 可选: 北向资金同向流入加分

TiMi 离线编译参数 (LLM 可调):
  由 StrategyParams.filters 承载所有策略特有参数。

用法:
    from strategies.capital import CapitalStrategy
    from strategies.base import StrategyParams

    params = StrategyParams(
        strategy_type="T6_capital",
        mode="normal",
        filters={
            "capital_inflow_days_min": 3,
            "inflow_amount_min_million": 50,
        },
    )
    strategy = CapitalStrategy(params)
"""

from typing import Any, Dict, List, Optional

from .base import BaseStrategy, Signal, StrategyParams


class CapitalStrategy(BaseStrategy):
    """
    T6 资金驱动策略 — 跟随主力资金。

    参数 (全部通过 StrategyParams.filters 传入):
        capital_inflow_days_min: int = 3
            主力资金连续净流入天数最低要求。
            数据来源: akshare stock_individual_fund_flow 或 Wind 资金流向。

        inflow_amount_min_million: float = 50.0
            累计净流入金额最低要求 (百万元)。
            过滤小额资金波动。

        price_confirm_days: int = 3
            价格确认天数。资金流入期间必须有 N 天价格上涨。
            避免对倒出货 (主力左手倒右手制造流入假象)。

        volume_ratio_min: float = 1.2
            量比最低要求。资金流入期间日均量 / 20 日均量 ≥ 此值。

        min_market_cap_billion: float = 10.0
            最低市值 (亿元)。过滤庄股和流动性差的标的。

        northbound_bonus: bool = True
            北向资金加分。如北向同期也净流入，confidence +0.05。

        stop_loss_pct: float = 5.0
            止损百分比。资金驱动策略止损较紧 (主力可能突然撤出)。

        take_profit_pct: float = 10.0
            止盈百分比。

        max_holding_days: int = 10
            最大持有天数。主力资金信号时效短，超时即使未触发止盈也退出。

        consecutive_up_days_max: int = 5
            连续上涨天数上限。超过此值视为已透支，不追。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "capital_inflow_days_min": 3,
        "inflow_amount_min_million": 50.0,
        "price_confirm_days": 3,
        "volume_ratio_min": 1.2,
        "min_market_cap_billion": 10.0,
        "northbound_bonus": True,
        "stop_loss_pct": 5.0,
        "take_profit_pct": 10.0,
        "max_holding_days": 10,
        "consecutive_up_days_max": 5,
    }

    @property
    def strategy_type(self) -> str:
        return "T6_capital"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查资金驱动入场条件 (PIT 安全)。

        市场数据要求:
            symbol, close, volume,
            avg_volume_20d (20 日均量),
            capital_flow_days (近 N 日资金流向列表, 正=流入 负=流出),
            cumulative_inflow_million (累计净流入, 百万元),
            consecutive_up_days (连续上涨天数),
            price_confirm_days_count (资金流入期间上涨天数),
            northbound_inflow (bool, 北向同期是否净流入),
            market_cap_billion (市值, 亿元)

        条件:
            1. 连续流入天数 ≥ capital_inflow_days_min
            2. 累计净流入 ≥ inflow_amount_min_million
            3. 流入期间价格确认天数 ≥ price_confirm_days
            4. 量比 ≥ volume_ratio_min
            5. 市值 ≥ min_market_cap_billion
            6. 未透支 (连续上涨天数 < consecutive_up_days_max)
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)

        inflow_days = market_data.get("capital_inflow_days", 0)
        cumulative_inflow = market_data.get("cumulative_inflow_million", 0)

        inflow_days_min = self._get_filter("capital_inflow_days_min")
        inflow_amount_min = self._get_filter("inflow_amount_min_million")
        price_confirm = self._get_filter("price_confirm_days")
        vol_ratio_min = self._get_filter("volume_ratio_min")
        min_cap = self._get_filter("min_market_cap_billion")
        up_days_max = self._get_filter("consecutive_up_days_max")

        # ── 条件 1: 连续流入 ──
        if inflow_days < inflow_days_min:
            return False

        # ── 条件 2: 金额确认 ──
        if cumulative_inflow < inflow_amount_min:
            return False

        # ── 条件 3: 价格确认 ──
        price_up_count = market_data.get("price_confirm_days_count", 0)
        if price_up_count < price_confirm:
            return False

        # ── 条件 4: 放量确认 ──
        avg_vol_20d = market_data.get("avg_volume_20d", 0)
        if avg_vol_20d > 0 and volume / avg_vol_20d < vol_ratio_min:
            return False

        # ── 条件 5: 市值过滤 ──
        mcap = market_data.get("market_cap_billion", 0)
        if mcap < min_cap:
            return False

        # ── 条件 6: 未透支 ──
        up_days = market_data.get("consecutive_up_days", 0)
        if up_days >= up_days_max:
            return False

        # ── Edge: 资金流出反转 ──
        recent_flow = market_data.get("capital_flow_today", None)
        if recent_flow is not None and recent_flow < 0:
            return False  # 今日资金开始流出，信号失效

        return True

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成资金驱动入场信号。
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_20d = market_data.get("avg_volume_20d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")

        trigger_price = close
        stop_loss = close * (1 - stop_loss_pct / 100)
        take_profit = [close * (1 + take_profit_pct / 100)]

        confidence = self.get_confidence(market_data)

        # Edge: 主力资金策略的 edge 基于资金信号强度
        cumulative_inflow = market_data.get("cumulative_inflow_million", 50)
        edge = min(cumulative_inflow / 100, 3.0)  # Cap at 3x

        return Signal(
            symbol=str(market_data.get("symbol", "")),
            strategy_type=self.strategy_type,
            direction="long",
            confidence=confidence,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            edge=edge,
            win_rate=0.54,  # 主力资金跟随策略 avg win rate
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_20d, 1),
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "cumulative_inflow_million": cumulative_inflow,
                "capital_inflow_days": market_data.get("capital_inflow_days", 0),
                "northbound_aligned": market_data.get("northbound_inflow", False),
                "max_holding_days": self._get_filter("max_holding_days"),
                "generated_by": "TiMi_CapitalStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        """
        资金信号置信度 (0-1)。

        评分:
            - 连续流入 ≥ 5 天: +0.10 (资金趋势强度高)
            - 累计流入 ≥ 200M: +0.10 (大资金参与)
            - 量比 ≥ 2.0: +0.05 (高度活跃)
            - 北向资金同向: +0.05
            - 中大市值 (≥ 50B): +0.05 (流动性好)
        """
        conf = self.params.confidence_threshold

        inflow_days = market_data.get("capital_inflow_days", 0)
        cumulative_inflow = market_data.get("cumulative_inflow_million", 0)
        volume = market_data.get("volume", 0)
        avg_vol_20d = market_data.get("avg_volume_20d", 0)
        northbound = market_data.get("northbound_inflow", False)
        mcap = market_data.get("market_cap_billion", 0)

        if inflow_days >= 5:
            conf += 0.10

        if cumulative_inflow >= 200:
            conf += 0.10

        if avg_vol_20d > 0 and volume / avg_vol_20d >= 2.0:
            conf += 0.05

        if northbound and self._get_filter("northbound_bonus"):
            conf += 0.05

        if mcap >= 50:
            conf += 0.05

        return max(0.0, min(conf, 1.0))


def make_capital(
    mode: str = "normal",
    bias: str = "balanced",
    **overrides,
) -> CapitalStrategy:
    """创建 CapitalStrategy 的便捷函数"""
    filters = {**CapitalStrategy.DEFAULT_FILTERS, **overrides}
    params = StrategyParams(
        strategy_type="T6_capital",
        mode=mode,
        bias=bias,
        filters=filters,
    )
    return CapitalStrategy(params)