# SPDX-License-Identifier: MIT
# sector_rotation — Layer 1: 板块轮动 ML
#
# 31 个申万一级行业板块的 ML 预测模型。
# 方法论：两阶段 ML（先板块 → 后个股），GBDT/XGBoost 模型。
#
# 模块：
#   sector_data       — akshare → CSV 数据采集，SectorDataHandler
#   sector_features   — ~50 个特征工程，PIT 安全，截面标准化
#   sector_model      — SectorModelGenerator，训练/预测/集成
#   sector_aggregator — B 方案基准线：个股 alpha 聚合到板块
#   sector_backtest   — IC 分析，做多 Top-5 模拟，滚动窗口
#   sector_contract   — AgentAlpha 板块信号 JSON 契约

from src.sector_rotation.sector_data import (
    SectorDataCollector,
    SectorDataHandler,
    SW_SECTOR_CODES,
    get_sector_collector,
)

from src.sector_rotation.sector_features import (
    SectorFeatureEngine,
    FeatureConfig,
    SECTOR_FEATURE_GROUPS,
    get_sector_feature_engine,
)

from src.sector_rotation.sector_model import (
    SectorModelGenerator,
    SectorModelConfig,
    SECTOR_MODEL_CONFIGS,
    get_sector_model_generator,
)

from src.sector_rotation.sector_aggregator import (
    SectorAggregator,
    AggregationMethod,
    get_sector_aggregator,
)

from src.sector_rotation.sector_backtest import (
    SectorBacktester,
    SectorBacktestResult,
    get_sector_backtester,
)

from src.sector_rotation.sector_contract import (
    SectorSignal,
    SectorAllocation,
    SectorAllocationSignal,
    validate_sector_signal,
    sector_signal_to_json,
    json_to_sector_signal,
)

__all__ = [
    # sector_data
    "SectorDataCollector",
    "SectorDataHandler",
    "SW_SECTOR_CODES",
    "get_sector_collector",
    # sector_features
    "SectorFeatureEngine",
    "FeatureConfig",
    "SECTOR_FEATURE_GROUPS",
    "get_sector_feature_engine",
    # sector_model
    "SectorModelGenerator",
    "SectorModelConfig",
    "SECTOR_MODEL_CONFIGS",
    "get_sector_model_generator",
    # sector_aggregator
    "SectorAggregator",
    "AggregationMethod",
    "get_sector_aggregator",
    # sector_backtest
    "SectorBacktester",
    "SectorBacktestResult",
    "get_sector_backtester",
    # sector_contract
    "SectorSignal",
    "SectorAllocation",
    "SectorAllocationSignal",
    "validate_sector_signal",
    "sector_signal_to_json",
    "json_to_sector_signal",
]