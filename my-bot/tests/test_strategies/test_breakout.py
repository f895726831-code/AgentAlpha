"""
BreakoutStrategy 单元测试 — 验证 T1 突破策略的 check_conditions 逻辑。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.breakout import BreakoutStrategy, make_breakout
from strategies.base import StrategyParams


# ─── Fixtures ───

def _make_market_data(**overrides):
    """构造市场数据字典的快捷方法"""
    base = {
        "symbol": "600519.SH",
        "close": 1850.0,
        "open": 1830.0,
        "high": 1860.0,
        "low": 1820.0,
        "volume": 15_000_000,
        "avg_volume_5d": 10_000_000,
        "prev_close": 1820.0,
        "ma_60": 1750.0,
        "ma_20": 1820.0,
        "price_history": None,  # will be set per-test
    }
    base.update(overrides)
    return base


def _make_history(n_days: int, max_price: float) -> list:
    """生成 N 天盘整历史 (价格在 max_price 以下波动)"""
    import random
    random.seed(42)
    return [max_price - random.uniform(1, 20) for _ in range(n_days)]


def _make_md_with_history(history, **overrides):
    """构造含历史的 market_data — history 不含当日 bar"""
    base = {
        "symbol": "600519.SH",
        "close": 1850.0,
        "open": 1830.0,
        "high": 1860.0,
        "low": 1820.0,
        "volume": 15_000_000,
        "avg_volume_5d": 10_000_000,
        "prev_close": 1820.0,
        "ma_60": 1750.0,
        "ma_20": 1820.0,
        "price_history": history,  # 纯历史, 不含当日
    }
    base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════
# 基本突破条件
# ═══════════════════════════════════════════════════════════════

class TestBasicBreakout:
    """验证核心突破逻辑"""

    def test_standard_breakout_triggers(self):
        """标准突破: 30日盘整后放量突破"""
        strategy = make_breakout(lookback_days=30)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            price_history=history,  # 最新价突破
            volume=15_000_000,
            avg_volume_5d=10_000_000,
        )
        assert strategy.check_conditions(md) is True

    def test_no_breakout_no_signal(self):
        """未突破 → False"""
        strategy = make_breakout(lookback_days=30)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1790.0,  # 未突破
            price_history=history,
        )
        assert strategy.check_conditions(md) is False

    def test_volume_below_threshold_fails(self):
        """量比不足 → False"""
        strategy = make_breakout(volume_ratio_min=1.5)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            price_history=history,
            volume=10_000_000,      # 刚好等于均量
            avg_volume_5d=10_000_000,  # vol_ratio = 1.0 < 1.5
        )
        assert strategy.check_conditions(md) is False

    def test_volume_at_threshold_passes(self):
        """量比正好等于阈值 → True"""
        strategy = make_breakout(volume_ratio_min=1.5)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            price_history=history,
            volume=15_000_000,       # 1.5 × 均量
            avg_volume_5d=10_000_000,
        )
        assert strategy.check_conditions(md) is True

    def test_no_volume_confirm_skips_check(self):
        """volume_ratio_min=0 → 跳过量比确认"""
        strategy = make_breakout(volume_ratio_min=0)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            price_history=history,
            volume=1,          # 极低量
            avg_volume_5d=10_000_000,
        )
        assert strategy.check_conditions(md) is True

    def test_insufficient_data_fails(self):
        """盘整数据不足 min_consolidation_days → False"""
        strategy = make_breakout(lookback_days=30, min_consolidation_days=10)
        history = _make_history(5, 1800.0)  # only 5 days
        md = _make_market_data(close=1850.0, price_history=history)
        assert strategy.check_conditions(md) is False


# ═══════════════════════════════════════════════════════════════
# 跳空过滤
# ═══════════════════════════════════════════════════════════════

class TestGapFilter:
    """验证跳空上限逻辑"""

    def test_gap_within_limit_passes(self):
        """跳空在允许范围内 → True"""
        strategy = make_breakout(gap_max_pct=2.0)
        history = _make_history(30, 1800.0)
        # 前日收盘 1820, 今日开盘 1845 → gap = (1845/1820-1)*100 = 1.37%
        md = _make_market_data(
            close=1860.0,
            open=1845.0,
            prev_close=1820.0,
            price_history=history,
        )
        assert strategy.check_conditions(md) is True

    def test_gap_too_large_fails(self):
        """跳空超过上限 → False"""
        strategy = make_breakout(gap_max_pct=2.0)
        history = _make_history(30, 1800.0)
        # 前日收盘 1820, 今日开盘 1890 → gap = 3.85%
        md = _make_market_data(
            close=1900.0,
            open=1890.0,
            prev_close=1820.0,
            price_history=history,
        )
        assert strategy.check_conditions(md) is False

    def test_gap_filter_disabled(self):
        """gap_max_pct=0 → 跳过跳空检查"""
        strategy = make_breakout(gap_max_pct=0)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1900.0,
            open=1890.0,
            prev_close=1800.0,
            price_history=history,
        )
        assert strategy.check_conditions(md) is True

    def test_no_prev_close_skips_gap(self):
        """无前日收盘价 → 跳过跳空检查"""
        strategy = make_breakout(gap_max_pct=2.0)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            prev_close=0,   # no prev close
            price_history=history,
        )
        assert strategy.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# MA 趋势过滤
# ═══════════════════════════════════════════════════════════════

class TestMAFilter:
    """验证均线趋势过滤"""

    def test_close_above_ma_passes(self):
        """收盘在 MA 之上 → True"""
        strategy = make_breakout(ma_filter=60)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            ma_60=1750.0,
            price_history=history,
        )
        assert strategy.check_conditions(md) is True

    def test_close_below_ma_fails(self):
        """收盘在 MA 之下 → False"""
        strategy = make_breakout(ma_filter=60)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1700.0,
            ma_60=1750.0,
            price_history=history,
        )
        assert strategy.check_conditions(md) is False

    def test_ma_filter_disabled(self):
        """ma_filter=0 → 跳过 MA 检查"""
        strategy = make_breakout(ma_filter=0)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            price_history=history,
        )
        md.pop("ma_60", None)
        assert strategy.check_conditions(md) is True

    def test_ma_not_available_passes(self):
        """MA 数据缺失 → 跳过 (不阻塞)"""
        strategy = make_breakout(ma_filter=60)
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            price_history=history,
        )
        md.pop("ma_60", None)
        assert strategy.check_conditions(md) is True


# ═══════════════════════════════════════════════════════════════
# 固定突破价格
# ═══════════════════════════════════════════════════════════════

class TestFixedBreakoutPrice:
    """验证固定突破价格（如 60 元突破）"""

    def test_fixed_breakout_60(self):
        """固定 60 元突破 — 经典 breakout_60 场景"""
        strategy = make_breakout(breakout_price=60.0, lookback_days=30)
        history = [55 + i * 0.1 for i in range(30)]  # 55 → 58, max 57.9
        md = _make_market_data(
            close=62.0,
            price_history=history,
            volume=15_000_000,
            avg_volume_5d=10_000_000,
            ma_60=55.0,       # MA 适配 60 元股价
            prev_close=61.0,  # 前日收盘适配
            open=61.5,        # 开盘适配
        )
        assert strategy.check_conditions(md) is True

    def test_fixed_breakout_no_cross(self):
        """未突破固定价格 → False"""
        strategy = make_breakout(breakout_price=60.0)
        history = [55 + i * 0.1 for i in range(30)]
        md = _make_market_data(
            close=59.5,   # not yet 60
            price_history=history,
        )
        assert strategy.check_conditions(md) is False

    def test_fixed_price_already_above_in_history(self):
        """盘整期内曾有价格高于突破位 → False (不是真·盘整)"""
        strategy = make_breakout(breakout_price=60.0)
        history = [58, 61, 59, 58] * 7 + [59, 58]  # 历史中有 61 > 60
        md = _make_market_data(
            close=62.0,
            price_history=history,
        )
        assert strategy.check_conditions(md) is False


# ═══════════════════════════════════════════════════════════════
# 信号生成
# ═══════════════════════════════════════════════════════════════

class TestSignalGeneration:
    """验证 generate_signal 输出"""

    def test_generates_valid_signal(self):
        strategy = make_breakout()
        history = _make_history(30, 1800.0)
        md = _make_market_data(
            close=1850.0,
            price_history=history,
        )
        signal = strategy.generate_signal(md)
        assert signal is not None
        assert signal.symbol == "600519.SH"
        assert signal.strategy_type == "T1_breakout"
        assert signal.direction == "long"
        assert signal.trigger_price == 1850.0
        assert signal.stop_loss < signal.trigger_price
        assert len(signal.take_profit) > 0
        assert signal.take_profit[0] > signal.trigger_price

    def test_stop_loss_calculation(self):
        strategy = make_breakout(stop_loss_pct=5.0)
        history = _make_history(30, 1800.0)
        md = _make_market_data(close=1850.0, price_history=history)
        signal = strategy.generate_signal(md)
        assert signal.stop_loss == pytest.approx(1850.0 * 0.95, abs=1.0)

    def test_take_profit_calculation(self):
        strategy = make_breakout(take_profit_pct=12.0)
        history = _make_history(30, 1800.0)
        md = _make_market_data(close=1850.0, price_history=history)
        signal = strategy.generate_signal(md)
        assert signal.take_profit[0] == pytest.approx(1850.0 * 1.12, abs=1.0)

    def test_confidence_between_0_and_1(self):
        strategy = make_breakout()
        history = _make_history(30, 1800.0)
        md = _make_market_data(close=1850.0, price_history=history)
        signal = strategy.generate_signal(md)
        assert 0 <= signal.confidence <= 1.0

    def test_edge_positive(self):
        strategy = make_breakout(stop_loss_pct=5.0, take_profit_pct=12.0)
        history = _make_history(30, 1800.0)
        md = _make_market_data(close=1850.0, price_history=history)
        signal = strategy.generate_signal(md)
        assert signal.edge > 0  # reward/risk > 0

    def test_metadata_has_generated_by(self):
        strategy = make_breakout()
        history = _make_history(30, 1800.0)
        md = _make_market_data(close=1850.0, price_history=history)
        signal = strategy.generate_signal(md)
        assert "generated_by" in signal.metadata
        assert "TiMi" in signal.metadata["generated_by"]


# ═══════════════════════════════════════════════════════════════
# 置信度计算
# ═══════════════════════════════════════════════════════════════

class TestConfidence:
    """验证置信度增量逻辑"""

    def test_high_volume_boost(self):
        strategy = make_breakout()
        md = _make_market_data(volume=30_000_000, avg_volume_5d=10_000_000)  # vol_ratio=3.0
        conf = strategy.get_confidence(md)
        assert conf > strategy.params.confidence_threshold
        assert conf >= 0.70  # base 0.5 + 0.20 for vol_ratio >= 3.0

    def test_low_gap_boost(self):
        strategy = make_breakout()
        md = _make_market_data(open=1822.0, prev_close=1820.0)  # gap ≈ 0.11%
        conf = strategy.get_confidence(md)
        assert conf > 0.5  # should be boosted

    def test_high_gap_penalty(self):
        strategy = make_breakout()
        md = _make_market_data(open=1860.0, prev_close=1820.0)  # gap ≈ 2.2%
        conf = strategy.get_confidence(md)
        assert conf < 0.55  # penalized

    def test_confidence_clamped_to_1(self):
        strategy = make_breakout(confidence_threshold=0.9)
        md = _make_market_data(
            volume=50_000_000,
            avg_volume_5d=10_000_000,  # vol_ratio=5
            open=1821.0,
            prev_close=1820.0,  # tiny gap
        )
        conf = strategy.get_confidence(md)
        assert conf <= 1.0


# ═══════════════════════════════════════════════════════════════
# 策略工厂函数
# ═══════════════════════════════════════════════════════════════

class TestFactory:
    """验证 make_breakout 工厂函数"""

    def test_creates_strategy(self):
        s = make_breakout(lookback_days=20, volume_ratio_min=2.0)
        assert s.strategy_type == "T1_breakout"
        assert s._get_filter("lookback_days") == 20
        assert s._get_filter("volume_ratio_min") == 2.0

    def test_default_values(self):
        s = make_breakout()
        assert s._get_filter("lookback_days") == 30
        assert s._get_filter("volume_ratio_min") == 1.5
        assert s._get_filter("stop_loss_pct") == 5.0

    def test_mode_and_bias(self):
        s = make_breakout(mode="earnings_season", bias="right_priority")
        assert s.params.mode == "earnings_season"
        assert s.params.bias == "right_priority"


# ═══════════════════════════════════════════════════════════════
# 边界条件
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """异常输入边界测试"""

    def test_empty_market_data(self):
        strategy = make_breakout()
        assert strategy.check_conditions({}) is False

    def test_zero_volume(self):
        strategy = make_breakout()
        history = _make_history(30, 1800.0)
        md = _make_market_data(close=1850.0, volume=0, price_history=history + [1850.0])
        assert strategy.check_conditions(md) is False

    def test_dynamic_breakout_no_history(self):
        """动态突破位 + 无历史 → False"""
        strategy = make_breakout(breakout_price=None)  # dynamic
        md = _make_market_data(close=1850.0, price_history=[])
        assert strategy.check_conditions(md) is False

    def test_dynamic_breakout_with_history(self):
        """动态突破位 = lookback 高点"""
        strategy = make_breakout(lookback_days=20, breakout_price=None)
        history = _make_history(20, 1800.0)
        md = _make_market_data(
            close=1810.0,   # 突破 1800 高点
            price_history=history,
            volume=15_000_000,
            avg_volume_5d=10_000_000,
        )
        assert strategy.check_conditions(md) is True

    def test_strategy_is_active_for_mode(self):
        strategy = make_breakout(mode="normal", bias="right_priority")
        # 策略配置为 normal → 在所有模式下均可激活 (风控由 PositionSizer 执行)
        assert strategy.is_active_for_mode("normal", "right_priority") is True
        # risk_off 下策略本身仍然 active (只是仓位计算会返回 hold)
        assert strategy.is_active_for_mode("risk_off", "right_priority") is True
        # bias 不匹配 → 不激活
        assert strategy.is_active_for_mode("normal", "left_priority") is False

    def test_serialization_roundtrip(self):
        strategy = make_breakout(lookback_days=25, volume_ratio_min=2.0)
        d = strategy.to_params_dict()
        restored = BreakoutStrategy.from_params_dict(d)
        assert restored._get_filter("lookback_days") == 25
        assert restored._get_filter("volume_ratio_min") == 2.0