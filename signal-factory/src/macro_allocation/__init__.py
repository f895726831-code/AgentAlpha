# SPDX-License-Identifier: MIT
# macro_allocation — Layer 0: 宏观大类资产配置
#
# 11 类资产：石油ETF、黄金ETF、债券ETF、纳指ETF、恒生科技、A股、现金/回购、
#           科创50、日经225、印度SENSEX、转债ETF
# 方法论：规则框架 + ML 增强 + STABLE 条件 BL
#
# 模块：
#   macro_data         — 宏观 EDB 数据采集 + ETF/指数数据采集
#   macro_regime       — 六象限宏观环境判断
#   asset_scorer       — 11 类资产多因子评分
#   portfolio_builder  — 风险预算 + 主动观点组合构建
#   macro_backtest     — 大类资产配置回测
#   macro_contract     — JSON 契约定义
#   asset_returns      — [NEW] 11 类资产历史收益计算
#   regime_backtest    — [NEW] STABLE 4-Regime 历史分类
#   conditional_stats  — [NEW] 条件收益分布统计
#   stable_bl          — [NEW] 条件 Black-Litterman
#   mvo                — [NEW] 约束均值方差优化
#   backtest           — [NEW] STABLE 周度再平衡回测
#   integration        — [NEW] STABLE → macro-allocator 集成

from src.macro_allocation.macro_data import (
    MacroDataCollector,
    AssetUniverse,
    ASSET_UNIVERSE,
    PROVIDER,
    get_macro_collector,
)

from src.macro_allocation.macro_regime import (
    Direction,
    DimensionJudgment,
    MacroRegime,
    MacroRegimeClassifier,
    MacroRegimeResult,
    RegimeClassifier,
    RegimeScores,
    REGIME_MAP,
    REGIME_ASSET_RANKING,
    REGIME_LABELS,
    REGIME_LABELS_EN,
    classify_from_dict,
    get_regime_classifier,
    is_risk_off,
    load_regime_classifier,
)

from src.macro_allocation.asset_scorer import (
    AssetScorer,
    AssetScore,
    AssetScoreResult,
    FactorScore,
    ScoreBreakdown,
    SCORING_WEIGHTS,
    ASSET_DEFINITIONS,
    CATEGORY_WEIGHT_OVERRIDES,
    DEFAULT_WEIGHTS,
    RECOMMENDATION_THRESHOLDS,
    get_asset_scorer,
)

from src.macro_allocation.portfolio_builder import (
    AllocationMode,
    AllocationResult,
    AllocationConstraints,
    AssetWeight,
    CONSTRAINT_MAP,
    CONSTRAINTS_NORMAL,
    CONSTRAINTS_RISK_OFF,
    CONSTRAINTS_EARNINGS_SEASON,
    DEFAULT_CONSTRAINTS,
    MODE_CONSTRAINTS,
    PortfolioBuilder,
    RebalancePlan,
    get_portfolio_builder,
)

from src.macro_allocation.macro_backtest import (
    MacroBacktester,
    BacktestResult,
    get_macro_backtester,
)

from src.macro_allocation.macro_contract import (
    MacroAllocationSignal,
    AssetAllocation,
    validate_signal,
    signal_to_json,
    json_to_signal,
)

from src.macro_allocation.asset_returns import (
    AssetReturns,
    AssetStats,
    ASSET_NAMES as ASSET_NAMES_11,
    get_asset_returns,
)

from src.macro_allocation.regime_backtest import (
    StableRegimeClassifier,
    RegimeHistory,
    RegimePeriod,
    STABLE_REGIMES,
    REGIME_LABELS_CN,
    get_regime_classifier as get_stable_regime_classifier,
)

from src.macro_allocation.conditional_stats import (
    ConditionalStats,
    ConditionalStatsResult,
    RegimeConditionalStats,
    get_conditional_stats,
)

from src.macro_allocation.stable_bl import (
    StableBL,
    BLView,
    BLResult,
    get_stable_bl,
)

from src.macro_allocation.mvo import (
    MVO,
    MVOConstraints,
    MVOResult,
    ASSET_CATEGORIES,
    get_mvo,
)

from src.macro_allocation.backtest import (
    STABLEBacktest,
    BaselineBacktest,
    BacktestResult,
    compare_strategies,
)

from src.macro_allocation.integration import (
    STABLEIntegration,
    StableAllocation,
    get_integration,
)

__all__ = [
    # macro_data
    "MacroDataCollector",
    "AssetUniverse",
    "ASSET_UNIVERSE",
    "PROVIDER",
    "get_macro_collector",
    # macro_regime
    "Direction",
    "DimensionJudgment",
    "MacroRegime",
    "MacroRegimeClassifier",
    "MacroRegimeResult",
    "RegimeClassifier",
    "RegimeScores",
    "REGIME_MAP",
    "REGIME_ASSET_RANKING",
    "REGIME_LABELS",
    "REGIME_LABELS_EN",
    "classify_from_dict",
    "get_regime_classifier",
    "is_risk_off",
    "load_regime_classifier",
    # asset_scorer
    "AssetScorer",
    "AssetScore",
    "AssetScoreResult",
    "FactorScore",
    "ScoreBreakdown",
    "SCORING_WEIGHTS",
    "ASSET_DEFINITIONS",
    "CATEGORY_WEIGHT_OVERRIDES",
    "DEFAULT_WEIGHTS",
    "RECOMMENDATION_THRESHOLDS",
    "get_asset_scorer",
    # portfolio_builder
    "AllocationMode",
    "AllocationConstraints",
    "AllocationResult",
    "AssetWeight",
    "CONSTRAINT_MAP",
    "CONSTRAINTS_NORMAL",
    "CONSTRAINTS_RISK_OFF",
    "CONSTRAINTS_EARNINGS_SEASON",
    "DEFAULT_CONSTRAINTS",
    "MODE_CONSTRAINTS",
    "PortfolioBuilder",
    "RebalancePlan",
    "get_portfolio_builder",
    # macro_backtest
    "MacroBacktester",
    "BacktestResult",
    "get_macro_backtester",
    # macro_contract
    "MacroAllocationSignal",
    "AssetAllocation",
    "validate_signal",
    "signal_to_json",
    "json_to_signal",
    # ── STABLE v1.0 ──
    # asset_returns
    "AssetReturns",
    "AssetStats",
    "ASSET_NAMES_11",
    "get_asset_returns",
    # regime_backtest
    "StableRegimeClassifier",
    "RegimeHistory",
    "RegimePeriod",
    "STABLE_REGIMES",
    "REGIME_LABELS_CN",
    "get_stable_regime_classifier",
    # conditional_stats
    "ConditionalStats",
    "ConditionalStatsResult",
    "RegimeConditionalStats",
    "get_conditional_stats",
    # stable_bl
    "StableBL",
    "BLView",
    "BLResult",
    "get_stable_bl",
    # mvo
    "MVO",
    "MVOConstraints",
    "MVOResult",
    "ASSET_CATEGORIES",
    "get_mvo",
    # backtest
    "STABLEBacktest",
    "BaselineBacktest",
    "compare_strategies",
    # integration
    "STABLEIntegration",
    "StableAllocation",
    "get_integration",
]