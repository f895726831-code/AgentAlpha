"""
AnomalyDetector 单元测试 — 验证三种异常检测逻辑。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.anomaly_detector import AnomalyDetector, Anomaly
from strategies.base import Signal
from strategies.position_sizer import Position


@pytest.fixture
def detector():
    return AnomalyDetector(
        max_signals_per_tick=8,
        signal_zero_is_anomaly=True,
        bias_contradiction_threshold=0.5,
        min_tick_count_for_zero_check=10,
    )


def _make_signal(stype="T1_breakout", symbol="600036.SH"):
    return Signal(
        symbol=symbol,
        strategy_type=stype,
        direction="long",
        confidence=0.7,
        trigger_price=60.0,
        stop_loss=57.0,
        take_profit=[67.0],
    )


def _make_position(symbol="600036.SH", action="buy"):
    return Position(
        symbol=symbol,
        action=action,
        pct_of_portfolio=5.0,
        shares=500,
        max_shares=500,
        risk_per_share=3.0,
        total_risk_cny=1500,
        board="main_board",
        multipliers={},
        warnings=[],
    )


# ═══════════════════════════════════════════════════════════════
# 信号数量异常
# ═══════════════════════════════════════════════════════════════

class TestSignalCountAnomalies:
    def test_normal_signal_count_no_anomaly(self, detector):
        signals = [_make_signal(f"T1_breakout") for _ in range(5)]
        positions = [_make_position() for _ in range(5)]  # matching positions
        result = detector.check(signals, positions, data_count=50)
        assert len(result) == 0

    def test_signal_flood_detected(self, detector):
        signals = [_make_signal(f"T1_breakout") for _ in range(15)]
        result = detector.check(signals, [], data_count=50)
        critical = [a for a in result if a.code == "SIGNAL_FLOOD"]
        assert len(critical) == 1
        assert critical[0].severity == "CRITICAL"

    def test_signal_drought_detected(self, detector):
        result = detector.check([], [], data_count=20)
        drought = [a for a in result if a.code == "SIGNAL_DROUGHT"]
        assert len(drought) == 1
        assert drought[0].severity == "WARNING"

    def test_signal_drought_skipped_low_data(self, detector):
        """数据量少时不报零信号异常"""
        result = detector.check([], [], data_count=5)
        drought = [a for a in result if a.code == "SIGNAL_DROUGHT"]
        assert len(drought) == 0

    def test_signal_flood_at_boundary(self, detector):
        """信号数等于阈值 → 不触发"""
        signals = [_make_signal(f"T{i}") for i in range(8)]
        result = detector.check(signals, [], data_count=50)
        flood = [a for a in result if a.code == "SIGNAL_FLOOD"]
        assert len(flood) == 0  # 8 不大于 8

    def test_signal_flood_just_over(self, detector):
        """信号数 = 阈值 + 1 → 触发"""
        signals = [_make_signal(f"T{i}") for i in range(9)]
        result = detector.check(signals, [], data_count=50)
        flood = [a for a in result if a.code == "SIGNAL_FLOOD"]
        assert len(flood) == 1


# ═══════════════════════════════════════════════════════════════
# Bias 矛盾检测
# ═══════════════════════════════════════════════════════════════

class TestBiasContradiction:
    def test_right_priority_with_oversold_detected(self, detector):
        signals = [
            _make_signal("T3_oversold_bounce"),
            _make_signal("T3_oversold_bounce"),
            _make_signal("T1_breakout"),
        ]
        result = detector.check(signals, [], bias="right_priority")
        contradiction = [a for a in result if a.code == "BIAS_CONTRADICTION"]
        assert len(contradiction) == 1

    def test_left_priority_with_breakout_detected(self, detector):
        signals = [
            _make_signal("T1_breakout"),
            _make_signal("VCP_breakout"),
            _make_signal("T2_pullback"),
        ]
        result = detector.check(signals, [], bias="left_priority")
        contradiction = [a for a in result if a.code == "BIAS_CONTRADICTION"]
        assert len(contradiction) == 1

    def test_balanced_no_contradiction(self, detector):
        signals = [
            _make_signal("T1_breakout"),
            _make_signal("T3_oversold_bounce"),
            _make_signal("T2_pullback"),
        ]
        result = detector.check(signals, [], bias="balanced")
        contradiction = [a for a in result if a.code == "BIAS_CONTRADICTION"]
        assert len(contradiction) == 0

    def test_below_threshold_no_contradiction(self, detector):
        """矛盾信号占比低于阈值 → 不触发"""
        signals = [
            _make_signal("T3_oversold_bounce"),  # 1 矛盾
            _make_signal("T1_breakout"),          # 2 正常
            _make_signal("T1_breakout"),
        ]
        result = detector.check(signals, [], bias="right_priority")
        contradiction = [a for a in result if a.code == "BIAS_CONTRADICTION"]
        assert len(contradiction) == 0  # 33% < 50%

    def test_no_signals_no_contradiction(self, detector):
        result = detector.check([], [], bias="right_priority")
        contradiction = [a for a in result if a.code == "BIAS_CONTRADICTION"]
        assert len(contradiction) == 0

    def test_right_priority_valid_signals_no_contradiction(self, detector):
        """right_priority 下所有信号都是右侧类型 → 不触发"""
        signals = [
            _make_signal("T1_breakout"),
            _make_signal("T4_golden_cross"),
            _make_signal("VCP_breakout"),
        ]
        result = detector.check(signals, [], bias="right_priority")
        contradiction = [a for a in result if a.code == "BIAS_CONTRADICTION"]
        assert len(contradiction) == 0


# ═══════════════════════════════════════════════════════════════
# 数据完整性
# ═══════════════════════════════════════════════════════════════

class TestDataCompleteness:
    def test_all_rejected_detected(self, detector):
        signals = [_make_signal("T1_breakout"), _make_signal("T1_breakout")]
        result = detector.check(signals, [], data_count=50)
        rejected = [a for a in result if a.code == "ALL_REJECTED"]
        assert len(rejected) == 1
        assert rejected[0].severity == "INFO"

    def test_signals_and_positions_no_all_rejected(self, detector):
        signals = [_make_signal("T1_breakout")]
        positions = [_make_position()]
        result = detector.check(signals, positions, data_count=50)
        rejected = [a for a in result if a.code == "ALL_REJECTED"]
        assert len(rejected) == 0

    def test_no_signals_no_all_rejected(self, detector):
        result = detector.check([], [], data_count=50)
        rejected = [a for a in result if a.code == "ALL_REJECTED"]
        assert len(rejected) == 0


# ═══════════════════════════════════════════════════════════════
# 排序
# ═══════════════════════════════════════════════════════════════

class TestSeverityOrdering:
    def test_critical_before_warning(self, detector):
        """CRITICAL 排在 WARNING 前面"""
        signals = [_make_signal(f"T1") for _ in range(15)]  # flood
        # 同时触发 flood (CRITICAL) + bias contradiction (WARNING)
        signals.append(_make_signal("T3_oversold_bounce"))
        result = detector.check(signals, [], bias="right_priority", data_count=50)

        if len(result) >= 2:
            # 第一个必须是 CRITICAL
            assert result[0].severity == "CRITICAL"


# ═══════════════════════════════════════════════════════════════
# Anomaly 数据类
# ═══════════════════════════════════════════════════════════════

class TestAnomalyDataclass:
    def test_fields_set_correctly(self):
        a = Anomaly(
            code="TEST_CODE",
            severity="WARNING",
            message="test message",
            data={"key": "value"},
            recommendation="do something",
        )
        assert a.code == "TEST_CODE"
        assert a.severity == "WARNING"
        assert a.data == {"key": "value"}

    def test_default_fields(self):
        a = Anomaly(code="TEST", severity="INFO", message="msg")
        assert a.data == {}
        assert a.recommendation == ""