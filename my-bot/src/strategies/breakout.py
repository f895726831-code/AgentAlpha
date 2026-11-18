"""
T1 突破策略 — 放量突破盘整区间后触价即入。

参考实现:
  - signal-factory: breakout_60 (放量突破 60 元)
  - 记忆: [[entry-confirmation-must-filter-losses]] — 触价即入, 无需回踩确认
  - 记忆: [[smic-pullback-confirmation-false-positive]] — 确认条件反效果

策略逻辑:
  1. 过去 N 日价格始终在突破位以下 (盘整/蓄力确认)
  2. 当日收盘价突破盘整区间高点
  3. 成交量放大确认 (可选)
  4. 跳空不过大 (防止追高假突破)
  5. 价格在 MA 之上 (趋势确认, 可选)

TiMi 离线编译参数 (LLM 可调):
  由 StrategyParams.filters 承载所有策略特有参数。
  编译器 (compiler.py) 负责将 LLM JSON 映射到 filters dict。

用法:
    from strategies.breakout import BreakoutStrategy
    from strategies.base import StrategyParams

    params = StrategyParams(
        strategy_type="T1_breakout",
        mode="normal",
        bias="right_priority",
        filters={
            "lookback_days": 30,
            "volume_ratio_min": 1.5,
            "breakout_price": None,   # None = 动态用 lookback 高点
            "gap_max_pct": 2.0,
            "ma_filter": 60,
        },
    )
    strategy = BreakoutStrategy(params)

    # 在线执行
    if strategy.check_conditions(market_data):
        signal = strategy.generate_signal(market_data)
"""

from typing import Any, Dict, Optional

import numpy as np

from .base import BaseStrategy, Signal, StrategyParams


class BreakoutStrategy(BaseStrategy):
    """
    T1 突破策略 — 放量突破后触价即入。

    参数 (全部通过 StrategyParams.filters 传入):
        lookback_days: int = 30
            盘整观察期。过去 N 日收盘价均未突破盘整高点。
            默认 30 日。

        volume_ratio_min: float = 1.5
            量比最小要求。当日成交量 / 5 日均量 ≥ 此值。
            设为 0 可跳过成交量确认（不推荐）。

        breakout_price: float | None = None
            固定突破价格。None 表示动态使用 lookback 区间高点。
            适用于固定锚点策略 (如"突破 60 元")。

        gap_max_pct: float = 2.0
            跳空上限 (%)。当日开盘价相对前日收盘的涨幅超过此值视为跳空追高。
            跳空过大 → skip (防止情绪化假突破)。

        ma_filter: int = 60
            趋势均线周期。收盘价必须在此 MA 之上。
            设为 0 可跳过趋势过滤。

        stop_loss_pct: float = 5.0
            止损百分比。trigger_price × (1 - stop_loss_pct / 100)

        take_profit_pct: float = 12.0
            止盈百分比。trigger_price × (1 + take_profit_pct / 100)

        min_consolidation_days: int = 10
            盘整区最低天数。lookback 区间内最少需要这么多根 K 线。
            过短表示数据不足或刚上市，跳过。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "lookback_days": 30,
        "volume_ratio_min": 1.5,
        "breakout_price": None,
        "gap_max_pct": 2.0,
        "ma_filter": 60,
        "stop_loss_pct": 5.0,
        "take_profit_pct": 12.0,
        "min_consolidation_days": 10,
    }

    @property
    def strategy_type(self) -> str:
        return "T1_breakout"

    def _get_filter(self, key: str) -> Any:
        """从 params.filters 获取参数，缺失时用默认值"""
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查突破条件 (PIT 安全 — 仅使用当日及历史数据)。

        市场数据要求:
            symbol, close, open, high, low, volume,
            avg_volume_5d (5 日均量),
            ma_{period} (可选, 如 ma_60),
            price_history (历史收盘价列表，不含当日)

        条件:
            1. 过去 lookback_days 日收盘价均 < 突破位 (盘整确认)
            2. 当日收盘 >= 突破位 (触发突破)
            3. 量比 >= volume_ratio_min (放量确认)
            4. 开盘跳空 ≤ gap_max_pct (不过度追高)
            5. close > MA (趋势确认, 可选)

        注意: price_history 应为历史数据 (不含当日 bar)。
              当日数据通过 close/open/volume 等字段传入。
        """
        close = market_data.get("close", 0)
        open_price = market_data.get("open", 0)
        prev_close = market_data.get("prev_close", 0)
        volume = market_data.get("volume", 0)

        lookback = self._get_filter("lookback_days")
        vol_min = self._get_filter("volume_ratio_min")
        gap_max = self._get_filter("gap_max_pct")
        ma_period = self._get_filter("ma_filter")
        fixed_price = self._get_filter("breakout_price")
        min_consolidation = self._get_filter("min_consolidation_days")

        # ── 获取历史数据 (不含当日) ──
        history = market_data.get("price_history", [])
        if not isinstance(history, list):
            history = []

        # 截取 lookback 窗口 (但不含当日 bar)
        consolidation_closes = history[-lookback:] if len(history) >= lookback else history
        n_days = len(consolidation_closes)

        if n_days < min_consolidation:
            return False  # 数据不足

        # ── 确定突破价格 ──
        if fixed_price is not None:
            breakout_price = float(fixed_price)
            # 固定突破位: 检查所有历史收盘价均未突破
            if any(c >= breakout_price for c in consolidation_closes):
                return False
        else:
            # 动态突破位: lookback 窗口内最高价即阻力位
            # 盘整已隐含 (所有历史价格 ≤ 此高点)
            breakout_price = max(consolidation_closes)

        # ── 条件 2: 当日突破 ──
        if close < breakout_price:
            return False

        # ── 条件 3: 成交量确认 ──
        if vol_min > 0:
            avg_vol_5d = market_data.get("avg_volume_5d", 0)
            if avg_vol_5d > 0 and volume < avg_vol_5d * vol_min:
                return False

        # ── 条件 4: 跳空上限 ──
        if gap_max > 0 and prev_close > 0:
            gap_pct = (open_price / prev_close - 1) * 100
            if gap_pct > gap_max:
                return False

        # ── 条件 5: 趋势确认 (close > MA) ──
        if ma_period > 0:
            ma_key = f"ma_{ma_period}"
            ma_val = market_data.get(ma_key, None)
            if ma_val is not None and close <= ma_val:
                return False

        return True

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成标准化突破信号。

        信号参数:
            - stop_loss: trigger_price × (1 - stop_loss_pct%)
            - take_profit: [trigger_price × (1 + take_profit_pct%)]
            - edge: 基于回测统计 (默认 2.0)
            - win_rate: 基于回测统计 (默认 0.50)
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")

        trigger_price = close
        stop_loss = trigger_price * (1 - stop_loss_pct / 100)
        take_profit = [trigger_price * (1 + take_profit_pct / 100)]

        # ── 置信度: 基于量比和跳空程度 ──
        confidence = self.get_confidence(market_data)

        # ── Edge 估计 ──
        # 基于止损/止盈比计算隐含 edge
        risk = stop_loss_pct / 100
        reward = take_profit_pct / 100
        rr_ratio = reward / max(risk, 0.001)
        edge = rr_ratio  # 简化为 reward/risk

        return Signal(
            symbol=str(market_data.get("symbol", "")),
            strategy_type=self.strategy_type,
            direction="long",
            confidence=confidence,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            edge=edge,
            win_rate=0.50,   # 回测统计表明 Ruflo 突破策略胜率约 50%
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_5d, 1),
                "stop_loss_pct": stop_loss_pct,
                "take_profit_pct": take_profit_pct,
                "generated_by": "TiMi_BreakoutStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        """
        突破信号置信度 (0-1)。

        评分逻辑:
            - 量比 ≥ 2.0: +0.15 (强放量)
            - 量比 ≥ 3.0: +0.05 (极端放量, 边际递减)
            - 无跳空 (gap ≤ 0.5%): +0.10 (稳健突破)
            - 跳空 > 1.5%: -0.05 (过度追高)
            - 盘整期 > 20 日: +0.05 (充分蓄力)

        Base = params.confidence_threshold (默认 0.5)
        """
        conf = self.params.confidence_threshold

        volume = market_data.get("volume", 0)
        avg_vol_5d = market_data.get("avg_volume_5d", 0)
        open_price = market_data.get("open", 0)
        prev_close = market_data.get("prev_close", 0)

        # 量比评分
        if avg_vol_5d > 0:
            vol_ratio = volume / avg_vol_5d
            if vol_ratio >= 3.0:
                conf += 0.20
            elif vol_ratio >= 2.0:
                conf += 0.15
            elif vol_ratio >= 1.5:
                conf += 0.05

        # 跳空评分
        if prev_close > 0:
            gap_pct = (open_price / prev_close - 1) * 100
            if gap_pct <= 0.5:
                conf += 0.10
            elif gap_pct > 1.5:
                conf -= 0.05

        # 盘整充分性
        lookback = self._get_filter("lookback_days")
        price_history = market_data.get("price_history") or []
        if len(price_history) > lookback:
            conf += 0.05

        return max(0.0, min(conf, 1.0))


# ─── 便捷工厂函数 ───

def make_breakout(
    mode: str = "normal",
    bias: str = "right_priority",
    **overrides,
) -> BreakoutStrategy:
    """
    创建 BreakoutStrategy 的便捷函数。

    Args:
        mode: normal / earnings_season / risk_off
        bias: right_priority / left_priority / balanced
        **overrides: 覆盖默认 filter 参数

    Example:
        strategy = make_breakout(
            lookback_days=20,
            volume_ratio_min=2.0,
            breakout_price=60.0,
        )
    """
    filters = {**BreakoutStrategy.DEFAULT_FILTERS, **overrides}
    params = StrategyParams(
        strategy_type="T1_breakout",
        mode=mode,
        bias=bias,
        filters=filters,
    )
    return BreakoutStrategy(params)