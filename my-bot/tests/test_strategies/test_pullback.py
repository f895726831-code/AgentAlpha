"""
PullbackStrategy 单元测试 — 验证 T2 回调低吸策略。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.pullback import PullbackStrategy, make_pullback


def _make_md(**overrides):
    """构造 pullback 测试用的 market_data"""
    base = {
        "symbol": "000001.SZ",
        "close": 9.5,
        "open": 9.6,
        "high": 9.7,
        "low": 9.4,
        "volume": 3_000_000,
        "avg_volume_5d": 10_000_000,   # vol_ratio = 0.3 (缩量)
        "prev_close": 9.8,
        "ma_20": 10.0,
        "ma_20_prev": 9.9,             # MA20 向上
        "ma_60": 9.0,
        "ma_60_prev": 8.9,             # MA60 向上
        "recent_high_20d": 10.0,       # dd = (10.0-9.5)/10.0 = 5.0% (在 2-8% 范围内)
        "consecutive_down_days": 2,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
# 基本回调条件
# ═══════════════════════════════════════════════════════════════

class TestBasicPullback:
    def test_standard_pullback_triggers(self):
        """标准回调低吸: 趋势向上 + 回调 MA20 + 缩量"""
        s = make_pullback()
        md = _make_md()
        assert s.check_conditions(md) is True

    def test_no_pullback_above_ma(self):
        """价格在 MA 之上 → 不在回调 → False"""
        s = make_pullback()
        md = _make_md(close=10.5, ma_20=10.0)  # close > MA20
        assert s.check_conditions(md) is False

    def test_below_trend_ma_fails(self):
        """跌破趋势 MA (MA60) → 趋势已坏 → False"""
        s = make_pullback()
        md = _make_md(close=8.5, ma_20=9.0, ma_60=9.0)  # close < MA60
        assert s.check_conditions(md) is False

    def test_volume_not_shrunk_fails(self):
        """未缩量 → 抛压未衰竭 → False"""
        s = make_pullback(volume_shrink_ratio=0.7)
        md = _make_md(volume=10_000_000, avg_volume_5d=10_000_000)  # vol_ratio = 1.0
        assert s.check_conditions(md) is False

    def test_ma60_trend_down_fails(self):
        """MA60 向下 → 趋势不成立 → False"""
        s = make_pullback()
        md = _make_md(ma_60=9.0, ma_60_prev=9.5)  # MA60 下降
        assert s.check_conditions(md) is False

    def test_too_many_down_days_fails(self):
        """连跌超过 5 天 → 趋势可能反转 → False"""
        s = make_pullback(consecutive_drop_days_max=5)
        md = _make_md(consecutive_down_days=6)
        assert s.check_conditions(md) is False


# ═══════════════════════════════════════════════════════════════
# 回调幅度
# ═══════════════════════════════════════════════════════════════

class TestPullbackMagnitude:
    def test_within_range_passes(self):
        s = make_pullback(pullback_pct_min=2.0, pullback_pct_max=8.0)
        md = _make_md(close=9.97, recent_high_20d=10.5)  # dd = 5.0%
        assert s.check_conditions(md) is True

    def test_too_small_fails(self):
        s = make_pullback(pullback_pct_min=2.0)
        md = _make_md(close=10.45, recent_high_20d=10.5)  # dd = 0.5%
        assert s.check_conditions(md) is False

    def test_too_deep_fails(self):
        s = make_pullback(pullback_pct_max=8.0)
        md = _make_md(close=9.0, recent_high_20d=10.5)  # dd = 14.3%
        assert s.check_conditions(md) is False

    def test_no_recent_high_skips_check(self):
        """无 recent_high → 跳过回调幅度检查"""
        s = make_pullback()
        md = _make_md()
        md.pop("recent_high_20d", None)
        assert s.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# 支撑位
# ═══════════════════════════════════════════════════════════════

class TestSupportLevels:
    def test_ma20_support_passes(self):
        """MA20 支撑: close 在 MA20 下方但很近 (2% 容忍内)"""
        s = make_pullback(support_levels=["ma20"])
        md = _make_md(close=9.5, ma_20=9.6, ma_60=9.0)  # close 9.5 < ma20 9.6 (回调), 9.5 >= 9.6*0.98=9.408 (支撑)
        assert s.check_conditions(md) is True

    def test_ma20_no_support_fails(self):
        """离 MA20 太远 → 无支撑 → False"""
        s = make_pullback(support_levels=["ma20"])
        md = _make_md(close=9.2, ma_20=9.8, ma_60=9.0)  # close 9.2 < ma20*0.98=9.604
        assert s.check_conditions(md) is False

    def test_ma60_support_passes(self):
        """MA60 支撑: close 在 MA20 下方 (回调中) 但 MA60 很近"""
        s = make_pullback(support_levels=["ma60"])
        md = _make_md(close=9.31, ma_20=9.8, ma_60=9.3, ma_60_prev=9.2)  # close=9.31 > ma60=9.3, 9.31 >= 9.3*0.98=9.114
        assert s.check_conditions(md) is True

    def test_multi_support_one_match(self):
        """多支撑位: 至少一个满足即可"""
        s = make_pullback(support_levels=["ma20", "ma60"])
        md = _make_md(close=9.5, ma_20=9.8, ma_60=9.45)  # ma60 满足: 9.5 >= 9.45*0.98=9.261
        assert s.check_conditions(md) is True

    def test_no_support_match_fails(self):
        s = make_pullback(support_levels=["ma20", "ma60"])
        md = _make_md(close=9.5, ma_20=10.5, ma_60=10.0)  # close well below both
        assert s.check_conditions(md) is False


# ═══════════════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════════════

class TestSignalGeneration:
    def test_generates_valid_signal(self):
        s = make_pullback()
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.symbol == "000001.SZ"
        assert signal.strategy_type == "T2_pullback"
        assert signal.direction == "long"
        assert signal.stop_loss < signal.trigger_price
        assert len(signal.take_profit) > 0

    def test_stop_loss_uses_trend_ma(self):
        """止损取趋势 MA 和百分比止损中更紧的"""
        s = make_pullback(stop_loss_pct=5.0, trend_period=60)
        md = _make_md(close=9.5, ma_60=9.3)  # MA60 止损 = 9.3, 百分比 = 9.025
        signal = s.generate_signal(md)
        # 9.3 > 9.025, 所以取 9.3 (更紧)
        assert signal.stop_loss == pytest.approx(9.3, abs=0.1)

    def test_edge_positive(self):
        s = make_pullback(stop_loss_pct=5.0, take_profit_pct=10.0)
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.edge > 0

    def test_confidence_between_0_and_1(self):
        s = make_pullback()
        md = _make_md()
        signal = s.generate_signal(md)
        assert 0 <= signal.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════
# 置信度
# ═══════════════════════════════════════════════════════════════

class TestConfidence:
    def test_heavy_shrink_boost(self):
        s = make_pullback()
        md = _make_md(volume=2_000_000, avg_volume_5d=10_000_000)  # vol_ratio=0.2
        conf = s.get_confidence(md)
        assert conf > s.params.confidence_threshold

    def test_ideal_drawdown_boost(self):
        s = make_pullback()
        md = _make_md(close=10.0, recent_high_20d=10.5)  # dd = 4.76%
        conf = s.get_confidence(md)
        assert conf > 0.5


# ═══════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════

class TestFactory:
    def test_creates_strategy(self):
        s = make_pullback(ma_period=30, volume_shrink_ratio=0.5)
        assert s.strategy_type == "T2_pullback"
        assert s._get_filter("ma_period") == 30
        assert s._get_filter("volume_shrink_ratio") == 0.5

    def test_defaults(self):
        s = make_pullback()
        assert s._get_filter("ma_period") == 20
        assert s._get_filter("trend_period") == 60
        assert s._get_filter("stop_loss_pct") == 5.0

    def test_serialization_roundtrip(self):
        s = make_pullback(trend_period=50, pullback_pct_min=3.0)
        d = s.to_params_dict()
        restored = PullbackStrategy.from_params_dict(d)
        assert restored._get_filter("trend_period") == 50
        assert restored._get_filter("pullback_pct_min") == 3.0