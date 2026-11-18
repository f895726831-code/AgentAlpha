"""
T9 CANSLIM 成长股策略 — William O'Neil 七维选股体系的量化实现。

CANSLIM 是 Investor's Business Daily (IBD) 创始人 William O'Neil 的
经典成长股投资方法，基于对 1880-2009 年所有超级牛股的历史回测。

七维评分:
  C — Current Earnings: 当季 EPS 同比增长 ≥ 25%
  A — Annual Earnings: 近 3 年 EPS 年均增长 ≥ 25%，且逐年加速
  N — New: 距 52 周高点 ≤ 15%（新高附近），或新产品/新管理层
  S — Supply & Demand: 成交量确认（机构吸筹特征）
  L — Leader: RS 评级 ≥ 80（相对强度在市场前 20%）
  I — Institutional: 机构资金净流入（含北向）
  M — Market: 大盘在 MA200 之上（由 STABLE regime 兜底，此处做局部验证）

计分权重 (O'Neil 原作 7 维等权，C+A 双倍):
  C: 2/7, A: 2/7, N: 1/7, S: 1/7, L: 1/7, I: 1/7, M: 1/7

策略逻辑:
  1. 7 维基本面筛选（C/A/N/S/L/I/M），总分 ≥ 60% 通过 Gate 1
  2. 技术面入场确认（放量突破近期高点/缩量回调至 MA20）
  3. 止损: 买入价 -8%（O'Neil 铁律）或 MA50，取更高的
  4. 止盈: 盈利 +20-25%（O'Neil 规则），或 trailing stop

注意: 此策略依赖基本面数据，离线编译时 LLM 负责填入具体的筛选标的列表。
数据源: akshare (stock_yjbb_em, stock_financial_analysis_indicator,
  stock_a_lg_indicator, stock_individual_fund_flow, stock_hsgt_individual_em).

TiMi 离线编译参数 (LLM 可调):
  由 StrategyParams.filters 承载所有策略特有参数。

用法:
    from strategies.canslim import CanslimStrategy
    from strategies.base import StrategyParams

    params = StrategyParams(
        strategy_type="canslim",
        mode="normal",
        filters={
            "min_eps_growth_yoy_pct": 25.0,
            "min_annual_eps_growth_pct": 25.0,
            "min_roe_pct": 17.0,
            "rs_rating_min": 80,
        },
    )
    strategy = CanslimStrategy(params)
"""

from typing import Any, Dict, List, Optional

from .base import BaseStrategy, Signal, StrategyParams


class CanslimStrategy(BaseStrategy):
    """
    T9 CANSLIM 成长股策略 — 七维量化评分。

    参数 (全部通过 StrategyParams.filters 传入):
        # ── C: Current Earnings ──
        min_eps_growth_yoy_pct: float = 25.0
            当季 EPS 同比增长最低要求 (%)。O'Neil 标准 ≥ 25%。

        min_revenue_growth_yoy_pct: float = 20.0
            当季营收同比增长最低要求 (%)。辅助过滤"一次性的 EPS 暴增"。

        # ── A: Annual Earnings ──
        min_annual_eps_growth_pct: float = 25.0
            近 3 年 EPS 年均增速最低要求 (%)。

        min_roe_pct: float = 17.0
            最低 ROE (%)。O'Neil 标准 ≥ 17%。

        require_earnings_acceleration: bool = True
            是否要求 EPS 增速逐年递增。True = Canslim 正宗筛选。

        # ── N: New ──
        max_distance_from_high_pct: float = 15.0
            距 52 周高点最大距离 (%)。≤ 15% = 处于新高附近。

        min_quarterly_earnings_accel: float = 0.0
            最近两季度 EPS 增速差 (%)。正值 = 加速。

        # ── S: Supply & Demand ──
        min_volume_ratio: float = 0.8
            日成交量 vs 50 日均量的最小比率。> 1.0 = 放量。

        max_share_outstanding_billion: float = 50.0
            最大流通股本 (亿股)。小盘成长股更符合 CANSLIM。

        accumulation_days_min: int = 3
            近 10 日最小吸筹日数。放量上涨日 = 吸筹日。

        # ── L: Leader ──
        rs_rating_min: float = 80.0
            IBD RS 评级最低分 (0-99)。≥ 80 = 市场前 20%。

        rs_lookback_days: int = 252
            RS 计算回看天数。默认 252 个交易日 (~1 年)。

        # ── I: Institutional ──
        min_institutional_flow_million: float = 50.0
            近 20 日机构净流入最低额 (万元)。

        northbound_required: bool = False
            是否必须北向资金净流入。False = 不强制（很多优质成长股非沪深港通标的）。

        # ── M: Market Direction ──
        ma_market_trend: int = 200
            大盘 MA 周期。O'Neil 使用 200 日均线判断大势。

        require_market_uptrend: bool = True
            是否要求大盘在 MA 之上。风险偏好者可关闭。

        # ── 风控 ──
        stop_loss_pct: float = 8.0
            止损百分比。O'Neil 铁律: 永远不超过 8%。

        take_profit_pct: float = 25.0
            止盈百分比。O'Neil 规则: +20-25% 止盈。

        max_pe_ratio: float = 50.0
            最大 PE 比率。过滤估值离谱的标的（A 股特殊，PE 容忍度较高）。

        min_market_cap_billion: float = 10.0
            最低市值 (亿元)。过滤微盘股噪音。

        # ── 入场时机 ──
        entry_pullback_to_ma20: bool = True
            是否要求入场时回调至 MA20 附近 (≤ 3%)。经典 CANSLIM 入场点。

        entry_breakout_volume_ratio: float = 1.2
            突破入场时的成交量确认倍率。
    """

    DEFAULT_FILTERS: Dict[str, Any] = {
        # C: Current Earnings
        "min_eps_growth_yoy_pct": 25.0,
        "min_revenue_growth_yoy_pct": 20.0,
        # A: Annual Earnings
        "min_annual_eps_growth_pct": 25.0,
        "min_roe_pct": 17.0,
        "require_earnings_acceleration": True,
        # N: New
        "max_distance_from_high_pct": 15.0,
        "min_quarterly_earnings_accel": 0.0,
        # S: Supply & Demand
        "min_volume_ratio": 0.8,
        "max_share_outstanding_billion": 50.0,
        "accumulation_days_min": 3,
        # L: Leader
        "rs_rating_min": 80.0,
        "rs_lookback_days": 252,
        # I: Institutional
        "min_institutional_flow_million": 50.0,
        "northbound_required": False,
        # M: Market Direction
        "ma_market_trend": 200,
        "require_market_uptrend": True,
        # 风控
        "stop_loss_pct": 8.0,
        "take_profit_pct": 25.0,
        "max_pe_ratio": 50.0,
        "min_market_cap_billion": 10.0,
        # 入场时机
        "entry_pullback_to_ma20": True,
        "entry_breakout_volume_ratio": 1.2,
    }

    # CANSLIM 七维权重 (O'Neil 原作: C+A 双倍)
    SCORE_WEIGHTS = {
        "C": 2.0 / 7.0,  # Current Earnings
        "A": 2.0 / 7.0,  # Annual Earnings
        "N": 1.0 / 7.0,  # New
        "S": 1.0 / 7.0,  # Supply & Demand
        "L": 1.0 / 7.0,  # Leader
        "I": 1.0 / 7.0,  # Institutional
        "M": 1.0 / 7.0,  # Market Direction
    }

    @property
    def strategy_type(self) -> str:
        return "canslim"

    def _get_filter(self, key: str) -> Any:
        return self.params.filters.get(key, self.DEFAULT_FILTERS.get(key))

    # ── 七维评分引擎 ──────────────────────────────────────────────

    def _score_C(self, md: Dict[str, Any]) -> tuple:
        """
        C: Current Earnings — 当季盈利增长。

        检查: EPS 同比增长 + 营收同比增长。
        返回: (score_0_to_1, details_dict)
        """
        eps_growth = md.get("eps_growth_yoy_pct", 0)
        revenue_growth = md.get("revenue_growth_yoy_pct", 0)
        min_eps = self._get_filter("min_eps_growth_yoy_pct")
        min_rev = self._get_filter("min_revenue_growth_yoy_pct")

        # EPS growth 评分
        if eps_growth >= min_eps * 2:
            eps_score = 1.0  # ≥50% = 满分
        elif eps_growth >= min_eps:
            eps_score = 0.5 + 0.5 * (eps_growth - min_eps) / min_eps
        elif eps_growth > 0:
            eps_score = 0.5 * eps_growth / min_eps
        else:
            eps_score = 0.0

        # Revenue growth 辅助评分
        if revenue_growth >= min_rev:
            rev_score = 1.0
        elif revenue_growth > 0:
            rev_score = revenue_growth / min_rev
        else:
            rev_score = 0.0

        # EPS 权重 70%，营收权重 30%
        score = eps_score * 0.7 + rev_score * 0.3
        return score, {"eps_growth": eps_growth, "revenue_growth": revenue_growth}

    def _score_A(self, md: Dict[str, Any]) -> tuple:
        """
        A: Annual Earnings — 年度盈利质量。

        检查: ROE + 3 年 EPS 年化增速 + 增速是否加速。
        返回: (score_0_to_1, details_dict)
        """
        roe = md.get("roe_pct", 0)
        annual_eps = md.get("annual_eps_growth_3y_pct", 0)
        accelerating = md.get("earnings_accelerating", False)
        min_roe = self._get_filter("min_roe_pct")
        min_annual = self._get_filter("min_annual_eps_growth_pct")
        require_accel = self._get_filter("require_earnings_acceleration")

        # ROE 评分 (0-0.4)
        if roe >= min_roe:
            roe_score = 0.4
        elif roe > 0:
            roe_score = 0.4 * roe / min_roe
        else:
            roe_score = 0.0

        # 年度 EPS 增速评分 (0-0.4)
        if annual_eps >= min_annual:
            annual_score = 0.4
        elif annual_eps > 0:
            annual_score = 0.4 * annual_eps / min_annual
        else:
            annual_score = 0.0

        # 加速度加分 (0-0.2)
        if require_accel and accelerating:
            accel_score = 0.2
        elif not require_accel:
            accel_score = 0.1  # 不要求加速时不扣分
        else:
            accel_score = 0.0

        score = roe_score + annual_score + accel_score
        return score, {
            "roe": roe, "annual_eps_growth": annual_eps, "accelerating": accelerating
        }

    def _score_N(self, md: Dict[str, Any]) -> tuple:
        """
        N: New — 新高附近 + 盈利加速。

        检查: 距 52 周高点距离 + 季度盈利加速度。
        返回: (score_0_to_1, details_dict)
        """
        dist_high = md.get("pct_from_52w_high", 100)
        max_dist = self._get_filter("max_distance_from_high_pct")
        q_accel = md.get("quarterly_earnings_accel_pct", 0)
        min_accel = self._get_filter("min_quarterly_earnings_accel")

        # 距高点评分 (0-0.6): 越近越高分
        if dist_high <= 5:
            high_score = 0.6
        elif dist_high <= max_dist:
            high_score = 0.6 * (1.0 - (dist_high - 5) / (max_dist - 5))
        elif dist_high <= max_dist * 2:
            high_score = 0.2
        else:
            high_score = 0.0

        # 盈利加速度评分 (0-0.4)
        if q_accel >= 10.0:
            accel_score = 0.4
        elif q_accel >= min_accel:
            accel_score = 0.2
        elif q_accel >= -5.0:
            accel_score = 0.1
        else:
            accel_score = 0.0

        score = high_score + accel_score
        return score, {"dist_from_high": dist_high, "q_accel": q_accel}

    def _score_S(self, md: Dict[str, Any]) -> tuple:
        """
        S: Supply & Demand — 成交量与股本结构。

        检查: 流通股本规模 + 吸筹日数量 + 量比。
        返回: (score_0_to_1, details_dict)
        """
        shares_out = md.get("share_outstanding_billion", 999)
        max_shares = self._get_filter("max_share_outstanding_billion")
        accum_days = md.get("accumulation_days_10d", 0)
        min_accum = self._get_filter("accumulation_days_min")
        vol_ratio = md.get("volume_ratio_50d", 1.0)
        min_vol = self._get_filter("min_volume_ratio")

        # 流通股本评分 (0-0.3): 小盘 > 中盘 > 大盘
        if shares_out <= 5:
            cap_score = 0.3
        elif shares_out <= max_shares:
            cap_score = 0.3 * (1.0 - (shares_out - 5) / (max_shares - 5))
        else:
            cap_score = 0.0

        # 吸筹日评分 (0-0.4)
        if accum_days >= min_accum + 2:
            accum_score = 0.4
        elif accum_days >= min_accum:
            accum_score = 0.3
        elif accum_days >= 2:
            accum_score = 0.15
        else:
            accum_score = 0.0

        # 量比评分 (0-0.3): 温和放量最佳
        if 1.0 <= vol_ratio <= 2.0:
            vol_score = 0.3  # 温和放量 = 机构建仓
        elif vol_ratio > 2.0:
            vol_score = 0.15  # 过度放量 = 可能有出货
        elif vol_ratio >= min_vol:
            vol_score = 0.15
        else:
            vol_score = 0.0

        score = cap_score + accum_score + vol_score
        return score, {
            "shares_outstanding": shares_out,
            "accum_days": accum_days,
            "vol_ratio": vol_ratio,
        }

    def _score_L(self, md: Dict[str, Any]) -> tuple:
        """
        L: Leader — 相对强度。

        检查: RS 评级 + 相对大盘超额收益。
        返回: (score_0_to_1, details_dict)
        """
        rs_rating = md.get("rs_rating", 50)
        rs_min = self._get_filter("rs_rating_min")
        excess_return_3m = md.get("excess_return_3m_pct", 0)

        # RS 评级评分 (0-0.7)
        if rs_rating >= 90:
            rs_score = 0.7
        elif rs_rating >= rs_min:
            rs_score = 0.7 * (rs_rating - rs_min) / (90 - rs_min)
        elif rs_rating >= 60:
            rs_score = 0.2
        else:
            rs_score = 0.0

        # 超额收益评分 (0-0.3)
        if excess_return_3m >= 20.0:
            excess_score = 0.3
        elif excess_return_3m > 0:
            excess_score = 0.3 * excess_return_3m / 20.0
        else:
            excess_score = 0.0

        score = rs_score + excess_score
        return score, {"rs_rating": rs_rating, "excess_return_3m": excess_return_3m}

    def _score_I(self, md: Dict[str, Any]) -> tuple:
        """
        I: Institutional — 机构资金。

        检查: 机构净流入 + 北向资金。
        返回: (score_0_to_1, details_dict)
        """
        inst_flow = md.get("institutional_flow_20d_million", 0)
        min_flow = self._get_filter("min_institutional_flow_million")
        nbound = md.get("northbound_net_inflow_20d_million", 0)
        nbound_required = self._get_filter("northbound_required")

        # 机构净流入评分 (0-0.6)
        if inst_flow >= min_flow * 3:
            flow_score = 0.6
        elif inst_flow >= min_flow:
            flow_score = 0.4
        elif inst_flow > 0:
            flow_score = 0.2
        else:
            flow_score = 0.0

        # 北向加分 (0-0.4)
        if nbound > 100:
            north_score = 0.4
        elif nbound > 0:
            north_score = 0.2
        elif not nbound_required:
            north_score = 0.0  # 不强制
        else:
            north_score = 0.0

        score = flow_score + north_score
        return score, {"inst_flow": inst_flow, "northbound_flow": nbound}

    def _score_M(self, md: Dict[str, Any]) -> tuple:
        """
        M: Market Direction — 大盘方向。

        检查: 大盘指数在 MA 之上。
        返回: (score_0_to_1, details_dict)
        """
        ma_period = self._get_filter("ma_market_trend")
        require_uptrend = self._get_filter("require_market_uptrend")

        if not require_uptrend:
            return 1.0, {"market_trend": "not_required"}

        # 大盘在 MA 之上
        index_above_ma = md.get("index_above_ma200", None)
        index_close = md.get("index_close", 0)
        index_ma = md.get(f"index_ma_{ma_period}", 0)

        if index_above_ma is True:
            return 1.0, {"market_trend": "uptrend"}
        elif index_close > index_ma > 0:
            return 0.8, {"market_trend": "marginal_uptrend"}
        elif index_close > 0 and index_ma > 0:
            # 大盘在 MA 之下，计算偏离度
            deviation = (index_close - index_ma) / index_ma * 100
            if deviation > -3.0:
                return 0.5, {"market_trend": "near_ma", "deviation_pct": deviation}
            elif deviation > -8.0:
                return 0.3, {"market_trend": "correction", "deviation_pct": deviation}
            else:
                return 0.0, {"market_trend": "bear_market", "deviation_pct": deviation}
        else:
            # 无大盘数据，中性处理
            return 0.5, {"market_trend": "unknown"}

    # ── 综合评分 ──────────────────────────────────────────────────

    def compute_canslim_score(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        计算 CANSLIM 七维综合评分。

        返回:
            {
                "total_score": 0.0-1.0,
                "passed": bool,
                "dimensions": {
                    "C": score, "A": score, "N": score,
                    "S": score, "L": score, "I": score, "M": score,
                },
                "dimension_details": {...},
                "missing_data": [...],
            }
        """
        dimensions = {}
        details = {}
        missing = []

        # 逐维评分
        for dim, scorer in [
            ("C", self._score_C),
            ("A", self._score_A),
            ("N", self._score_N),
            ("S", self._score_S),
            ("L", self._score_L),
            ("I", self._score_I),
            ("M", self._score_M),
        ]:
            try:
                score, dim_detail = scorer(market_data)
                dimensions[dim] = score
                details[dim] = dim_detail
            except Exception:
                dimensions[dim] = 0.0
                details[dim] = {}
                missing.append(dim)

        # 加权总分
        total = sum(
            dimensions[dim] * self.SCORE_WEIGHTS[dim]
            for dim in self.SCORE_WEIGHTS
        )

        # 通过门槛: ≥ 0.60 且 C + A 必须 > 0 (盈利不能为 0)
        ca_min = (dimensions["C"] + dimensions["A"]) / 2
        passed = total >= 0.60 and ca_min >= 0.3

        return {
            "total_score": round(total, 3),
            "passed": passed,
            "dimensions": dimensions,
            "dimension_details": details,
            "missing_data": missing,
        }

    # ── 核心方法 ──────────────────────────────────────────────────

    def check_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        检查 CANSLIM 入场条件。

        市场数据要求:
            symbol, close, open, high, low, volume,
            ma_20, ma_50, ma_200 (均线系统),
            # C: Current Earnings
            eps_growth_yoy_pct, revenue_growth_yoy_pct,
            # A: Annual Earnings
            roe_pct, annual_eps_growth_3y_pct, earnings_accelerating,
            # N: New
            pct_from_52w_high, quarterly_earnings_accel_pct,
            # S: Supply & Demand
            share_outstanding_billion, accumulation_days_10d, volume_ratio_50d,
            # L: Leader
            rs_rating, excess_return_3m_pct,
            # I: Institutional
            institutional_flow_20d_million, northbound_net_inflow_20d_million,
            # M: Market
            index_above_ma200, index_close, index_ma_200

        条件:
            1. CANSLIM 综合评分 ≥ 60% (Gate 1 基本面筛选)
            2. C + A 均分 ≥ 30% (盈利维度不能为 0)
            3. 市值 ≥ 最低要求
            4. PE ≤ 最大容忍
            5. 技术面入场确认 (MA20 回调 或 放量突破)
        """
        close = market_data.get("close", 0)
        if close <= 0:
            return False

        # ── Gate 1: CANSLIM 基本面评分 ──
        cs_score = self.compute_canslim_score(market_data)
        if not cs_score["passed"]:
            return False

        # ── Gate 2: 市值过滤 ──
        min_cap = self._get_filter("min_market_cap_billion")
        mcap = market_data.get("market_cap_billion", 0)
        if mcap < min_cap:
            return False

        # ── Gate 3: PE 过滤 ──
        max_pe = self._get_filter("max_pe_ratio")
        pe = market_data.get("pe_ttm", 0)
        if pe > max_pe > 0:
            return False

        # ── Gate 4: 有基本面数据 (至少 C 和 A 不能全部缺失) ──
        missing = cs_score.get("missing_data", [])
        essential = {"C", "A"}
        if essential.intersection(missing):
            return False  # 核心盈利数据缺失，不生成信号

        # ── Gate 5: 技术面入场确认 ──
        entry_ok = self._check_entry_setup(market_data)
        if not entry_ok:
            return False

        return True

    def _check_entry_setup(self, market_data: Dict[str, Any]) -> bool:
        """
        技术面入场时机确认。

        两种入场模式:
          1. 回调至 MA20 附近 (≤ 3%): 经典 CANSLIM "买在支撑" 入场
          2. 放量突破近期高点: 强势突破入场

        满足其一即可。
        """
        close = market_data.get("close", 0)
        ma20 = market_data.get("ma_20", 0)
        volume = market_data.get("volume", 0)
        avg_vol = market_data.get("avg_volume_50d", 0)
        high_20d = market_data.get("high_20d", 0)

        use_pullback = self._get_filter("entry_pullback_to_ma20")
        vol_ratio_min = self._get_filter("entry_breakout_volume_ratio")

        # ── 模式 1: MA20 回调入场 ──
        if use_pullback and ma20 > 0:
            dist_to_ma20 = (close - ma20) / ma20 * 100
            if 0 <= dist_to_ma20 <= 3.0:
                # 价在 MA20 上方且接近 = 理想入场
                return True
            if -3.0 <= dist_to_ma20 < 0:
                # 略低于 MA20，但成交量萎缩 = 缩量回调
                if avg_vol > 0 and volume < avg_vol * 0.8:
                    return True

        # ── 模式 2: 放量突破入场 ──
        if high_20d > 0 and close >= high_20d * 0.98:
            if avg_vol > 0 and volume > avg_vol * vol_ratio_min:
                return True

        # ── 兜底: 无明确入场模式时，如果在 MA50 上方且放量，也接受 ──
        ma50 = market_data.get("ma_50", 0)
        if ma50 > 0 and close > ma50:
            if avg_vol > 0 and volume >= avg_vol * 0.9:
                return True

        return False

    def generate_signal(self, market_data: Dict[str, Any]) -> Optional[Signal]:
        """
        生成 CANSLIM 成长股入场信号。

        止损: max(买入价 - stop_loss%, MA50) — O'Neil 铁律 + 均线保护。
        止盈: 买入价 + take_profit%。
        持有期: 无固定期限，止损/止盈触发或 regime 切换为止。
        """
        close = market_data.get("close", 0)
        volume = market_data.get("volume", 0)
        avg_vol_50d = market_data.get("avg_volume_50d", 0)

        stop_loss_pct = self._get_filter("stop_loss_pct")
        take_profit_pct = self._get_filter("take_profit_pct")

        # 止损: O'Neil 8% 硬止损 + MA50 兜底
        pct_stop = close * (1 - stop_loss_pct / 100)
        ma50 = market_data.get("ma_50", 0)
        if ma50 > 0:
            stop_loss = max(pct_stop, ma50)  # 取更高的 (更紧的止损)
        else:
            stop_loss = pct_stop

        trigger_price = close
        take_profit = [close * (1 + take_profit_pct / 100)]

        confidence = self.get_confidence(market_data)

        # Edge: CANSLIM 历史胜率 ~35-40%，但盈亏比 3:1+
        win_rate = 0.38
        avg_gain = take_profit_pct / 100
        avg_loss = stop_loss_pct / 100
        edge = (avg_gain * win_rate - avg_loss * (1 - win_rate)) / avg_loss

        cs_score = self.compute_canslim_score(market_data)

        return Signal(
            symbol=str(market_data.get("symbol", "")),
            strategy_type=self.strategy_type,
            direction="long",
            confidence=confidence,
            trigger_price=trigger_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            edge=round(edge, 3),
            win_rate=win_rate,
            metadata={
                "close": close,
                "volume_ratio": volume / max(avg_vol_50d, 1),
                "stop_loss_pct": (trigger_price - stop_loss) / trigger_price * 100,
                "take_profit_pct": take_profit_pct,
                "pe_ttm": market_data.get("pe_ttm", 0),
                "roe_pct": market_data.get("roe_pct", 0),
                "eps_growth": market_data.get("eps_growth_yoy_pct", 0),
                "rs_rating": market_data.get("rs_rating", 50),
                "canslim_score": cs_score["total_score"],
                "canslim_dimensions": cs_score["dimensions"],
                "canslim_detail": {
                    dim: f"{cs_score['dimensions'][dim]:.2f}" for dim in cs_score["dimensions"]
                },
                "generated_by": "TiMi_CanslimStrategy",
            },
        )

    def get_confidence(self, market_data: Dict[str, Any]) -> float:
        """
        CANSLIM 信号置信度 (0-1)。

        基准 0.50，七维评分加权贡献:
          - CANSLIM 总分 ≥ 0.80: +0.20
          - CANSLIM 总分 ≥ 0.70: +0.10
          - C + A 均分 ≥ 0.80: +0.10 (盈利质量极高)
          - 距 52 周高 < 5%: +0.05 (强势)
          - RS ≥ 90: +0.05 (龙头中的龙头)
          - 机构 + 北向双重流入: +0.05
          - 数据完整度: 7/7 无缺失: +0.05
        """
        conf = 0.50

        cs_score = self.compute_canslim_score(market_data)
        total = cs_score["total_score"]
        dims = cs_score["dimensions"]
        missing = cs_score.get("missing_data", [])

        # 总分梯度
        if total >= 0.80:
            conf += 0.20
        elif total >= 0.70:
            conf += 0.10
        elif total >= 0.65:
            conf += 0.05

        # C + A 盈利质量
        ca_avg = (dims.get("C", 0) + dims.get("A", 0)) / 2
        if ca_avg >= 0.80:
            conf += 0.10
        elif ca_avg >= 0.60:
            conf += 0.05

        # N: 新高附近
        dist_high = market_data.get("pct_from_52w_high", 100)
        if dist_high <= 5.0:
            conf += 0.05

        # L: 顶级龙头
        rs = market_data.get("rs_rating", 50)
        if rs >= 90:
            conf += 0.05

        # I: 双重机构流入
        inst = market_data.get("institutional_flow_20d_million", 0)
        north = market_data.get("northbound_net_inflow_20d_million", 0)
        if inst > 0 and north > 0:
            conf += 0.05

        # 数据完整度
        if len(missing) == 0:
            conf += 0.05

        # 技术面质量: 在 MA20 上方 + 均线多头排列
        close = market_data.get("close", 0)
        ma20 = market_data.get("ma_20", 0)
        ma50 = market_data.get("ma_50", 0)
        if close > ma20 > ma50 > 0:
            conf += 0.05

        return max(0.0, min(conf, 1.0))


def make_canslim(
    mode: str = "normal",
    bias: str = "balanced",
    **overrides,
) -> CanslimStrategy:
    """创建 CanslimStrategy 的便捷函数"""
    filters = {**CanslimStrategy.DEFAULT_FILTERS, **overrides}
    params = StrategyParams(
        strategy_type="canslim",
        mode=mode,
        bias=bias,
        filters=filters,
    )
    return CanslimStrategy(params)