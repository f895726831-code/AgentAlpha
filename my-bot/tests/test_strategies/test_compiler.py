"""
StrategyCompiler 单元测试。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.compiler import StrategyCompiler, CompileResult
from strategies.base import BaseStrategy


@pytest.fixture
def compiler():
    return StrategyCompiler()


@pytest.fixture
def valid_llm_json():
    return {
        "regime": "进攻",
        "bias": "right_priority",
        "mode": "normal",
        "strategies": [
            {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30, "volume_ratio_min": 1.5}},
            {"type": "T2_pullback", "active": True, "params": {"trend_period": 60, "ma_period": 20}},
            {"type": "VCP_breakout", "active": True, "params": {"contraction_ratio": 0.5}},
        ],
        "filters": {"min_market_cap": 5e9, "exclude_st": True},
        "meta": {"compiled_by": "deepseek-v3", "timestamp": "2026-10-14T08:00:00"},
    }


# ═══════════════════════════════════════════════════════════════
# 基本编译
# ═══════════════════════════════════════════════════════════════

class TestBasicCompile:
    def test_compiles_valid_json(self, compiler, valid_llm_json):
        result = compiler.compile(valid_llm_json)
        assert result.is_valid
        assert len(result.strategies) == 3
        assert len(result.errors) == 0

    def test_active_strategies_filter(self, compiler):
        json_data = {
            "regime": "震荡",
            "bias": "balanced",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {}},
                {"type": "T2_pullback", "active": False, "params": {}},
                {"type": "T3_oversold_bounce", "active": True, "params": {}},
            ],
        }
        result = compiler.compile(json_data)
        active = result.active_strategies
        assert len(active) == 2
        assert "T1_breakout" in active
        assert "T2_pullback" not in active
        assert "T3_oversold_bounce" in active

    def test_regime_bias_mode_passthrough(self, compiler, valid_llm_json):
        result = compiler.compile(valid_llm_json)
        assert result.regime == "进攻"
        assert result.bias == "right_priority"
        assert result.mode == "normal"

    def test_global_filters_preserved(self, compiler, valid_llm_json):
        result = compiler.compile(valid_llm_json)
        assert result.filters["min_market_cap"] == 5e9
        assert result.filters["exclude_st"] is True


# ═══════════════════════════════════════════════════════════════
# 错误处理
# ═══════════════════════════════════════════════════════════════

class TestErrorHandling:
    def test_empty_strategies_error(self, compiler):
        result = compiler.compile({"strategies": []})
        assert not result.is_valid
        assert len(result.errors) >= 1

    def test_unknown_strategy_type_warning(self, compiler):
        result = compiler.compile({
            "strategies": [{"type": "nonexistent_strategy", "active": True, "params": {}}],
        })
        assert len(result.warnings) >= 1
        assert len(result.strategies) == 0

    def test_no_type_field_warning(self, compiler):
        result = compiler.compile({
            "strategies": [{"active": True, "params": {}}],
        })
        assert len(result.warnings) >= 1

    def test_missing_strategies_key(self, compiler):
        result = compiler.compile({})
        assert not result.is_valid


# ═══════════════════════════════════════════════════════════════
# 参数校验
# ═══════════════════════════════════════════════════════════════

class TestParamValidation:
    def test_param_out_of_bounds_warning(self, compiler):
        result = compiler.compile({
            "strategies": [{"type": "T1_breakout", "active": True, "params": {"lookback_days": 5}}],
        })
        assert len(result.warnings) >= 1  # lookback_days < 10

    def test_param_in_bounds_no_warning(self, compiler):
        result = compiler.compile({
            "strategies": [{"type": "T1_breakout", "active": True, "params": {"lookback_days": 60}}],
        })
        param_warnings = [w for w in result.warnings if "lookback_days" in w]
        assert len(param_warnings) == 0

    def test_stop_loss_too_high_warning(self, compiler):
        result = compiler.compile({
            "strategies": [{"type": "T1_breakout", "active": True, "params": {"stop_loss_pct": 20.0}}],
        })
        assert len(result.warnings) >= 1


# ═══════════════════════════════════════════════════════════════
# Cross-strategy validation
# ═══════════════════════════════════════════════════════════════

class TestValidate:
    def test_incompatible_bias_warning(self, compiler):
        result = compiler.compile({
            "bias": "right_priority",
            "strategies": [
                {"type": "T3_oversold_bounce", "active": True, "params": {}},
            ],
        })
        warnings = compiler.validate(result)
        assert any("T3_oversold_bounce" in w for w in warnings)

    def test_no_active_strategies_warning(self, compiler):
        result = compiler.compile({
            "bias": "balanced",
            "strategies": [
                {"type": "T1_breakout", "active": False, "params": {}},
                {"type": "T2_pullback", "active": False, "params": {}},
            ],
        })
        warnings = compiler.validate(result)
        assert any("无活跃策略" in w for w in warnings)

    def test_pullback_param_consistency(self, compiler):
        result = compiler.compile({
            "strategies": [{
                "type": "T2_pullback", "active": True,
                "params": {"pullback_pct_min": 8.0, "pullback_pct_max": 5.0},
            }],
        })
        warnings = compiler.validate(result)
        assert any("pullback_pct_min" in w for w in warnings)

    def test_golden_cross_ma_consistency(self, compiler):
        result = compiler.compile({
            "strategies": [{
                "type": "T4_golden_cross", "active": True,
                "params": {"ma_short": 60, "ma_long": 20},
            }],
        })
        warnings = compiler.validate(result)
        assert any("ma_short" in w for w in warnings)

    def test_valid_config_no_warnings(self, compiler):
        result = compiler.compile({
            "bias": "right_priority",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
                {"type": "VCP_breakout", "active": True, "params": {"contraction_ratio": 0.5}},
            ],
        })
        warnings = compiler.validate(result)
        assert len(warnings) == 0


# ═══════════════════════════════════════════════════════════════
# 策略注册
# ═══════════════════════════════════════════════════════════════

class TestRegistration:
    def test_register_new_strategy(self, compiler):
        from strategies.breakout import BreakoutStrategy
        compiler.register_strategy("my_breakout", BreakoutStrategy)
        result = compiler.compile({
            "strategies": [{"type": "my_breakout", "active": True, "params": {}}],
        })
        assert result.is_valid
        assert "my_breakout" in result.strategies


# ═══════════════════════════════════════════════════════════════
# 策略实例正确性
# ═══════════════════════════════════════════════════════════════

class TestStrategyInstantiation:
    def test_breakout_params_injected(self, compiler):
        result = compiler.compile({
            "strategies": [{
                "type": "T1_breakout", "active": True,
                "params": {"lookback_days": 45, "volume_ratio_min": 2.0, "stop_loss_pct": 7.0},
            }],
        })
        s = result.strategies["T1_breakout"]
        assert s._get_filter("lookback_days") == 45
        assert s._get_filter("volume_ratio_min") == 2.0
        assert s._get_filter("stop_loss_pct") == 7.0

    def test_mode_bias_on_strategy_params(self, compiler):
        result = compiler.compile({
            "bias": "right_priority",
            "mode": "earnings_season",
            "strategies": [{"type": "T1_breakout", "active": True, "params": {}}],
        })
        s = result.strategies["T1_breakout"]
        assert s.params.bias == "right_priority"
        assert s.params.mode == "earnings_season"

    def test_all_five_strategies_compile(self, compiler):
        """所有 5 种策略类型均可编译"""
        json_data = {
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {}},
                {"type": "T2_pullback", "active": True, "params": {}},
                {"type": "VCP_breakout", "active": True, "params": {}},
                {"type": "T3_oversold_bounce", "active": True, "params": {}},
                {"type": "T4_golden_cross", "active": True, "params": {}},
            ],
        }
        result = compiler.compile(json_data)
        assert result.is_valid
        assert len(result.strategies) == 5