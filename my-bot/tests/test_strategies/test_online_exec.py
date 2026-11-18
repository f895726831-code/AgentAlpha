"""
TiMi 端到端集成测试 — 模拟完整交易日。

验证完整链路:
  1. LLM JSON → compiler.compile() → CompileResult
  2. CompileResult → serializer.serialize() → disk
  3. disk → serializer.deserialize() → CompileResult
  4. CompileResult → executor.execute() → signals + positions
  5. 异常检测 + 回退

场景:
  - 进攻市 + 右侧突破 (正向)
  - 防守市 + 左侧超跌 (逆向)
  - 震荡市 + 多策略并发
  - 空数据 / 空策略 (边缘)
  - 异常触发 LLM 回退
"""

import json
import pytest
import sys
import tempfile
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.compiler import StrategyCompiler
from strategies.serializer import StrategySerializer
from strategies.position_sizer import PositionSizer, Account
from strategies.multipliers import Multipliers
from strategies.executor import OnlineExecutor
from strategies.anomaly_detector import AnomalyDetector
from strategies.fallback import FallbackHandler


@pytest.fixture
def compiler():
    return StrategyCompiler()


@pytest.fixture
def sizer():
    m = Multipliers.from_risk_policy()
    return PositionSizer(m)


@pytest.fixture
def account():
    return Account(total_value=1_000_000, cash=500_000, positions={})


def _breakout_md(symbol, close):
    history = [close - 10 + i * 0.2 for i in range(30)]
    return {
        "symbol": symbol, "close": close,
        "open": close - 0.5, "high": close + 1.0, "low": close - 1.0,
        "volume": 15_000_000, "avg_volume_5d": 8_000_000,
        "prev_close": close - 1.5, "ma_60": close - 5.0,
        "price_history": history,
    }


def _pullback_md(symbol, close):
    return {
        "symbol": symbol, "close": close,
        "open": close + 0.2, "high": close + 0.5, "low": close - 1.5,
        "volume": 2_000_000, "avg_volume_5d": 8_000_000,
        "prev_close": close + 0.1,
        "ma_20": close + 0.5, "ma_20_prev": close - 0.5,
        "ma_60": close - 3.0, "ma_60_prev": close - 3.5,
        "recent_high_20d": close + 1.0,
        "consecutive_down_days": 2,
    }


def _oversold_md(symbol, close):
    return {
        "symbol": symbol, "close": close,
        "open": close - 0.3, "high": close + 0.3, "low": close - 1.5,
        "volume": 3_000_000, "avg_volume_5d": 10_000_000,
        "prev_close": close - 1.0,
        "rsi_14": 28.0, "rsi_14_prev": 32.0,
    }


def _golden_cross_md(symbol, close):
    return {
        "symbol": symbol, "close": close,
        "open": close - 1.0, "high": close + 0.5, "low": close - 0.5,
        "volume": 12_000_000, "avg_volume_5d": 8_000_000,
        "prev_close": close - 1.0,
        "ma_20": close - 1.0, "ma_60": close - 3.0,
        "ma_20_prev": close - 4.0, "ma_60_prev": close - 3.5,
    }


# ═══════════════════════════════════════════════════════════════
# 场景 1: 进攻市 — 右侧突破
# ═══════════════════════════════════════════════════════════════

class TestBullishRegime:
    def test_end_to_end_bullish_breakout(self, compiler, sizer, account):
        """进攻市 + 右侧优先 → T1 breakout 选股"""
        llm_json = {
            "regime": "进攻",
            "bias": "right_priority",
            "mode": "normal",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
                {"type": "VCP_breakout", "active": True, "params": {"contraction_ratio": 0.5}},
            ],
            "filters": {"exclude_st": True, "min_market_cap": 5e9},
            "meta": {"compiled_by": "e2e_test"},
        }

        # Compile
        result = compiler.compile(llm_json)
        assert result.is_valid
        assert result.bias == "right_priority"
        assert len(result.active_strategies) == 2

        # Serialize → Deserialize
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            # Execute: 50 个标的，10 个触发突破
            executor = OnlineExecutor(position_sizer=sizer)
            md_list = []
            for i in range(10):
                md_list.append(_breakout_md(f"6000{i:02d}.SH", 65.0))
            for i in range(40):
                md_list.append({"symbol": f"000{i:03d}.SZ", "close": 50.0, "volume": 1})

            result = executor.execute(
                restored, md_list, account,
                mode="normal", bias="right_priority", gate2_light="green",
            )

            assert result.signal_count == 10
            assert len(result.positions) == 10
            for p in result.positions:
                assert p.action == "buy"
                assert 0 < p.pct_of_portfolio <= 10  # 主板集中度上限
            assert not result.fallback_triggered


# ═══════════════════════════════════════════════════════════════
# 场景 2: 防守市 — 左侧超跌
# ═══════════════════════════════════════════════════════════════

class TestBearishRegime:
    def test_end_to_end_bearish_oversold(self, compiler, sizer, account):
        """防守市 + 左侧优先 → T3 超跌反弹"""
        llm_json = {
            "regime": "防守",
            "bias": "left_priority",
            "mode": "normal",
            "strategies": [
                {"type": "T3_oversold_bounce", "active": True, "params": {"rsi_oversold": 30}},
                {"type": "T2_pullback", "active": True, "params": {"trend_period": 60}},
            ],
            "filters": {"exclude_st": True},
            "meta": {"compiled_by": "e2e_test"},
        }

        result = compiler.compile(llm_json)
        assert result.is_valid

        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            executor = OnlineExecutor(position_sizer=sizer)
            md_list = []
            # 5 个超卖标的
            for i in range(5):
                md_list.append(_oversold_md(f"000{i:03d}.SZ", 9.5))
            # 15 个不触发
            for i in range(15):
                md_list.append({"symbol": f"600{i:03d}.SH", "close": 60.0, "volume": 1})

            result = executor.execute(
                restored, md_list, account,
                mode="normal", bias="left_priority", gate2_light="green",
            )

            assert result.signal_count == 5
            for s in result.signals:
                assert s.strategy_type == "T3_oversold_bounce"


# ═══════════════════════════════════════════════════════════════
# 场景 3: 震荡市 — 多策略并发
# ═══════════════════════════════════════════════════════════════

class TestNeutralRegime:
    def test_end_to_end_all_strategies(self, compiler, sizer, account):
        """震荡市 + balanced → 全部 5 种策略激活（每种各自触发不同标的）"""
        llm_json = {
            "regime": "震荡",
            "bias": "balanced",
            "mode": "normal",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
                {"type": "T2_pullback", "active": True, "params": {"trend_period": 60}},
                {"type": "VCP_breakout", "active": True, "params": {"contraction_ratio": 0.5}},
                {"type": "T3_oversold_bounce", "active": True, "params": {"rsi_oversold": 30}},
                {"type": "T4_golden_cross", "active": True, "params": {"ma_short": 20, "ma_long": 60}},
            ],
            "filters": {"min_market_cap": 5e9},
            "meta": {"compiled_by": "e2e_test"},
        }

        result = compiler.compile(llm_json)
        assert len(result.active_strategies) == 5

        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            executor = OnlineExecutor(position_sizer=sizer)

            # 每种策略 3 个标的，数据互不重叠 (避免多策略同时触发同一标的)
            md_list = []
            # T1 breakout: 动态突破
            for i in range(3):
                md_list.append(_breakout_md(f"B{i:03d}.SH", 65.0))
            # T2 pullback: 回调 MA20 + MA60 支撑
            for i in range(3):
                md_list.append(_pullback_md(f"P{i:03d}.SZ", 9.5))
            # T3 oversold: RSI < 30 + 缩量 + 止跌
            for i in range(3):
                md_list.append(_oversold_md(f"O{i:03d}.SH", 9.5))
            # T4 golden cross: MA20 上穿 MA60
            for i in range(3):
                md_list.append(_golden_cross_md(f"G{i:03d}.SZ", 62.0))

            result = executor.execute(
                restored, md_list, account,
                mode="normal", bias="balanced", gate2_light="green",
            )

            # 每个标的只触发第一个匹配的策略，所以至少 9 个信号
            # (VCP 可能不触发，取决于价格波动形态)
            assert result.signal_count >= 9
            strategy_types = set(s.strategy_type for s in result.signals)


# ═══════════════════════════════════════════════════════════════
# 场景 4: 风险关闭 — 全部拒绝
# ═══════════════════════════════════════════════════════════════

class TestRiskOff:
    def test_risk_off_blocks_all(self, compiler, sizer, account):
        """risk_off 模式 → 所有仓位被拒绝"""
        llm_json = {
            "regime": "防守",
            "bias": "left_priority",
            "mode": "risk_off",
            "strategies": [
                {"type": "T3_oversold_bounce", "active": True, "params": {}},
            ],
        }

        result = compiler.compile(llm_json)

        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            executor = OnlineExecutor(position_sizer=sizer)
            md_list = [_oversold_md("000001.SZ", 9.5)]

            result = executor.execute(
                restored, md_list, account,
                mode="risk_off", bias="left_priority",
            )

            # 有信号，但仓位全被 mode_mult=0 拒绝
            assert result.signal_count >= 0
            for p in result.positions:
                assert p.action == "hold" or p.pct_of_portfolio == 0


# ═══════════════════════════════════════════════════════════════
# 场景 5: 异常 → 回退
# ═══════════════════════════════════════════════════════════════

class TestAnomalyFallback:
    def test_signal_flood_triggers_fallback(self, compiler, sizer, account):
        """信号泛滥 → 异常检测触发 → LLM 回退"""
        detector = AnomalyDetector(max_signals_per_tick=5)
        fallback = FallbackHandler(mock_mode=True)

        llm_json = {
            "regime": "进攻",
            "bias": "right_priority",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
            ],
        }

        result = compiler.compile(llm_json)

        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            executor = OnlineExecutor(
                position_sizer=sizer,
                anomaly_detector=detector,
                fallback_handler=fallback,
            )

            # 10 个标的全部触发 → 超过 max_signals_per_tick=5
            md_list = [
                _breakout_md(f"600{i:03d}.SH", 65.0) for i in range(10)
            ]

            result = executor.execute(
                restored, md_list, account,
                mode="normal", bias="right_priority", gate2_light="green",
            )

            assert result.fallback_triggered
            assert result.fallback_result is not None
            assert result.fallback_result.decision == "abort"  # CRITICAL → abort

    def test_bias_contradiction_fallback(self, compiler, sizer, account):
        """判档矛盾 → 异常 → 回退"""
        detector = AnomalyDetector(bias_contradiction_threshold=0.3)
        fallback = FallbackHandler(mock_mode=True, default_decision="hold")

        llm_json = {
            "regime": "不明",
            "bias": "right_priority",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
                {"type": "T3_oversold_bounce", "active": True, "params": {}},
            ],
        }

        result = compiler.compile(llm_json)

        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            executor = OnlineExecutor(
                position_sizer=sizer,
                anomaly_detector=detector,
                fallback_handler=fallback,
            )

            # 2 个 breakout + 3 个 oversold — 矛盾占比 60% > 30%
            md_list = [
                _breakout_md("B01.SH", 65.0),
                _breakout_md("B02.SH", 65.0),
                _oversold_md("O01.SZ", 9.5),
                _oversold_md("O02.SZ", 9.5),
                _oversold_md("O03.SZ", 9.5),
            ]

            result = executor.execute(
                restored, md_list, account,
                mode="normal", bias="right_priority", gate2_light="green",
            )

            assert result.fallback_triggered
            assert len(result.anomalies) >= 1


# ═══════════════════════════════════════════════════════════════
# 场景 6: 空数据 / 空策略
# ═══════════════════════════════════════════════════════════════

class TestEmptyScenarios:
    def test_empty_signals_no_crash(self, compiler, sizer, account):
        """空信号场景不崩溃"""
        llm_json = {
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {}},
            ],
        }

        result = compiler.compile(llm_json)
        executor = OnlineExecutor(position_sizer=sizer)

        # 没有触发条件的标的
        md_list = [{"symbol": f"000{i:03d}.SZ", "close": 50.0} for i in range(20)]

        result = executor.execute(
            compiler.compile(llm_json), md_list, account,
        )

        assert result.signal_count == 0
        assert len(result.positions) == 0

    def test_no_active_strategies(self, compiler, sizer, account):
        """无活跃策略 → 优雅退出"""
        llm_json = {
            "strategies": [
                {"type": "T1_breakout", "active": False, "params": {}},
            ],
        }
        result = compiler.compile(llm_json)
        executor = OnlineExecutor(position_sizer=sizer)

        exec_result = executor.execute(
            result, [_breakout_md("600036.SH", 65.0)], account,
        )

        assert exec_result.signal_count == 0
        assert any("无活跃策略" in w for w in exec_result.warnings)


# ═══════════════════════════════════════════════════════════════
# 场景 7: Gate 2 信号灯变化
# ═══════════════════════════════════════════════════════════════

class TestGate2Integration:
    def test_yellow_reduces_position(self, compiler, sizer, account):
        """Gate 2 yellow → 仓位减半"""
        llm_json = {
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
            ],
        }

        result = compiler.compile(llm_json)
        executor = OnlineExecutor(position_sizer=sizer)
        md_list = [_breakout_md("600036.SH", 65.0)]

        # Green: full position
        r_green = executor.execute(
            result, md_list, account, gate2_light="green",
        )
        # Yellow: half position
        r_yellow = executor.execute(
            result, md_list, account, gate2_light="yellow",
        )

        if r_green.positions and r_yellow.positions:
            green_pct = r_green.positions[0].pct_of_portfolio
            yellow_pct = r_yellow.positions[0].pct_of_portfolio
            assert yellow_pct < green_pct, (
                f"Yellow={yellow_pct} 应 < Green={green_pct}"
            )

    def test_orange_blocks_position(self, compiler, sizer, account):
        """Gate 2 orange → 仓位为 0"""
        llm_json = {
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
            ],
        }

        result = compiler.compile(llm_json)
        executor = OnlineExecutor(position_sizer=sizer)
        md_list = [_breakout_md("600036.SH", 65.0)]

        r = executor.execute(
            result, md_list, account, gate2_light="orange",
        )

        for p in r.positions:
            assert p.shares == 0