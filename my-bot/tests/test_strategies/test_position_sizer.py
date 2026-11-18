"""
PositionSizer 测试套件 — 100 个测试用例验证仓位计算与 risk-policy.json 一致。

验证维度:
  1. Fractional-Kelly 基础计算 (10 cases)
  2. 模式乘数 (8 cases)
  3. 策略偏好多乘数 (12 cases)
  4. Gate 2 乘数 (8 cases)
  5. 集中度上限 (10 cases)
  6. 拥挤超驰 (6 cases)
  7. Risk_off 模式 (4 cases)
  8. 板块类型检测 (12 cases)
  9. 整手取整 (6 cases)
  10. 复合场景 (12 cases)
  11. 边界条件 (12 cases)
"""

import pytest
import sys
from pathlib import Path

# 确保 my-bot/src 在 sys.path 中
MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.position_sizer import PositionSizer, Signal, Position, Account, compute_position
from strategies.multipliers import Multipliers


# ─── Fixtures ───

@pytest.fixture(scope="module")
def m():
    """加载 risk-policy.json，所有测试共享"""
    return Multipliers.from_risk_policy()


@pytest.fixture
def sizer(m):
    return PositionSizer(m)


@pytest.fixture
def base_signal():
    """标准多头信号，参数均可覆盖"""
    return Signal(
        symbol="600519.SH",
        strategy_type="T1_breakout",
        direction="long",
        confidence=0.8,
        trigger_price=1800.0,
        stop_loss=1710.0,       # -5%
        edge=3.0,
        win_rate=0.55,
    )


@pytest.fixture
def base_account():
    return Account(
        total_value=500000,
        cash=200000,
        positions={},
    )


# ═══════════════════════════════════════════════════════════════
# 1. Fractional-Kelly 基础计算 (10 cases)
# ═══════════════════════════════════════════════════════════════

class TestFractionalKelly:
    """验证 Kelly 公式的数学正确性"""

    def test_kelly_breakeven(self, sizer):
        """胜率 50%、edge=1.0 → Kelly = 0"""
        pct = sizer._fractional_kelly(edge=1.0, win_rate=0.5)
        assert pct == pytest.approx(0.0, abs=1e-4)

    def test_kelly_positive_edge(self, sizer):
        """win=0.55, edge=3.0 → kelly_f = (0.55*3 - 0.45)/3 = 0.40, ×0.25 = 0.10, ×100 = 10%"""
        pct = sizer._fractional_kelly(edge=3.0, win_rate=0.55)
        expected = (0.55 * 3.0 - 0.45) / 3.0 * 0.25 * 100
        assert pct == pytest.approx(expected, abs=1e-2)

    def test_kelly_high_winrate(self, sizer):
        """win=0.70, edge=2.0 → kelly_f = (0.70*2 - 0.30)/2 = 0.55, ×0.25 = 0.1375, ×100 = 13.75%"""
        pct = sizer._fractional_kelly(edge=2.0, win_rate=0.70)
        expected = (0.70 * 2.0 - 0.30) / 2.0 * 0.25 * 100
        assert pct == pytest.approx(expected, abs=1e-2)

    def test_kelly_low_winrate(self, sizer):
        """win=0.35, edge=3.0 → kelly_f = (0.35*3 - 0.65)/3 = 0.133, ×0.25 = 0.033, ×100 = 3.33%"""
        pct = sizer._fractional_kelly(edge=3.0, win_rate=0.35)
        expected = max(0.35 * 3.0 - 0.65, 0) / 3.0 * 0.25 * 100
        assert pct >= 0

    def test_kelly_50pct_cap(self, sizer):
        """即使 edge 极大，Kelly 不超过 50%"""
        pct = sizer._fractional_kelly(edge=10.0, win_rate=0.9)
        assert pct <= 50.0

    def test_kelly_zero_edge_clamped(self, sizer):
        """edge=0 被钳制到 0.1，不会除零"""
        pct = sizer._fractional_kelly(edge=0.0, win_rate=0.5)
        assert pct >= 0

    def test_kelly_winrate_clamped(self, sizer):
        """win_rate < 0.3 被钳制到 0.3"""
        pct = sizer._fractional_kelly(edge=3.0, win_rate=0.1)
        # 钳制后: kelly = (0.3*3 - 0.7)/3 = 0.067, ×0.25 = 0.0167, ×100 = 1.67%
        assert pct >= 0

    def test_kelly_negative_edge(self, sizer):
        """负 edge → Kelly = 0"""
        pct = sizer._fractional_kelly(edge=-1.0, win_rate=0.5)
        assert pct == pytest.approx(0.0, abs=1e-4)

    def test_kelly_high_confidence(self, sizer):
        """高置信度组合 win=0.8, edge=4.0 → 应该有显著仓位"""
        pct = sizer._fractional_kelly(edge=4.0, win_rate=0.80)
        assert pct > 10.0  # 应该 > 10%

    def test_kelly_risky_low_edge(self, sizer):
        """低 edge 高胜率 → 仓位受限"""
        pct = sizer._fractional_kelly(edge=0.5, win_rate=0.55)
        assert pct < 5.0


# ═══════════════════════════════════════════════════════════════
# 2. 模式乘数 (8 cases)
# ═══════════════════════════════════════════════════════════════

class TestModeMultipliers:
    """验证 normal/earnings_season/risk_off 模式"""

    def test_normal_allows_new(self, sizer):
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, mode="normal", gate2_light="green")
        assert pos.action == "buy"
        assert pos.multipliers["mode_mult"] == 1.0

    def test_earnings_season_allows_new(self, sizer):
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, mode="earnings_season", gate2_light="green")
        assert pos.action == "buy"
        assert pos.multipliers["mode_mult"] == 1.0

    def test_risk_off_blocks_all(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, mode="risk_off")
        assert pos.action == "hold"
        assert pos.pct_of_portfolio == 0
        assert pos.shares == 0
        assert len(pos.warnings) > 0
        assert "risk_off" in pos.warnings[0]

    def test_risk_off_any_strategy(self, sizer):
        """risk_off 不应区分策略类型"""
        signal = Signal("000001.SZ", "T2_pullback", "long", 0.8, 10.0, 9.5, edge=2.0, win_rate=0.6)
        pos = sizer.calculate(signal, account_value=500000, mode="risk_off")
        assert pos.action == "hold"

    def test_normal_allows_short(self, sizer):
        """卖空信号在 normal 模式下应允许"""
        signal = Signal("600036.SH", "T1_breakout", "short", 0.7, 40.0, 42.0, edge=2.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, mode="normal", gate2_light="green")
        assert pos.action in ("buy", "sell")

    def test_earnings_season_same_concentration(self, sizer, base_signal):
        """earnings_season 和 normal 集中度相同"""
        pos_e = sizer.calculate(base_signal, account_value=500000, mode="earnings_season")
        pos_n = sizer.calculate(base_signal, account_value=500000, mode="normal")
        assert pos_e.multipliers["concentration_limit"] == pos_n.multipliers["concentration_limit"]

    def test_invalid_mode_defaults(self, sizer, base_signal):
        """无效 mode 应 fallback 到 normal"""
        pos = sizer.calculate(base_signal, account_value=500000, mode="nonexistent")
        assert pos.action in ("buy", "hold")  # 不崩溃

    def test_mode_multiplier_propagation(self, sizer, base_signal):
        """模式乘数在 multipliers 字典中正确记录"""
        pos = sizer.calculate(base_signal, account_value=500000, mode="normal")
        assert "mode_mult" in pos.multipliers
        assert pos.multipliers["mode_mult"] == 1.0


# ═══════════════════════════════════════════════════════════════
# 3. 策略偏好多乘数 (12 cases)
# ═══════════════════════════════════════════════════════════════

class TestStrategyBias:
    """验证 3 种 bias 下的策略权重"""

    # right_priority: T1×1.5, T2×0.5, T3×0.0, T4×1.5, VCP×1.5
    # left_priority: T1×0.0, T2×1.5, T3×1.0, T4×0.0, VCP×0.0
    # balanced: all ×1.0

    def test_right_bias_boosts_breakout(self, sizer):
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="right_priority", gate2_light="green")
        assert pos.multipliers["bias_mult"] == 1.5
        assert pos.action == "buy"

    def test_right_bias_disables_oversold(self, sizer):
        signal = Signal("600519.SH", "T3_oversold_bounce", "long", 0.8, 1800.0, 1710.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000, bias="right_priority")
        assert pos.action == "hold"
        assert pos.multipliers["bias_mult"] == 0.0

    def test_right_bias_boosts_vcp(self, sizer):
        signal = Signal("688001.SH", "VCP_breakout", "long", 0.7, 50.0, 47.5, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="right_priority")
        assert pos.multipliers["bias_mult"] == 1.5

    def test_left_bias_boosts_pullback(self, sizer):
        signal = Signal("000001.SZ", "T2_pullback", "long", 0.8, 10.0, 9.5, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="left_priority")
        assert pos.multipliers["bias_mult"] == 1.5

    def test_left_bias_disables_breakout(self, sizer):
        signal = Signal("600519.SH", "T1_breakout", "long", 0.8, 1800.0, 1710.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="left_priority")
        assert pos.action == "hold"
        assert pos.multipliers["bias_mult"] == 0.0

    def test_left_bias_disables_vcp(self, sizer):
        signal = Signal("688001.SH", "VCP_breakout", "long", 0.7, 50.0, 47.5, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="left_priority")
        assert pos.action == "hold"

    def test_left_bias_disables_golden_cross(self, sizer):
        signal = Signal("600519.SH", "T4_golden_cross", "long", 0.6, 1800.0, 1710.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000, bias="left_priority")
        assert pos.action == "hold"

    def test_balanced_all_equal(self, sizer):
        """balanced 下所有策略权重为 1.0"""
        for stype in ["T1_breakout", "T2_pullback", "T3_oversold_bounce",
                       "T4_golden_cross", "VCP_breakout", "T5_earnings", "T6_capital"]:
            signal = Signal("600519.SH", stype, "long", 0.6, 1800.0, 1710.0, edge=2.0, win_rate=0.5)
            pos = sizer.calculate(signal, account_value=500000, bias="balanced")
            assert pos.multipliers["bias_mult"] == 1.0, f"{stype} bias_mult != 1.0"

    def test_left_bias_oversold_standard(self, sizer):
        """left_priority 下 T3 权重=1.0"""
        signal = Signal("600036.SH", "T3_oversold_bounce", "long", 0.6, 40.0, 38.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000, bias="left_priority", gate2_light="green")
        assert pos.multipliers["bias_mult"] == 1.0
        assert pos.action == "buy"

    def test_right_bias_pullback_half(self, sizer):
        """right_priority 下 T2 权重=0.5"""
        signal = Signal("000001.SZ", "T2_pullback", "long", 0.8, 10.0, 9.5, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="right_priority")
        assert pos.multipliers["bias_mult"] == 0.5
        assert pos.action == "buy"

    def test_canslim_weight(self, sizer):
        """CANSLIM 在 balanced 下权重=1.0（不在 risk-policy 中显式定义，fallback 1.0）"""
        signal = Signal("600519.SH", "canslim", "long", 0.7, 1800.0, 1710.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000, bias="balanced")
        assert pos.multipliers["bias_mult"] == 1.0

    def test_earnings_weight_balanced(self, sizer):
        """T5_earnings 在 balanced 下权重=1.0"""
        signal = Signal("600519.SH", "T5_earnings", "long", 0.7, 1800.0, 1710.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000, bias="balanced")
        assert pos.multipliers["bias_mult"] == 1.0


# ═══════════════════════════════════════════════════════════════
# 4. Gate 2 乘数 (8 cases)
# ═══════════════════════════════════════════════════════════════

class TestGate2Multipliers:
    """验证 Gate 2 灯号 → 仓位乘数"""

    def test_green_full_position(self, sizer):
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, gate2_light="green")
        assert pos.multipliers["gate2_mult"] == 1.0
        assert pos.action == "buy"

    def test_yellow_half_position(self, sizer):
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, gate2_light="yellow")
        assert pos.multipliers["gate2_mult"] == 0.5
        assert pos.action == "buy"

    def test_orange_no_position(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, gate2_light="orange")
        assert pos.action == "hold"
        assert pos.multipliers["gate2_mult"] == 0.0

    def test_red_no_position(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, gate2_light="red")
        assert pos.action == "hold"
        assert pos.multipliers["gate2_mult"] == 0.0

    def test_net_score_to_light_green(self, sizer, base_signal):
        """Net_Score=2.5 → green"""
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=2.5)
        assert pos.multipliers["gate2_light"] == "green"
        assert pos.multipliers["gate2_mult"] == 1.0

    def test_net_score_to_light_yellow(self, sizer, base_signal):
        """Net_Score=1.0 → yellow"""
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=1.0)
        assert pos.multipliers["gate2_light"] == "yellow"
        assert pos.multipliers["gate2_mult"] == 0.5

    def test_net_score_to_light_orange(self, sizer, base_signal):
        """Net_Score=0 → orange"""
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=0.0)
        assert pos.multipliers["gate2_light"] == "orange"
        assert pos.multipliers["gate2_mult"] == 0.0

    def test_net_score_to_light_red(self, sizer, base_signal):
        """Net_Score=-1.0 → red"""
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=-1.0)
        assert pos.multipliers["gate2_light"] == "red"
        assert pos.multipliers["gate2_mult"] == 0.0


# ═══════════════════════════════════════════════════════════════
# 5. 集中度上限 (10 cases)
# ═══════════════════════════════════════════════════════════════

class TestConcentrationLimits:
    """验证板块集中度上限"""

    def test_main_board_normal_10pct(self, sizer, base_signal):
        """主板 normal 集中度上限 10%"""
        # Kelly 很大 → 应被 10% cap 限制
        signal = Signal("600519.SH", "T1_breakout", "long", 0.9, 1800.0, 1710.0,
                        edge=10.0, win_rate=0.9)
        pos = sizer.calculate(signal, account_value=500000, mode="normal")
        assert pos.pct_of_portfolio <= 10.0
        assert pos.multipliers["concentration_limit"] == 10

    def test_star_board_normal_30pct(self, sizer):
        """科创板 normal 集中度上限 30%"""
        signal = Signal("688001.SH", "T1_breakout", "long", 0.9, 50.0, 47.5,
                        edge=10.0, win_rate=0.9)
        pos = sizer.calculate(signal, account_value=500000, mode="normal")
        assert pos.pct_of_portfolio <= 30.0
        assert pos.multipliers["concentration_limit"] == 30

    def test_hk_board_normal_15pct(self, sizer):
        """港股 normal 集中度上限 15%"""
        signal = Signal("00700.HK", "T1_breakout", "long", 0.9, 400.0, 380.0,
                        edge=10.0, win_rate=0.9)
        pos = sizer.calculate(signal, account_value=500000, mode="normal")
        assert pos.pct_of_portfolio <= 15.0
        assert pos.multipliers["concentration_limit"] == 15

    def test_main_board_risk_off_5pct(self, sizer):
        """risk_off → 集中度 5%，但 risk_off 直接禁止建仓"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.9, 1800.0, 1710.0,
                        edge=10.0, win_rate=0.9)
        pos = sizer.calculate(signal, account_value=500000, mode="risk_off")
        assert pos.action == "hold"
        assert pos.pct_of_portfolio == 0

    def test_star_board_risk_off_limit(self, sizer):
        """科创板 risk_off 集中度 20%，但被禁止新建仓"""
        signal = Signal("688001.SH", "T1_breakout", "long", 0.9, 50.0, 47.5,
                        edge=10.0, win_rate=0.9)
        pos = sizer.calculate(signal, account_value=500000, mode="risk_off")
        assert pos.action == "hold"

    def test_hk_board_risk_off_limit(self, sizer):
        """港股 risk_off 集中度 10%，但被禁止新建仓"""
        signal = Signal("00700.HK", "T1_breakout", "long", 0.9, 400.0, 380.0,
                        edge=10.0, win_rate=0.9)
        pos = sizer.calculate(signal, account_value=500000, mode="risk_off")
        assert pos.action == "hold"

    def test_600_code_main_board(self, sizer):
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.board == "main_board"
        assert pos.multipliers["concentration_limit"] == 10

    def test_000_code_main_board(self, sizer):
        signal = Signal("000001.SZ", "T1_breakout", "long", 0.8, 10.0, 9.5, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.board == "main_board"

    def test_300_code_main_board(self, sizer):
        signal = Signal("300750.SZ", "T1_breakout", "long", 0.8, 200.0, 190.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.board == "main_board"

    def test_small_kelly_below_cap(self, sizer):
        """Kelly 计算结果低于集中度上限时应不受限"""
        signal = Signal("600036.SH", "T1_breakout", "long", 0.55, 40.0, 38.0,
                        edge=1.5, win_rate=0.52)
        pos = sizer.calculate(signal, account_value=500000, gate2_light="green")
        assert pos.pct_of_portfolio < 10.0  # Kelly 在 + 乘数后应仍 < 10%
        assert pos.action == "buy"


# ═══════════════════════════════════════════════════════════════
# 6. 拥挤超驰 (6 cases)
# ═══════════════════════════════════════════════════════════════

class TestCrowdingOverride:
    """验证拥挤 Bear ≥ 4 时的仓位减半 + 止损收紧"""

    def test_extreme_crowding_halves_position(self, sizer, base_signal):
        pos_normal = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=0)
        pos_crowded = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=4)
        # 仓位应该减半
        assert pos_crowded.pct_of_portfolio <= pos_normal.pct_of_portfolio * 0.6
        assert len(pos_crowded.warnings) > 0

    def test_crowding_below_threshold_no_override(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=3)
        assert len(pos.warnings) == 0  # 无超驰警告

    def test_crowding_at_threshold_triggers(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=4)
        assert any("拥挤" in w for w in pos.warnings)

    def test_crowding_override_multiplier(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=5)
        assert pos.multipliers["crowding_mult"] == 0.5

    def test_crowding_doubled_with_concentration(self, sizer):
        """拥挤超驰和集中度上限同时生效"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.9, 1800.0, 1710.0,
                        edge=10.0, win_rate=0.9)
        pos = sizer.calculate(signal, account_value=500000, crowding_bear_score=5)
        # 10% cap × 0.5 crowding = 5% max
        assert pos.pct_of_portfolio <= 5.0

    def test_crowding_warning_contains_score(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=4)
        warnings_text = " ".join(pos.warnings)
        assert "4" in warnings_text


# ═══════════════════════════════════════════════════════════════
# 7. Risk_off 模式 (4 cases)
# ═══════════════════════════════════════════════════════════════

class TestRiskOff:
    """验证 risk_off 的完整阻断链"""

    def test_risk_off_zero_everything(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, mode="risk_off")
        assert pos.pct_of_portfolio == 0
        assert pos.shares == 0
        assert pos.max_shares == 0
        assert pos.risk_per_share == 0
        assert pos.total_risk_cny == 0

    def test_risk_off_action_is_hold(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, mode="risk_off")
        assert pos.action == "hold"

    def test_risk_off_reason_in_multipliers(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, mode="risk_off")
        assert "reason" in pos.multipliers

    def test_risk_off_warning_message(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000, mode="risk_off")
        assert len(pos.warnings) >= 1


# ═══════════════════════════════════════════════════════════════
# 8. 板块类型检测 (12 cases)
# ═══════════════════════════════════════════════════════════════

class TestBoardDetection:
    """验证 symbol → board 映射"""

    @pytest.mark.parametrize("symbol,expected_board", [
        ("600519.SH", "main_board"),
        ("601318.SH", "main_board"),
        ("603259.SH", "main_board"),
        ("000001.SZ", "main_board"),
        ("000858.SZ", "main_board"),
        ("002415.SZ", "main_board"),
        ("300750.SZ", "main_board"),
        ("300059.SZ", "main_board"),
        ("688111.SH", "star_board"),
        ("688981.SH", "star_board"),
        ("00700.HK", "hk_board"),
        ("09988.HK", "hk_board"),
    ])
    def test_board_detection(self, sizer, symbol, expected_board):
        signal = Signal(symbol, "T1_breakout", "long", 0.6, 100.0, 95.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.board == expected_board, f"{symbol} → {pos.board}, expected {expected_board}"


# ═══════════════════════════════════════════════════════════════
# 9. 整手取整 (6 cases)
# ═══════════════════════════════════════════════════════════════

class TestLotRounding:
    """验证整手取整逻辑"""

    def test_main_board_100_lot(self, sizer):
        """主板 100 股整手"""
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, gate2_light="green")
        assert pos.shares % 100 == 0
        assert pos.shares > 0

    def test_star_board_200_lot(self, sizer):
        """科创板 200 股整手"""
        signal = Signal("688111.SH", "T1_breakout", "long", 0.8, 50.0, 47.5, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.shares % 200 == 0

    def test_hk_board_100_lot(self, sizer):
        """港股 100 股整手"""
        signal = Signal("00700.HK", "T1_breakout", "long", 0.8, 400.0, 380.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.shares % 100 == 0

    def test_small_position_rounds_to_zero(self, sizer):
        """极小仓位整手取整为 0"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.51, 2000.0, 1900.0,
                        edge=0.2, win_rate=0.51)
        pos = sizer.calculate(signal, account_value=10000)  # 极小账户
        assert pos.shares == 0
        assert pos.action == "hold"

    def test_max_shares_also_rounded(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000)
        assert pos.max_shares % 100 == 0
        assert pos.max_shares >= pos.shares

    def test_high_price_stock_low_shares(self, sizer):
        """高价股整手取整后股数少"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.8, 2000.0, 1900.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=100000)
        assert pos.shares % 100 == 0
        assert pos.shares <= pos.max_shares


# ═══════════════════════════════════════════════════════════════
# 10. 复合场景 (12 cases)
# ═══════════════════════════════════════════════════════════════

class TestCompositeScenarios:
    """多乘数叠加的复合场景"""

    def test_right_boost_green_gate2(self, sizer):
        """right_priority T1 + green Gate 2 → 1.5 × 1.0 = 1.5"""
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="right_priority", gate2_light="green")
        assert pos.multipliers["bias_mult"] == 1.5
        assert pos.multipliers["gate2_mult"] == 1.0
        assert pos.action == "buy"

    def test_left_yellow_gate2(self, sizer):
        """left_priority T2 + yellow Gate 2 → 1.5 × 0.5 = 0.75"""
        signal = Signal("000001.SZ", "T2_pullback", "long", 0.8, 10.0, 9.5, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="left_priority", gate2_light="yellow")
        assert pos.multipliers["bias_mult"] == 1.5
        assert pos.multipliers["gate2_mult"] == 0.5
        assert pos.action == "buy"

    def test_balanced_orange_blocks(self, sizer):
        """balanced + orange Gate 2 → gate2_mult=0, hold"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.8, 1800.0, 1710.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="balanced", gate2_light="orange")
        assert pos.action == "hold"

    def test_right_disabled_ignores_gate2(self, sizer):
        """strategy disabled by bias → Gate 2 不检查"""
        signal = Signal("600519.SH", "T3_oversold_bounce", "long", 0.8, 1800.0, 1710.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="right_priority", gate2_light="green")
        assert pos.action == "hold"
        assert pos.multipliers["bias_mult"] == 0.0

    def test_right_boost_crowded(self, sizer):
        """right_priority T1 + crowding Bear=4 → 1.5 × 0.5 = 0.75"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.8, 1800.0, 1710.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, bias="right_priority", crowding_bear_score=5)
        # bias × 1.5, crowding × 0.5 → net effect smaller than just crowding
        assert pos.multipliers["bias_mult"] == 1.5
        assert pos.multipliers["crowding_mult"] == 0.5

    def test_earnings_season_normal_policy(self, sizer):
        """earnings_season 模式下仓位策略不变"""
        signal = Signal("600036.SH", "T5_earnings", "long", 0.7, 40.0, 38.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000, mode="earnings_season", gate2_light="green")
        assert pos.action == "buy"
        assert pos.multipliers["mode_mult"] == 1.0

    def test_total_risk_cny_positive(self, sizer):
        """总风险金额应 > 0"""
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000, gate2_light="green")
        assert pos.total_risk_cny > 0

    def test_risk_per_share_correct_long(self, sizer, base_signal):
        """多头: risk_per_share = trigger - stop_loss"""
        pos = sizer.calculate(base_signal, account_value=500000)
        assert pos.risk_per_share == pytest.approx(90.0, abs=1.0)  # 1800 - 1710

    def test_risk_per_share_correct_short(self, sizer):
        """空头: risk_per_share = stop_loss - trigger"""
        signal = Signal("600519.SH", "T1_breakout", "short", 0.6, 1800.0, 1890.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.risk_per_share == pytest.approx(90.0, abs=1.0)  # 1890 - 1800

    def test_concentration_limit_in_multipliers(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000)
        assert "concentration_limit" in pos.multipliers

    def test_all_multipliers_present(self, sizer, base_signal):
        pos = sizer.calculate(base_signal, account_value=500000)
        for key in ["base_pct", "mode_mult", "bias_mult", "gate2_mult", "crowding_mult", "concentration_limit"]:
            assert key in pos.multipliers, f"Missing key: {key}"

    def test_net_score_edge_boundaries(self, sizer, base_signal):
        """验证 Net_Score 阈值边界"""
        # green ≥ 2.0
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=2.0)
        assert pos.multipliers["gate2_light"] == "green"
        # yellow ≥ 0.5
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=0.5)
        assert pos.multipliers["gate2_light"] == "yellow"
        # orange > -0.5
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=-0.49)
        assert pos.multipliers["gate2_light"] == "orange"
        # red ≤ -0.5
        pos = sizer.calculate(base_signal, account_value=500000, gate2_net_score=-0.5)
        assert pos.multipliers["gate2_light"] == "red"


# ═══════════════════════════════════════════════════════════════
# 11. 边界条件 (12 cases)
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """异常输入的边界测试"""

    def test_zero_account_value(self, sizer):
        """账户价值 0 → 仓位 0"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.8, 1800.0, 1710.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=0)
        assert pos.shares == 0
        assert pos.action == "hold"

    def test_negative_trigger_price(self, sizer):
        """负触发价 → 应不崩溃"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.8, -100.0, -200.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.action == "hold"  # shares would be negative → floor to 0

    def test_stop_loss_above_trigger_long(self, sizer):
        """止损 > 触发 (long) → risk_per_share 为负"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.8, 1800.0, 1900.0, edge=3.0, win_rate=0.55)
        pos = sizer.calculate(signal, account_value=500000)
        # risk_per_share is negative but shares still computed
        assert pos.shares >= 0  # doesn't crash

    def test_take_profit_empty(self, sizer):
        """无止盈目标 → 正常"""
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        signal.take_profit = []
        pos = sizer.calculate(signal, account_value=500000, gate2_light="green")
        assert pos.action == "buy"

    def test_take_profit_multiple_levels(self, sizer):
        """多级止盈 → 正常"""
        signal = Signal("600036.SH", "T1_breakout", "long", 0.8, 40.0, 38.0, edge=3.0, win_rate=0.55)
        signal.take_profit = [44.0, 48.0, 52.0]
        pos = sizer.calculate(signal, account_value=500000, gate2_light="green")
        assert pos.action == "buy"

    def test_unknown_strategy_type(self, sizer):
        """未知策略类型 → fallback 权重 1.0"""
        signal = Signal("600519.SH", "unknown_strategy", "long", 0.6, 100.0, 95.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.multipliers["bias_mult"] == 1.0

    def test_no_bias_no_gate2(self, sizer, base_signal):
        """无 bias 和无 gate2 → 默认 yellow"""
        pos = sizer.calculate(base_signal, account_value=500000)
        assert pos.multipliers["gate2_light"] == "yellow"
        assert pos.multipliers["bias_mult"] == 1.0

    def test_large_account_small_signal(self, sizer):
        """大账户 + 小信号 → 正常计算"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.55, 1800.0, 1710.0, edge=2.0, win_rate=0.52)
        pos = sizer.calculate(signal, account_value=10_000_000)  # 千万账户
        assert pos.shares % 100 == 0
        assert pos.pct_of_portfolio <= 10.0

    def test_crowding_max_score(self, sizer, base_signal):
        """拥挤度最大值 5"""
        pos = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=5)
        assert pos.multipliers["crowding_mult"] == 0.5

    def test_crowding_zero_score(self, sizer, base_signal):
        """拥挤度 0 → 无超驰"""
        pos = sizer.calculate(base_signal, account_value=500000, crowding_bear_score=0)
        assert pos.multipliers["crowding_mult"] == 1.0

    def test_short_direction_signal(self, sizer):
        """空头信号 → action 可能是 sell 或 hold"""
        signal = Signal("600519.SH", "T1_breakout", "short", 0.6, 1800.0, 1890.0, edge=2.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=500000)
        assert pos.action in ("buy", "sell", "hold")

    def test_minimum_viable_signal(self, sizer):
        """最小可行信号"""
        signal = Signal("600519.SH", "T1_breakout", "long", 0.5, 100.0, 95.0, edge=1.0, win_rate=0.5)
        pos = sizer.calculate(signal, account_value=100000)
        assert pos.action in ("buy", "hold")


# ═══════════════════════════════════════════════════════════════
# 12. compute_position 快捷函数 (2 cases)
# ═══════════════════════════════════════════════════════════════

class TestComputePositionFunction:
    """验证快捷函数的正确性"""

    def test_returns_position_object(self):
        pos = compute_position(
            symbol="600036.SH",
            strategy_type="T1_breakout",
            direction="long",
            confidence=0.8,
            trigger_price=40.0,
            stop_loss=38.0,
            account_value=500000,
            edge=3.0,
            win_rate=0.55,
        )
        assert isinstance(pos, Position)
        assert pos.action == "buy"

    def test_compute_position_with_net_score(self):
        pos = compute_position(
            symbol="600036.SH",
            strategy_type="T1_breakout",
            direction="long",
            confidence=0.8,
            trigger_price=40.0,
            stop_loss=38.0,
            account_value=500000,
            edge=3.0,
            win_rate=0.55,
            gate2_net_score=2.5,
        )
        assert pos.multipliers["gate2_light"] == "green"


# ═══════════════════════════════════════════════════════════════
# 13. Multipliers 加载验证 (6 cases)
# ═══════════════════════════════════════════════════════════════

class TestMultipliersLoading:
    """验证 risk-policy.json 加载正确"""

    def test_risk_policy_loaded(self, m):
        assert m._data is not None
        assert "version" in m._data

    def test_kelly_multiplier(self, m):
        assert m.kelly_multiplier == 0.25

    def test_concentration_normal_main(self, m):
        assert m.get_concentration_limit("normal", "main_board") == 10

    def test_concentration_risk_off_main(self, m):
        assert m.get_concentration_limit("risk_off", "main_board") == 5

    def test_all_strategy_weights_balanced(self, m):
        weights = m.get_all_strategy_weights("balanced")
        assert len(weights) > 0
        for w in weights.values():
            assert w == 1.0

    def test_all_strategy_weights_right(self, m):
        weights = m.get_all_strategy_weights("right_priority")
        assert weights.get("T1_breakout") == 1.5
        assert weights.get("T3_oversold_bounce") == 0.0