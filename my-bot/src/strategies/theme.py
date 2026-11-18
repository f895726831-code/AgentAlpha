"""
题材驱动策略 — 识别市场主线题材并跟随龙头。

参考技能: a-share-primary-theme-identification (LLM-only)
参考论文: 题材股的超额收益主要来自散户关注度溢出和资金抱团效应

策略逻辑 (四阶段):
  1. 题材确认: 板块内有龙头连板 + 跟风股数量达标 (sector_breadth)
  2. 热度验证: 题材热度指数达标 + 持续性确认
  3. 入场时机: 龙头首阴/分歧日 (leader_pause) → 低吸龙头或跟风补涨
  4. 退出: 龙回头破位 / 热度退潮 / 新题材出现

注意: 此策略依赖外部题材热度数据，实际运行时需要:
  - 连板数据 (akshare stock_zt_pool_em)
  - 板块热度数据 (同花顺概念/东方财富板块)
  - 龙虎榜数据 (akshare stock_lhb_detail_em)
  离线编译时 LLM 负责识别当前主线题材和龙头标的。

TiMi 离线编译参数 (LLM 可调):
  由 StrategyParams.filters 承载所有策略特有参数。

用法:
    from strategies.theme import ThemeStrategy
    from strategies.base import StrategyParams

    params = StrategyParams(
        strategy_type="theme",
        mode="normal",
        filters={
            "leader_limit_up_days_min": 3,
            "sector_breadth_min": 5,
        },
    )
    strategy = ThemeStrategy(params)
"""

from typing import Any, Dict, List, Optional

from .base import BaseStrategy, Signal, StrategyParams


class ThemeStrategy(BaseStrategy):
    """
    题材驱动策略 — 主线题材 + 龙头跟随。

    参数 (全部通过 StrategyParams.filters 传入):
        leader_limit_up_days_min: int = 3
            龙头最低连板天数。题材龙头必须有一定连板高度才能带动板块情绪。

        leader_pause_max_pct: float = -3.0
            龙头分歧日最大跌幅 (%)。龙头首阴是入场时机，但跌幅不能过大
            (过大说明题材可能结束)。

        sector_breadth_min: int = 5
            板块内跟风股最低数量。确认题材有板块效应而非个股独立行情。

        heat_threshold: float = 60.0
            题材热度阈值 (0-100)。低于此值说明题材关注度不够。
            数据来源: 同花顺概念热度 / 东方财富板块热度 / 百度指数。

        sustainability_days_min: int = 2
            题材最低持续天数。过滤一日游题材。

        volume_confirmation: bool = True
            题材龙头是否放量。分歧日放量说明换手充分，资金接力意愿强。

        position_in_sector: str = "leader"
            持仓定位: "leader" (龙头), "follower" (跟风补涨), "any" (不限)。
            leader 风险高收益高, follower 相对稳健。

        max_theme_age_days: int = 10
            题材最大年龄 (天)。超过此天数题材审美疲劳，不追。

        stop_loss_pct: float = 8.0
            止损百分比。题材股波动大，止损略宽。

        take_profit_pct: float = 20.0
            止盈百分比。题材龙头有较高弹性。

        trailing_stop_pct: float = 5.0
            移动止盈回撤百分比。龙头破位 5% 即止盈。

        market_cap_range_billion: tuple = (3.0, 50.0)
            市值区间 (亿元)。题材股通常中小市值弹性大。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        "leader_limit_up_days_min": 3,
        "leader_pause_max_pct": -3.0,
        "sector_breadth_min": 5,
        "heat_threshold": 60.0,
        "sustainability_days_min": 2,
        "volume_confirmation": True,
        "position_in_sector": "leader",
        "max_theme_age_days": 10,
        "stop_loss_pct": 8.0,
        "take_profit_pct": 20.0,
        "trailing_stop_pct": 5.0,
        "market_cap_range_billion": (3.0, 50.0),
    }

    @property
    def strategy_type(self) -> str:
        return "theme"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查题材驱动入场条件 (PIT 安全)。

        市场数据要求:
            symbol, close, volume,
            avg_volume_20d,
            theme_name (题材名称),
            theme_heat (题材热度 0-100),
            theme_age_days (题材已持续天数),
            sector_breadth (板块内跟风股数量),
            is_leader (bool, 是否题材龙头),
            leader_limit_up_days (龙头连板天数),
            leader_pause_pct (龙头今日涨跌幅, 负=分歧),
            leader_volume_ratio (龙头量比),
            market_cap_billion (市值, 亿元)

        条件:
            1. 题材热度 ≥ heat_threshold
            2. 题材持续性 ≥ sustainability_days_min
            3. 板块跟风股 ≥ sector_breadth_min
            4. 龙头连板 ≥ leader_limit_up_days_min
            5. 龙头分歧 (如 target=leader) 或 跟风启动 (如 target=follower)
            6. 题材年龄 ≤ max_theme_age_days
            7. 市值在合理区间
        """
        close = market_data.get("close", 0)

        # ── 题材级别条件 ──
        heat = market_data.get("theme_heat", 0)
        heat_threshold = self._get_filter("heat_threshold")
        if heat < heat_threshold:
            return False

        sustainability = market_data.get("theme_sustainability_days", 0)
        sustain_min = self._get_filter("sustainability_days_min")
        if sustainability < sustain_min:
            return False

        breadth = market_data.get("sector_breadth", 0)
        breadth_min = self._get_filter("sector_breadth_min")
        if breadth < breadth_min:
            return False

        theme_age = market_data.get("theme_age_days", 0)
        max_age = self._get_filter("max_theme_age_days")
        if theme_age > max_age:
            return False

        # ── 龙头条件 ──
        leader_up_days = market_data.get("leader_limit_up_days", 0)
        leader_up_min = self._get_filter("leader_limit_up_days_min")
        if leader_up_days < leader_up_min:
            return False

        # ── 入场时机 ──
        position = self._get_filter("position_in_sector")
        is_leader = market_data.get("is_leader", False)
        leader_pause = market_data.get("leader_pause_pct", 0)

        if position == "leader" and is_leader:
            # 龙头首阴/分歧: 今日回调但不超过阈值
            pause_max = self._get_filter("leader_pause_max_pct")
            if leader_pause > 0:
                return False  # 龙头还在涨 — 不打板不追高
            if leader_pause < pause_max:
                return False  # 龙头跌太多 — 题材可能结束

            # 龙头分歧日放量确认 (换手充分)
            if self._get_filter("volume_confirmation"):
                leader_vol_ratio = market_data.get("leader_volume_ratio", 0)
                if leader_vol_ratio < 1.0:
                    return False  # 缩量下跌 — 资金在出逃

        elif position == "follower":
            # 跟风补涨: 龙头不在分歧日，跟风股刚启动
            if is_leader:
                return False  # follower 模式不选龙头
            follower_up_pct = market_data.get("change_pct", 0)
            if follower_up_pct < 1.0:
                return False  # 跟风股还没动
            if follower_up_pct > 5.0:
                return False  # 跟风股已经涨太多

        # ── 市值过滤 ──
        mcap = market_data.get("market_cap_billion", 0)
        cap_min, cap_max = self._get_filter("market_cap_range_billion")
        if mcap < cap_min or mcap > cap_max:
            return False

        # ── ST 过滤 ──
        if market_data.get("is_st", False):
            return False

        return True

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成题材驱动入场信号。

        止损: 取固定百分比和龙头上一个涨停板价格中更紧的。
        止盈: 固定百分比 + 移动止盈。
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_20d = market_data.get("avg_volume_20d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")

        # 龙头最低价作为额外技术止损参考
        leader_low = market_data.get("leader_period_low", 0)
        pct_stop = close * (1 - stop_loss_pct / 100)
        if leader_low > 0:
            stop_loss = max(leader_low, pct_stop)  # 更紧的止损
        else:
            stop_loss = pct_stop

        trigger_price = close
        take_profit = [close * (1 + take_profit_pct / 100)]

        confidence = self.get_confidence(market_data)

        # Edge: 题材弹性大，edge 基于热度和龙头强度
        heat = market_data.get("theme_heat", 60)
        leader_days = market_data.get("leader_limit_up_days", 3)
        edge = (heat / 100) * (leader_days / 3) * 3.0  # 基础 edge × 热度 × 龙头强度

        is_leader = market_data.get("is_leader", False)

        return Signal(
            symbol=str(market_data.get("symbol", "")),
            strategy_type=self.strategy_type,
            direction="long",
            confidence=confidence,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            edge=min(edge, 5.0),
            win_rate=0.45 if is_leader else 0.52,  # 龙头胜率偏低但赔率高, 跟风胜率偏高
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_20d, 1),
                "stop_loss_pct": (trigger_price - stop_loss) / trigger_price * 100,
                "take_profit_pct": take_profit_pct,
                "theme_name": market_data.get("theme_name", ""),
                "theme_heat": heat,
                "is_leader": is_leader,
                "leader_limit_up_days": leader_days,
                "position_in_sector": self._get_filter("position_in_sector"),
                "generated_by": "TiMi_ThemeStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        """
        题材信号置信度 (0-1)。

        评分:
            - 热度 ≥ 80 (极高热度): +0.10
            - 龙头连板 ≥ 5: +0.10
            - 板块跟风 ≥ 10 只: +0.10 (强板块效应)
            - 题材年龄 3-5 天 (黄金期): +0.05
            - 龙头分歧日放量 ≥ 1.5x: +0.05 (高位换手充分)
            - 分歧日跌幅在 -1%~-3% (理想): +0.05
        """
        conf = self.params.confidence_threshold

        heat = market_data.get("theme_heat", 0)
        leader_days = market_data.get("leader_limit_up_days", 0)
        breadth = market_data.get("sector_breadth", 0)
        theme_age = market_data.get("theme_age_days", 0)
        leader_vol = market_data.get("leader_volume_ratio", 0)
        leader_pause = market_data.get("leader_pause_pct", 0)

        if heat >= 80:
            conf += 0.10
        elif heat >= 70:
            conf += 0.05

        if leader_days >= 5:
            conf += 0.10
        elif leader_days >= 4:
            conf += 0.05

        if breadth >= 10:
            conf += 0.10
        elif breadth >= 7:
            conf += 0.05

        if 3 <= theme_age <= 5:
            conf += 0.05

        if leader_vol >= 1.5:
            conf += 0.05

        if -3.0 <= leader_pause <= -1.0:
            conf += 0.05  # 理想的分歧日回调幅度

        return max(0.0, min(conf, 1.0))


def make_theme(
    mode: str = "normal",
    bias: str = "balanced",
    **overrides,
) -> ThemeStrategy:
    """创建 ThemeStrategy 的便捷函数"""
    filters = {**ThemeStrategy.DEFAULT_FILTERS, **overrides}
    params = StrategyParams(
        strategy_type="theme",
        mode=mode,
        bias=bias,
        filters=filters,
    )
    return ThemeStrategy(params)