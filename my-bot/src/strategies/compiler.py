"""
策略编译器 — LLM JSON 输出 → 可执行 Python 策略对象。

TiMi 离线编译流程:
  1. market-watcher 判档 (regime + bias)
  2. LLM Agent 输出策略选择 JSON
  3. StrategyCompiler.compile() → Dict[str, BaseStrategy]
  4. StrategySerializer → disk

LLM JSON 输入格式:
{
  "regime": "进攻",
  "bias": "right_priority",
  "mode": "normal",
  "strategies": [
    {
      "type": "T1_breakout",
      "active": true,
      "params": { "lookback_days": 30, "volume_ratio_min": 1.5 }
    },
    {
      "type": "T2_pullback",
      "active": false
    }
  ],
  "filters": {
    "min_market_cap": 5e9,
    "exclude_st": true,
    "exclude_new_listings_days": 60
  },
  "meta": {
    "compiled_by": "deepseek-v3",
    "timestamp": "2026-10-14T08:00:00"
  }
}

用法:
    from strategies.compiler import StrategyCompiler

    compiler = StrategyCompiler()
    strategies = compiler.compile(llm_json)
    warnings = compiler.validate(strategies)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from .base import BaseStrategy, StrategyParams
from .breakout import BreakoutStrategy
from .pullback import PullbackStrategy
from .vcp import VCPStrategy
from .oversold_bounce import OversoldBounceStrategy
from .golden_cross import GoldenCrossStrategy
from .earnings import EarningsStrategy
from .capital import CapitalStrategy
from .theme import ThemeStrategy
from .canslim import CanslimStrategy


# ─── 策略类型 → 类映射 ───

STRATEGY_CLASS_MAP: Dict[str, Type[BaseStrategy]] = {
    "T1_breakout": BreakoutStrategy,
    "T2_pullback": PullbackStrategy,
    "VCP_breakout": VCPStrategy,
    "T3_oversold_bounce": OversoldBounceStrategy,
    "T4_golden_cross": GoldenCrossStrategy,
    "T5_earnings": EarningsStrategy,
    "T6_capital": CapitalStrategy,
    "theme": ThemeStrategy,
    "canslim": CanslimStrategy,
}


@dataclass
class CompileResult:
    """编译结果"""

    strategies: Dict[str, BaseStrategy]         # strategy_type → strategy instance
    regime: str = ""                            # 市场判档
    bias: str = ""                              # 策略偏好
    mode: str = "normal"                        # 运行模式
    filters: Dict[str, Any] = field(default_factory=dict)  # 全局过滤器
    warnings: List[str] = field(default_factory=list)      # 编译警告
    errors: List[str] = field(default_factory=list)        # 编译错误
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def active_strategies(self) -> Dict[str, BaseStrategy]:
        """仅返回 active=True 的策略"""
        return {
            k: v for k, v in self.strategies.items()
            if v.params.active
        }

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


class StrategyCompiler:
    """
    策略编译器 — LLM JSON → Python 策略对象。

    LLM 输出策略配置 JSON，编译器负责:
      1. 校验 JSON 结构完整性
      2. 映射 strategy type → Python 类
      3. 实例化策略 (参数从 JSON 注入)
      4. 参数合理性校验
      5. 生成编译警告/错误
    """

    # 参数值合理范围 (超出产生警告)
    PARAM_BOUNDS: Dict[str, Dict[str, tuple]] = {
        "T1_breakout": {
            "lookback_days": (10, 120),
            "volume_ratio_min": (0, 5.0),
            "gap_max_pct": (0, 10.0),
            "stop_loss_pct": (1.0, 15.0),
            "take_profit_pct": (3.0, 50.0),
        },
        "T2_pullback": {
            "trend_period": (20, 250),
            "ma_period": (5, 60),
            "pullback_pct_min": (1.0, 15.0),
            "pullback_pct_max": (3.0, 20.0),
            "volume_shrink_ratio": (0.1, 1.0),
            "stop_loss_pct": (1.0, 15.0),
            "take_profit_pct": (3.0, 30.0),
        },
        "VCP_breakout": {
            "lookback_days": (20, 120),
            "contraction_ratio": (0.1, 0.8),
            "breakout_volume_ratio": (1.0, 5.0),
            "stop_loss_pct": (1.0, 15.0),
            "take_profit_pct": (5.0, 50.0),
        },
        "T3_oversold_bounce": {
            "rsi_period": (7, 28),
            "rsi_oversold": (15, 40),
            "volume_shrink_ratio": (0.1, 1.0),
            "stop_loss_pct": (1.0, 10.0),
            "take_profit_pct": (3.0, 20.0),
        },
        "T4_golden_cross": {
            "ma_short": (5, 60),
            "ma_long": (30, 250),
            "volume_confirm_ratio": (0, 5.0),
            "stop_loss_pct": (1.0, 15.0),
            "take_profit_pct": (5.0, 50.0),
        },
        "T5_earnings": {
            "surprise_min_pct": (1.0, 10.0),
            "post_earnings_drift_days": (20, 120),
            "entry_window_days": (1, 10),
            "volume_confirmation_ratio": (0, 5.0),
            "max_gap_up_pct": (3.0, 10.0),
            "stop_loss_pct": (2.0, 15.0),
            "take_profit_pct": (5.0, 30.0),
            "ma_trend_period": (20, 120),
            "min_market_cap_billion": (1.0, 100.0),
        },
        "T6_capital": {
            "capital_inflow_days_min": (2, 10),
            "inflow_amount_min_million": (10.0, 500.0),
            "price_confirm_days": (2, 5),
            "volume_ratio_min": (0, 5.0),
            "min_market_cap_billion": (3.0, 100.0),
            "stop_loss_pct": (2.0, 10.0),
            "take_profit_pct": (3.0, 20.0),
            "max_holding_days": (3, 20),
            "consecutive_up_days_max": (3, 10),
        },
        "theme": {
            "leader_limit_up_days_min": (2, 10),
            "leader_pause_max_pct": (-8.0, -1.0),
            "sector_breadth_min": (3, 20),
            "heat_threshold": (40.0, 90.0),
            "sustainability_days_min": (1, 7),
            "max_theme_age_days": (5, 20),
            "stop_loss_pct": (3.0, 15.0),
            "take_profit_pct": (10.0, 40.0),
            "trailing_stop_pct": (3.0, 10.0),
        },
        "canslim": {
            "min_eps_growth_yoy_pct": (15.0, 100.0),
            "min_revenue_growth_yoy_pct": (10.0, 80.0),
            "min_annual_eps_growth_pct": (15.0, 100.0),
            "min_roe_pct": (10.0, 50.0),
            "max_distance_from_high_pct": (5.0, 30.0),
            "max_share_outstanding_billion": (1.0, 200.0),
            "rs_rating_min": (60.0, 99.0),
            "min_institutional_flow_million": (10.0, 500.0),
            "min_market_cap_billion": (5.0, 500.0),
            "max_pe_ratio": (10.0, 100.0),
            "stop_loss_pct": (3.0, 12.0),
            "take_profit_pct": (10.0, 50.0),
            "entry_breakout_volume_ratio": (1.0, 3.0),
        },
    }

    def __init__(self, class_map: Dict[str, Type[BaseStrategy]] = None):
        """
        Args:
            class_map: 策略类型→类映射。默认使用 STRATEGY_CLASS_MAP。
        """
        self.class_map = class_map or STRATEGY_CLASS_MAP

    def compile(self, llm_json: Dict[str, Any]) -> CompileResult:
        """
        编译 LLM JSON 输出为策略对象字典。

        Args:
            llm_json: LLM Agent 输出的策略配置 JSON

        Returns:
            CompileResult (包含 strategies, warnings, errors)
        """
        warnings: List[str] = []
        errors: List[str] = []

        # ── 提取顶层字段 ──
        regime = str(llm_json.get("regime", ""))
        bias = str(llm_json.get("bias", "balanced"))
        mode = str(llm_json.get("mode", "normal"))
        filters = llm_json.get("filters", {}) or {}
        meta = llm_json.get("meta", {}) or {}
        strategy_configs = llm_json.get("strategies", [])

        if not strategy_configs:
            errors.append("strategies 字段为空 — 无策略可编译")

        # ── 逐个编译策略 ──
        strategies: Dict[str, BaseStrategy] = {}
        for cfg in strategy_configs:
            stype = cfg.get("type", "")
            active = cfg.get("active", True)
            params = cfg.get("params", {}) or {}

            if not stype:
                warnings.append(f"跳过无 type 字段的策略配置: {cfg}")
                continue

            if stype not in self.class_map:
                warnings.append(f"未知策略类型 '{stype}' — 跳过 (可用: {list(self.class_map.keys())})")
                continue

            try:
                strategy_cls = self.class_map[stype]

                # 构建 StrategyParams
                strategy_params = StrategyParams(
                    strategy_type=stype,
                    mode=mode,
                    bias=bias,
                    active=active,
                    filters=params,
                    metadata={"compiled_by": meta.get("compiled_by", "unknown")},
                )

                # 实例化策略
                strategy = strategy_cls(strategy_params)
                strategies[stype] = strategy

                # 校验参数范围
                param_warnings = self._check_param_bounds(stype, params)
                warnings.extend(param_warnings)

            except Exception as e:
                errors.append(f"编译 {stype} 失败: {e}")

        return CompileResult(
            strategies=strategies,
            regime=regime,
            bias=bias,
            mode=mode,
            filters=filters,
            warnings=warnings,
            errors=errors,
            meta=meta,
        )

    def validate(self, result: CompileResult) -> List[str]:
        """
        验证编译结果的合理性 — 跨策略一致性检查。

        检查项:
          1. bias 与策略兼容性
          2. 同向 bias 下策略权重方向一致
          3. 策略参数之间的一致性

        Returns:
            验证警告列表
        """
        warnings: List[str] = []

        if not result.is_valid:
            warnings.append("编译存在错误，跳过验证")
            return warnings

        bias = result.bias
        active = result.active_strategies

        # ── Bias 兼容性 ──
        incompatible = {
            "right_priority": ["T3_oversold_bounce"],
            "left_priority": ["T1_breakout", "T4_golden_cross", "VCP_breakout", "theme"],
        }
        disabled = incompatible.get(bias, [])
        for stype in disabled:
            if stype in active:
                warnings.append(f"策略 {stype} 在 bias={bias} 下不推荐激活")

        # ── 策略数量检查 ──
        if len(active) == 0:
            warnings.append("无活跃策略 — 市场状态下无适用模板")
        elif len(active) > 6:
            warnings.append(f"活跃策略数过多 ({len(active)}) — 建议聚焦 3-5 种")

        # ── 参数一致性检查 ──
        for stype, strategy in active.items():
            if stype == "T2_pullback":
                pb_min = strategy.params.filters.get("pullback_pct_min", 0)
                pb_max = strategy.params.filters.get("pullback_pct_max", 0)
                if pb_min >= pb_max:
                    warnings.append(f"{stype}: pullback_pct_min ({pb_min}) >= pullback_pct_max ({pb_max})")

            if stype == "T4_golden_cross":
                ma_s = strategy.params.filters.get("ma_short", 0)
                ma_l = strategy.params.filters.get("ma_long", 0)
                if ma_s >= ma_l:
                    warnings.append(f"{stype}: ma_short ({ma_s}) >= ma_long ({ma_l})")

        return warnings

    def _check_param_bounds(self, stype: str, params: Dict[str, Any]) -> List[str]:
        """检查单个策略的参数是否在合理范围内"""
        warnings = []
        bounds = self.PARAM_BOUNDS.get(stype, {})

        for key, (lo, hi) in bounds.items():
            value = params.get(key)
            if value is None:
                continue
            try:
                v = float(value)
                if v < lo:
                    warnings.append(f"{stype}.{key}={v} < 下限 {lo}")
                elif v > hi:
                    warnings.append(f"{stype}.{key}={v} > 上限 {hi}")
            except (TypeError, ValueError):
                pass

        return warnings

    def register_strategy(self, stype: str, cls: Type[BaseStrategy]) -> None:
        """注册新策略类型"""
        self.class_map[stype] = cls