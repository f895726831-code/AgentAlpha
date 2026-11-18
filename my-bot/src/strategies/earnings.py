"""
T5 财报驱动策略 — 财报超预期后捕捉盈余漂移 (PEAD)。

参考技能: earnings_calendar_planner, earnings_preview, earnings_reaction_interpreter
参考论文: Ball & Brown (1968), Bernard & Thomas (1989) — PEAD 是 A 股最强的
  事件驱动 alpha 之一，财报超预期后 60 个交易日存在显著超额收益。

策略逻辑:
  1. 财报披露日涨幅超预期 (surprise > threshold)
  2. 放量确认 (机构调仓信号)
  3. 财报披露后 1-5 日内缩量回调 → 入场窗口
  4. 持有至披露后 drift_days 或止盈触发

注意: 此策略依赖财报日历和业绩数据，实际运行时需要:
  - 财报披露日期数据 (akshare stock_yjbb_em 或 Wind)
  - 分析师预期数据 (如可用)
  离线编译时 LLM 负责填入具体的财报日期和标的列表。

TiMi 离线编译参数 (LLM 可调):
  由 StrategyParams.filters 承载所有策略特有参数。

用法:
    from strategies.earnings import EarningsStrategy
    from strategies.base import StrategyParams

    params = StrategyParams(
        strategy_type="T5_earnings",
        mode="normal",
        filters={
            "surprise_min_pct": 5.0,
            "post_earnings_drift_days": 60,
            "entry_window_days": 5,
            "avoid_if_guidance_cut": True,
        },
    )
    strategy = EarningsStrategy(params)
"""

from typing import Any, Dict, List, Optional

from .base import BaseStrategy, Signal, StrategyParams


class EarningsStrategy(BaseStrategy):
    """
    T5 财报驱动策略 — 盈余漂移捕捉。

    参数 (全部通过 StrategyParams.filters 传入):
        surprise_min_pct: float = 5.0
            财报日涨幅最低要求 (%)。当日涨幅超过此值视为超预期。
            在无分析师预期数据时，用价格反应作为超预期的代理变量。

        post_earnings_drift_days: int = 60
            盈余漂移持有天数。财报超预期后 PEAD 效应通常持续 40-60 个交易日。
            实际持仓如触发止盈/止损则提前退出。

        entry_window_days: int = 5
            入场窗口天数。财报披露后 1-5 日内寻找缩量回调介入点。
            过晚入场则漂移效应衰减。

        avoid_if_guidance_cut: bool = True
            是否规避业绩指引下调。True 时跳过管理层下调下一季度指引的标的。
            需外部数据源 (Wind 业绩预告 / akshare 业绩变动公告)。

        volume_confirmation_ratio: float = 1.5
            财报日放量确认。财报日成交量 / 20 日均量 ≥ 此值。

        max_gap_up_pct: float = 8.0
            财报日跳空涨幅上限 (%)。超过此值视为一字板/利好兑现，不做 PEAD。

        stop_loss_pct: float = 7.0
            止损百分比。PEAD 波动大，止损略宽。

        take_profit_pct: float = 15.0
            止盈百分比。PEAD 漂移平均 5-10%，15% 是上限。

        ma_trend_period: int = 60
            趋势过滤 MA 周期。收盘价必须在 MA 之上 (不能在下跌趋势中做 PEAD)。

        min_market_cap_billion: float = 5.0
            最低市值 (亿元)。过滤微盘壳股财报噪音。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "surprise_min_pct": 5.0,
        "post_earnings_drift_days": 60,
        "entry_window_days": 5,
        "avoid_if_guidance_cut": True,
        "volume_confirmation_ratio": 1.5,
        "max_gap_up_pct": 8.0,
        "stop_loss_pct": 7.0,
        "take_profit_pct": 15.0,
        "ma_trend_period": 60,
        "min_market_cap_billion": 5.0,
    }

    @property
    def strategy_type(self) -> str:
        return "T5_earnings"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查财报驱动入场条件 (PIT 安全)。

        市场数据要求:
            symbol, close, open, high, low, volume,
            avg_volume_20d (20 日均量),
            ma_60 (趋势过滤),
            earnings_date (财报披露日期, ISO 格式),
            earnings_surprise_pct (财报日涨幅 %),
            guidance_downgraded (bool, 业绩指引是否下调),
            days_since_earnings (财报披露后天数),
            market_cap_billion (市值, 亿元)

        条件:
            1. 财报日涨幅 ≥ surprise_min_pct (超预期确认)
            2. 财报日放量 ≥ volume_confirmation_ratio (机构参与)
            3. 财报日跳空 ≤ max_gap_up_pct (非一字板)
            4. 距财报日 ≤ entry_window_days (在入场窗口内)
            5. 当前在 MA 之上 (趋势没坏)
            6. 无业绩指引下调 (如数据可用)
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)

        surprise_pct = market_data.get("earnings_surprise_pct", None)
        days_since = market_data.get("days_since_earnings", None)
        guidance_cut = market_data.get("guidance_downgraded", False)

        # ── 必须有财报数据 ──
        if surprise_pct is None or days_since is None:
            return False

        surprise_min = self._get_filter("surprise_min_pct")
        vol_confirm = self._get_filter("volume_confirmation_ratio")
        gap_max = self._get_filter("max_gap_up_pct")
        entry_window = self._get_filter("entry_window_days")
        ma_period = self._get_filter("ma_trend_period")
        avoid_cut = self._get_filter("avoid_if_guidance_cut")
        min_cap = self._get_filter("min_market_cap_billion")

        # ── 条件 1: 超预期确认 ──
        if surprise_pct < surprise_min:
            return False

        # ── 条件 2: 放量确认 ──
        # 注意: 这里检查的是当日成交量 (不是财报日)
        # 财报日的放量在 earnings_day_vol_ratio 中单独传入
        earnings_vol = market_data.get("earnings_day_vol_ratio", 0)
        if earnings_vol > 0 and earnings_vol < vol_confirm:
            return False

        # ── 条件 3: 跳空上限 ──
        if surprise_pct > gap_max:
            return False  # 一字板或涨幅过大，利好已兑现

        # ── 条件 4: 窗口期 ──
        if days_since < 1:
            return False  # 财报当日不追 (等市场消化)
        if days_since > entry_window:
            return False  # 错过入场窗口

        # ── 条件 5: 趋势过滤 ──
        ma_key = f"ma_{ma_period}"
        ma_val = market_data.get(ma_key, None)
        if ma_val is not None and close <= ma_val:
            return False

        # ── 条件 6: 指引下调 ──
        if avoid_cut and guidance_cut:
            return False

        # ── 条件 7: 市值过滤 ──
        mcap = market_data.get("market_cap_billion", 0)
        if mcap < min_cap:
            return False

        # ── 条件 8: 财报后缩量回调 (入场时机) ──
        # 财报日当天通常冲高, 次日缩量回调才是更好的入场点
        if days_since >= 2:
            avg_vol_20d = market_data.get("avg_volume_20d", 0)
            if avg_vol_20d > 0 and volume > avg_vol_20d * 0.8:
                return False  # 未缩量 — 等待更好的入场时机

        return True

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成财报漂移入场信号。

        止损: 财报日最低价或固定百分比止损，取更紧的。
        止盈: 固定百分比。
        持有期: min(drift_days, 止盈/止损触发)。
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_20d = market_data.get("avg_volume_20d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")
        drift_days = self._get_filter("post_earnings_drift_days")

        days_since = market_data.get("days_since_earnings", 1)
        earnings_low = market_data.get("earnings_day_low", 0)

        # 止损: earnings day low (技术) 或 pct stop (百分比), 取更紧的
        pct_stop = close * (1 - stop_loss_pct / 100)
        if earnings_low > 0:
            stop_loss = max(earnings_low, pct_stop)  # 做多取更高价 (更紧)
        else:
            stop_loss = pct_stop

        trigger_price = close
        take_profit = [close * (1 + take_profit_pct / 100)]

        confidence = self.get_confidence(market_data)

        # Edge: PEAD 平均超额 3-8%，以 5% 计
        avg_excess = 5.0
        risk = stop_loss_pct / 100
        edge = avg_excess / max(risk, 0.001)

        days_since_earnings = market_data.get("days_since_earnings", 0)

        return Signal(
            symbol=str(market_data.get("symbol", "")),
            strategy_type=self.strategy_type,
            direction="long",
            confidence=confidence,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            edge=edge,
            win_rate=0.58,  # PEAD 历史胜率偏高
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_20d, 1),
                "stop_loss_pct": (trigger_price - stop_loss) / trigger_price * 100,
                "take_profit_pct": take_profit_pct,
                "drift_days_max": drift_days,
                "days_since_earnings": days_since_earnings,
                "earnings_surprise_pct": market_data.get("earnings_surprise_pct", 0),
                "generated_by": "TiMi_EarningsStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        """
        财报信号置信度 (0-1)。

        评分:
            - 超预期幅度 ≥ 10% (涨停附近): +0.10
            - 财报日放量 ≥ 2.5x: +0.10
            - 入场时机: 回调 -2%~-5%: +0.10 (更好的入场价)
            - 大盘环境配合 (close > MA60): +0.05
            - 无指引下调 (数据明确): +0.05
        """
        conf = self.params.confidence_threshold

        surprise = market_data.get("earnings_surprise_pct", 0)
        earnings_vol = market_data.get("earnings_day_vol_ratio", 0)
        days_since = market_data.get("days_since_earnings", 1)
        earnings_close = market_data.get("earnings_day_close", 0)
        close = market_data.get("close", 0)
        guidance_cut = market_data.get("guidance_downgraded", None)

        # 超预期幅度
        if surprise >= 10.0:
            conf += 0.10
        elif surprise >= 7.0:
            conf += 0.05

        # 财报日放量
        if earnings_vol >= 2.5:
            conf += 0.10
        elif earnings_vol >= 1.5:
            conf += 0.05

        # 入场时机: 回调 -2% ~ -5% 是最佳入场窗口
        if earnings_close > 0 and days_since >= 2:
            retrace = (close - earnings_close) / earnings_close * 100
            if -5.0 <= retrace <= -2.0:
                conf += 0.10  # 典型的 PEAD 回调入场

        # 大盘趋势
        ma_key = f"ma_{self._get_filter('ma_trend_period')}"
        ma_val = market_data.get(ma_key, None)
        if ma_val and close > ma_val:
            conf += 0.05

        # 指引状态
        if guidance_cut is False:  # 明确无下调
            conf += 0.05

        return max(0.0, min(conf, 1.0))


def make_earnings(
    mode: str = "normal",
    bias: str = "balanced",
    **overrides,
) -> EarningsStrategy:
    """创建 EarningsStrategy 的便捷函数"""
    filters = {**EarningsStrategy.DEFAULT_FILTERS, **overrides}
    params = StrategyParams(
        strategy_type="T5_earnings",
        mode=mode,
        bias=bias,
        filters=filters,
    )
    return EarningsStrategy(params)