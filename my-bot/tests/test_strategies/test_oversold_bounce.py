"""
OversoldBounceStrategy 单元测试 — 验证 T3 超跌反弹策略。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.oversold_bounce import OversoldBounceStrategy, make_oversold_bounce


def _make_md(**overrides):
    """构造超跌反弹测试用的 market_data"""
    base = {
        "symbol": "000001.SZ",
        "close": 10.0,
        "open": 9.5,
        "high": 10.1,
        "low": 9.3,
        "volume": 3_000_000,
        "avg_volume_5d": 10_000_000,   # vol_ratio = 0.3 (缩量)
        "prev_close": 9.5,             # close > prev_close (止跌阳线)
        "rsi_14": 28.0,                # 超卖触发
        "rsi_14_prev": 32.0,           # 前一日未超卖 (下穿中)
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
# 基本超跌条件
# ═══════════════════════════════════════════════════════════════

class TestBasicOversold:
    def test_standard_oversold_bounce_triggers(self):
        """RSI 超卖 + 缩量 + 止跌阳线 → True"""
        s = make_oversold_bounce()
        md = _make_md()
        assert s.check_conditions(md) is True

    def test_not_oversold_fails(self):
        """RSI 未超卖 → False"""
        s = make_oversold_bounce(rsi_oversold=30)
        md = _make_md(rsi_14=45.0, rsi_14_prev=50.0)
        assert s.check_conditions(md) is False

    def test_missing_rsi_fails(self):
        """无 RSI 数据 → False"""
        s = make_oversold_bounce()
        md = _make_md()
        md.pop("rsi_14", None)
        assert s.check_conditions(md) is False

    def test_no_volume_shrink_fails(self):
        """未缩量 (放量下跌) → False"""
        s = make_oversold_bounce(volume_shrink_ratio=0.6)
        md = _make_md(volume=10_000_000, avg_volume_5d=10_000_000)  # vol_ratio = 1.0
        assert s.check_conditions(md) is False

    def test_no_bullish_candle_fails(self):
        """未收阳 → False"""
        s = make_oversold_bounce()
        md = _make_md(close=9.3, prev_close=9.5)  # close < prev_close
        assert s.check_conditions(md) is False


# ═══════════════════════════════════════════════════════════════
# RSI 下穿逻辑
# ═══════════════════════════════════════════════════════════════

class TestRSICrossover:
    def test_already_oversold_no_crossover_fails(self):
        """RSI 已经超卖多日 → 非首次下穿 → False"""
        s = make_oversold_bounce()
        md = _make_md(rsi_14=25.0, rsi_14_prev=28.0)  # prev 也在超卖区
        assert s.check_conditions(md) is False

    def test_first_crossover_triggers(self):
        """RSI 首次从超卖线上方下穿 → True"""
        s = make_oversold_bounce(rsi_oversold=30)
        md = _make_md(rsi_14=29.0, rsi_14_prev=31.0)
        assert s.check_conditions(md) is True

    def test_custom_rsi_period(self):
        """自定义 RSI 周期"""
        s = make_oversold_bounce(rsi_period=7, rsi_oversold=25)
        md = _make_md(rsi_14=40)  # rsi_14 不超卖
        md["rsi_7"] = 22.0        # rsi_7 超卖
        md["rsi_7_prev"] = 28.0
        md.pop("rsi_14", None)
        md.pop("rsi_14_prev", None)
        assert s.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# 跌停过滤
# ═══════════════════════════════════════════════════════════════

class TestLimitDownFilter:
    def test_limit_down_fails(self):
        """跌停板 → 不接飞刀 → False"""
        s = make_oversold_bounce()
        md = _make_md(close=8.5, prev_close=9.5)  # close < 9.5*0.9 = 8.55
        assert s.check_conditions(md) is False

    def test_near_limit_but_not_passes(self):
        """接近跌停但未触及 → True (从 10 元跌到 9.05, 收阳, RSI 触发超卖)"""
        s = make_oversold_bounce()
        md = _make_md(close=9.05, prev_close=9.0, high=9.1, low=8.2,  # close > prev_close (收阳), low 接近跌停
                      rsi_14=28.0, rsi_14_prev=32.0)
        assert s.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════════════

class TestSignalGeneration:
    def test_generates_valid_signal(self):
        s = make_oversold_bounce()
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.symbol == "000001.SZ"
        assert signal.strategy_type == "T3_oversold_bounce"
        assert signal.direction == "long"
        assert signal.stop_loss < signal.trigger_price
        assert len(signal.take_profit) > 0

    def test_tight_stop_loss(self):
        """超跌反弹止损应偏紧"""
        s = make_oversold_bounce(stop_loss_pct=3.0)
        md = _make_md(close=10.0)
        signal = s.generate_signal(md)
        assert signal.stop_loss == pytest.approx(9.70, abs=0.01)

    def test_confidence_between_0_and_1(self):
        s = make_oversold_bounce()
        md = _make_md()
        signal = s.generate_signal(md)
        assert 0 <= signal.confidence <= 1.0

    def test_edge_positive(self):
        s = make_oversold_bounce(stop_loss_pct=3.0, take_profit_pct=8.0)
        md = _make_md()
        signal = s.generate_signal(md)
        assert signal.edge > 0


# ═══════════════════════════════════════════════════════════════
# 置信度
# ═══════════════════════════════════════════════════════════════

class TestConfidence:
    def test_extreme_oversold_boost(self):
        s = make_oversold_bounce()
        md = _make_md(rsi_14=18.0)  # 极度超卖
        conf = s.get_confidence(md)
        assert conf > 0.6

    def test_moderate_oversold_boost(self):
        s = make_oversold_bounce()
        md = _make_md(rsi_14=28.0)  # 接近超卖线
        conf = s.get_confidence(md)
        assert conf > 0.5

    def test_extreme_shrink_boost(self):
        s = make_oversold_bounce()
        md = _make_md(volume=2_000_000, avg_vol_5d=10_000_000)  # vol_ratio = 0.2
        conf = s.get_confidence(md)
        assert conf > 0.55

    def test_lower_wick_boost(self):
        """长下影线: 抄底资金进场"""
        s = make_oversold_bounce()
        md = _make_md(close=10.0, low=9.1, high=10.1)  # lower_wick = (10-9.1)/(10.1-9.1) = 0.9
        conf = s.get_confidence(md)
        assert conf > 0.55


# ═══════════════════════════════════════════════════════════════
# 工厂
# ═══════════════════════════════════════════════════════════════

class TestFactory:
    def test_creates_strategy(self):
        s = make_oversold_bounce(rsi_period=7, rsi_oversold=25)
        assert s.strategy_type == "T3_oversold_bounce"
        assert s._get_filter("rsi_period") == 7
        assert s._get_filter("rsi_oversold") == 25

    def test_defaults(self):
        s = make_oversold_bounce()
        assert s._get_filter("rsi_period") == 14
        assert s._get_filter("rsi_oversold") == 30
        assert s._get_filter("volume_shrink_ratio") == 0.6

    def test_serialization_roundtrip(self):
        s = make_oversold_bounce(rsi_period=10, stop_loss_pct=4.0)
        d = s.to_params_dict()
        restored = OversoldBounceStrategy.from_params_dict(d)
        assert restored._get_filter("rsi_period") == 10
        assert restored._get_filter("stop_loss_pct") == 4.0