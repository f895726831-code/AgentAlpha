"""
VCPStrategy 单元测试 — 验证波动收缩突破策略。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.vcp import VCPStrategy, make_vcp


def _steady_prices(start: float, step: float, n: int) -> list:
    """生成匀速上涨的价格序列"""
    return [start + i * step for i in range(n)]


def _make_md(close=65.0, volume=15_000_000, avg_vol_5d=10_000_000, **overrides):
    """构造 VCP 测试用的 market_data"""
    history = _steady_prices(50.0, 0.1, 60)  # 50.0 → 55.9, 平稳上涨
    base = {
        "symbol": "600036.SH",
        "close": close,
        "open": close - 1.0,
        "high": close + 0.5,
        "low": close - 0.5,
        "volume": volume,
        "avg_volume_5d": avg_vol_5d,
        "prev_close": close - 1.0,
        "price_history": history,
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
# 基本 VCP 条件
# ═══════════════════════════════════════════════════════════════

class TestBasicVCP:
    def test_contraction_and_breakout_triggers(self):
        """波动收缩 + 放量突破 → True"""
        s = make_vcp(contraction_ratio=0.5, breakout_volume_ratio=1.5)

        # 前 30 天波动大, 后 30 天波动小
        first_half = _steady_prices(50.0, 0.3, 30)   # range: 50.0 → 58.7 ★
        second_half = _steady_prices(58.8, 0.05, 30)  # range: 58.8 → 60.25, 波动小得多
        history = first_half + second_half

        md = _make_md(close=60.25, volume=15_000_000, avg_vol_5d=10_000_000)
        md["price_history"] = history
        assert s.check_conditions(md) is True

    def test_no_contraction_fails(self):
        """波动未收缩 → False"""
        s = make_vcp(contraction_ratio=0.3)  # 要求收缩至 30%

        # 两段波动率接近
        history = _steady_prices(50.0, 0.15, 60)
        md = _make_md(close=60.0, volume=15_000_000, avg_vol_5d=10_000_000)
        md["price_history"] = history
        assert s.check_conditions(md) is False

    def test_no_breakout_fails(self):
        """价格未突破区间高点 → False"""
        s = make_vcp()

        first_half = _steady_prices(50.0, 0.3, 30)
        second_half = _steady_prices(58.8, 0.02, 30)  # 波动收缩
        history = first_half + second_half

        # close=59.2 < hist_high=59.4
        md = _make_md(close=59.2, volume=15_000_000, avg_vol_5d=10_000_000)
        md["price_history"] = history
        assert s.check_conditions(md) is False

    def test_low_volume_fails(self):
        """未放量突破 → False"""
        s = make_vcp(breakout_volume_ratio=2.0)

        first_half = _steady_prices(50.0, 0.3, 30)
        second_half = _steady_prices(58.8, 0.02, 30)
        history = first_half + second_half

        md = _make_md(close=60.25, volume=8_000_000, avg_vol_5d=10_000_000)
        md["price_history"] = history
        assert s.check_conditions(md) is False

    def test_insufficient_history_fails(self):
        """历史数据不足 → False"""
        s = make_vcp(lookback_days=60, min_history_bars=60)
        md = _make_md(price_history=[50.0, 51.0, 52.0])
        assert s.check_conditions(md) is False

    def test_zero_first_std_fails(self):
        """前半段标准差为零 (一字板) → False"""
        s = make_vcp()
        history = [55.0] * 30 + _steady_prices(55.0, 0.05, 30)
        md = _make_md(close=60.25, volume=15_000_000, avg_vol_5d=10_000_000)
        md["price_history"] = history
        assert s.check_conditions(md) is False


# ═══════════════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════════════

class TestSignalGeneration:
    def test_generates_valid_signal(self):
        s = make_vcp()
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.symbol == "600036.SH"
        assert signal.strategy_type == "VCP_breakout"
        assert signal.direction == "long"
        assert signal.stop_loss < signal.trigger_price
        assert len(signal.take_profit) > 0

    def test_stop_loss_and_take_profit(self):
        s = make_vcp(stop_loss_pct=5.0, take_profit_pct=15.0)
        md = _make_md(close=65.0)
        signal = s.generate_signal(md)
        assert signal.stop_loss == pytest.approx(61.75, abs=0.01)  # 65 * 0.95
        assert signal.take_profit[0] == pytest.approx(74.75, abs=0.01)  # 65 * 1.15

    def test_confidence_between_0_and_1(self):
        s = make_vcp()
        md = _make_md()
        signal = s.generate_signal(md)
        assert 0 <= signal.confidence <= 1.0

    def test_edge_positive(self):
        s = make_vcp(stop_loss_pct=5.0, take_profit_pct=15.0)
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.edge > 0


# ═══════════════════════════════════════════════════════════════
# 置信度
# ═══════════════════════════════════════════════════════════════

class TestConfidence:
    def test_high_volume_boost(self):
        s = make_vcp()
        md = _make_md(volume=30_000_000, avg_vol_5d=10_000_000)  # vol_ratio = 3.0
        conf = s.get_confidence(md)
        assert conf > s.params.confidence_threshold + 0.15

    def test_moderate_volume_boost(self):
        s = make_vcp()
        md = _make_md(volume=20_000_000, avg_vol_5d=10_000_000)  # vol_ratio = 2.0
        conf = s.get_confidence(md)
        assert conf > s.params.confidence_threshold + 0.10

    def test_extreme_contraction_boost(self):
        s = make_vcp()
        first_half = _steady_prices(50.0, 0.5, 30)   # 宽幅波动
        second_half = _steady_prices(64.5, 0.02, 30)  # 极度收缩
        history = first_half + second_half
        md = _make_md(volume=20_000_000)
        md["price_history"] = history
        conf = s.get_confidence(md)
        assert conf > 0.6


# ═══════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════

class TestFactory:
    def test_creates_strategy(self):
        s = make_vcp(lookback_days=40, contraction_ratio=0.4)
        assert s.strategy_type == "VCP_breakout"
        assert s._get_filter("lookback_days") == 40
        assert s._get_filter("contraction_ratio") == 0.4

    def test_defaults(self):
        s = make_vcp()
        assert s._get_filter("lookback_days") == 60
        assert s._get_filter("contraction_ratio") == 0.5
        assert s._get_filter("stop_loss_pct") == 5.0

    def test_serialization_roundtrip(self):
        s = make_vcp(lookback_days=80, contraction_ratio=0.4)
        d = s.to_params_dict()
        restored = VCPStrategy.from_params_dict(d)
        assert restored._get_filter("lookback_days") == 80
        assert restored._get_filter("contraction_ratio") == 0.4