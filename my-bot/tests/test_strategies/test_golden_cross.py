"""
GoldenCrossStrategy 单元测试 — 验证 T4 均线金叉策略。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.golden_cross import GoldenCrossStrategy, make_golden_cross


def _make_md(**overrides):
    """构造金叉测试用的 market_data"""
    base = {
        "symbol": "600519.SH",
        "close": 62.0,
        "open": 60.0,
        "high": 63.0,
        "low": 59.5,
        "volume": 15_000_000,
        "avg_volume_5d": 10_000_000,   # vol_ratio = 1.5
        "prev_close": 60.0,
        "ma_20": 59.0,                  # 金叉: ma20 > ma60
        "ma_60": 58.0,
        "ma_20_prev": 57.5,             # 前一日: ma20 <= ma60 (未金叉)
        "ma_60_prev": 58.0,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
# 基本金叉条件
# ═══════════════════════════════════════════════════════════════

class TestBasicGoldenCross:
    def test_standard_cross_triggers(self):
        """MA20 上穿 MA60 + 放量 + 价格在 MA60 上方 → True"""
        s = make_golden_cross()
        md = _make_md()
        assert s.check_conditions(md) is True

    def test_no_cross_fails(self):
        """短均线仍在下方 → False"""
        s = make_golden_cross()
        md = _make_md(ma_20=57.0, ma_60=58.0, ma_20_prev=56.0, ma_60_prev=57.0)
        assert s.check_conditions(md) is False

    def test_missing_ma_fails(self):
        """缺少 MA 数据 → False"""
        s = make_golden_cross()
        md = _make_md()
        md.pop("ma_20", None)
        assert s.check_conditions(md) is False

    def test_already_crossed_fails(self):
        """之前已经金叉 → 非首次 → False"""
        s = make_golden_cross()
        md = _make_md(ma_20=59.0, ma_60=58.0, ma_20_prev=59.0, ma_60_prev=58.0)
        assert s.check_conditions(md) is False

    def test_price_below_ma_long_fails(self):
        """价格在长期均线下方 → 无效金叉 → False"""
        s = make_golden_cross(price_above_ma_long=True)
        md = _make_md(close=57.0, ma_20=59.0, ma_60=58.0)  # close=57 < ma_60=58
        assert s.check_conditions(md) is False

    def test_price_above_ma_long_disabled(self):
        """关闭 price_above_ma_long → 允许弱势金叉"""
        s = make_golden_cross(price_above_ma_long=False)
        md = _make_md(close=57.0, ma_20=59.0, ma_60=58.0)
        assert s.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# 成交量确认
# ═══════════════════════════════════════════════════════════════

class TestVolumeConfirm:
    def test_low_volume_fails(self):
        """缩量金叉 → False"""
        s = make_golden_cross(volume_confirm_ratio=1.5)
        md = _make_md(volume=5_000_000, avg_vol_5d=10_000_000)  # vol_ratio=0.5
        assert s.check_conditions(md) is False

    def test_volume_disabled_passes(self):
        """volume_confirm_ratio=0 → 跳过量确认"""
        s = make_golden_cross(volume_confirm_ratio=0)
        md = _make_md(volume=5_000_000, avg_vol_5d=10_000_000)
        assert s.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# 自定义 MA 周期
# ═══════════════════════════════════════════════════════════════

class TestCustomMA:
    def test_ma5_cross_ma20(self):
        s = make_golden_cross(ma_short=5, ma_long=20)
        md = _make_md()
        md["ma_5"] = 60.0
        md["ma_20"] = 59.0
        md["ma_5_prev"] = 58.0
        md["ma_20_prev"] = 59.0
        md.pop("ma_20", None)  # 移除默认的 (key 冲突)
        md["ma_20"] = 59.0     # 重新设为 long
        md["ma_60"] = 57.0     # 保留
        assert s.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════════════

class TestSignalGeneration:
    def test_generates_valid_signal(self):
        s = make_golden_cross()
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.symbol == "600519.SH"
        assert signal.strategy_type == "T4_golden_cross"
        assert signal.direction == "long"
        assert signal.stop_loss < signal.trigger_price
        assert len(signal.take_profit) > 0

    def test_stop_loss_uses_technical_stop(self):
        """止损使用长期均线或百分比止损中更紧的"""
        s = make_golden_cross(stop_loss_pct=10.0, ma_long=60)
        md = _make_md(close=62.0, ma_60=60.0)
        signal = s.generate_signal(md)
        # pct_stop=55.8, technical_stop=60.0, max=60.0
        assert signal.stop_loss == pytest.approx(60.0, abs=0.01)

    def test_confidence_between_0_and_1(self):
        s = make_golden_cross()
        md = _make_md()
        signal = s.generate_signal(md)
        assert 0 <= signal.confidence <= 1.0

    def test_edge_positive(self):
        s = make_golden_cross(stop_loss_pct=5.0, take_profit_pct=15.0)
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.edge > 0


# ═══════════════════════════════════════════════════════════════
# 置信度
# ═══════════════════════════════════════════════════════════════

class TestConfidence:
    def test_high_volume_boost(self):
        s = make_golden_cross()
        md = _make_md(volume=25_000_000, avg_vol_5d=10_000_000)  # vol_ratio = 2.5
        conf = s.get_confidence(md)
        assert conf > 0.55

    def test_ma_spread_boost(self):
        s = make_golden_cross()
        md = _make_md(ma_20=62.0, ma_60=58.0)  # spread = (62-58)/58 = 6.9% > 2%
        conf = s.get_confidence(md)
        assert conf > 0.55

    def test_price_above_ma_short_boost(self):
        s = make_golden_cross()
        md = _make_md(close=62.0, ma_20=59.0)
        conf = s.get_confidence(md)
        assert conf > 0.5


# ═══════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════

class TestFactory:
    def test_creates_strategy(self):
        s = make_golden_cross(ma_short=10, ma_long=30, volume_confirm_ratio=2.0)
        assert s.strategy_type == "T4_golden_cross"
        assert s._get_filter("ma_short") == 10
        assert s._get_filter("ma_long") == 30
        assert s._get_filter("volume_confirm_ratio") == 2.0

    def test_defaults(self):
        s = make_golden_cross()
        assert s._get_filter("ma_short") == 20
        assert s._get_filter("ma_long") == 60
        assert s._get_filter("volume_confirm_ratio") == 1.2

    def test_serialization_roundtrip(self):
        s = make_golden_cross(ma_short=15, ma_long=50)
        d = s.to_params_dict()
        restored = GoldenCrossStrategy.from_params_dict(d)
        assert restored._get_filter("ma_short") == 15
        assert restored._get_filter("ma_long") == 50