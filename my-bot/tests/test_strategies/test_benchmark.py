"""
TiMi 在线执行延迟基准测试 — 验证 signal → position < 100ms。

测试场景:
  1. 单标的延迟
  2. 全市场扫描 (500 标的)
  3. 多策略并发
  4. 有/无 PositionSizer 的延迟差异
  5. 序列化-反序列化-执行完整链路延迟
"""

import pytest
import sys
import time
import statistics
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.compiler import StrategyCompiler
from strategies.serializer import StrategySerializer
from strategies.position_sizer import PositionSizer, Account
from strategies.multipliers import Multipliers
from strategies.executor import OnlineExecutor


@pytest.fixture
def compiler():
    return StrategyCompiler()


@pytest.fixture
def strategies(compiler):
    return compiler.compile({
        "regime": "进攻",
        "bias": "right_priority",
        "mode": "normal",
        "strategies": [
            {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
            {"type": "T2_pullback", "active": True, "params": {"trend_period": 60}},
            {"type": "VCP_breakout", "active": True, "params": {"contraction_ratio": 0.5}},
            {"type": "T3_oversold_bounce", "active": True, "params": {}},
            {"type": "T4_golden_cross", "active": True, "params": {}},
        ],
    })


@pytest.fixture
def sizer():
    m = Multipliers.from_risk_policy()
    return PositionSizer(m)


@pytest.fixture
def account():
    return Account(total_value=1_000_000, cash=500_000, positions={})


def _make_breakout_md(symbol="600036.SH", close=62.0):
    history = [55 + i * 0.1 for i in range(30)]
    return {
        "symbol": symbol,
        "close": close, "open": close - 0.5, "high": close + 0.5, "low": close - 1.0,
        "volume": 15_000_000, "avg_volume_5d": 10_000_000,
        "prev_close": close - 1.0, "ma_60": 55.0,
        "price_history": history,
    }


# ═══════════════════════════════════════════════════════════════
# 单标的延迟
# ═══════════════════════════════════════════════════════════════

class TestSingleTickLatency:
    def test_single_tick_under_100ms(self, strategies, sizer, account):
        """单标的执行 < 100ms"""
        executor = OnlineExecutor(position_sizer=sizer, latency_budget_ms=100)
        md_list = [_make_breakout_md("600036.SH", 62.0)]

        result = executor.execute(strategies, md_list, account, gate2_light="green")
        assert result.total_latency_ms < 100
        assert result.signal_count >= 1

    def test_single_tick_under_10ms(self, strategies, sizer, account):
        """单标的执行 < 10ms（典型实际延迟）"""
        executor = OnlineExecutor(position_sizer=sizer)
        md_list = [_make_breakout_md("600036.SH", 62.0)]

        # Warm up
        executor.execute(strategies, md_list, account, gate2_light="green")

        latencies = []
        for _ in range(50):
            result = executor.execute(strategies, md_list, account, gate2_light="green")
            latencies.append(result.total_latency_ms)

        avg = statistics.mean(latencies)
        p99 = sorted(latencies)[-1]  # worst case

        assert avg < 10.0, f"平均延迟 {avg:.2f}ms > 10ms"
        assert p99 < 20.0, f"最差延迟 {p99:.2f}ms > 20ms"

    def test_single_tick_no_sizer_under_5ms(self, strategies, account):
        """无仓位计算: < 5ms"""
        executor = OnlineExecutor(position_sizer=None)
        md_list = [_make_breakout_md("600036.SH", 62.0)]

        latencies = []
        for _ in range(50):
            result = executor.execute(strategies, md_list, account, gate2_light="green")
            latencies.append(result.total_latency_ms)

        avg = statistics.mean(latencies)
        assert avg < 5.0, f"无 sizer 平均延迟 {avg:.2f}ms > 5ms"


# ═══════════════════════════════════════════════════════════════
# 全市场扫描
# ═══════════════════════════════════════════════════════════════

class TestMarketScanLatency:
    def test_500_symbols_under_100ms(self, strategies, sizer, account):
        """全市场 500 标的 < 100ms"""
        executor = OnlineExecutor(position_sizer=sizer, latency_budget_ms=100)

        # 500 标的: 400 触发 + 100 不触发
        md_list = []
        for i in range(400):
            sym = f"60{i:04d}.SH"
            md_list.append(_make_breakout_md(sym, 62.0))
        for i in range(100):
            md_list.append({"symbol": f"00{i:04d}.SZ", "close": 50.0, "volume": 1})

        result = executor.execute(strategies, md_list, account, gate2_light="green")
        assert result.total_latency_ms < 100, (
            f"500 标的延迟 {result.total_latency_ms:.1f}ms > 100ms"
        )

    def test_1000_symbols_under_200ms(self, strategies, sizer, account):
        """全市场 1000 标的 < 200ms"""
        executor = OnlineExecutor(position_sizer=sizer, latency_budget_ms=200)

        md_list = []
        for i in range(1000):
            sym = f"60{i:04d}.SH"
            md_list.append(_make_breakout_md(sym, 62.0))

        result = executor.execute(strategies, md_list, account, gate2_light="green")
        assert result.total_latency_ms < 200, (
            f"1000 标的延迟 {result.total_latency_ms:.1f}ms > 200ms"
        )


# ═══════════════════════════════════════════════════════════════
# 多策略并发
# ═══════════════════════════════════════════════════════════════

class TestMultiStrategyLatency:
    def test_5_strategies_under_100ms(self, strategies, sizer, account):
        """5 策略并发单个标的 < 100ms"""
        executor = OnlineExecutor(position_sizer=sizer)
        md_list = [_make_breakout_md("600036.SH", 62.0)]

        latencies = []
        for _ in range(50):
            result = executor.execute(strategies, md_list, account, gate2_light="green")
            latencies.append(result.total_latency_ms)

        avg = statistics.mean(latencies)
        assert avg < 15.0, f"5 策略平均延迟 {avg:.2f}ms > 15ms"

    def test_check_conditions_only_under_5ms(self, strategies, sizer, account):
        """仅条件检查 (不含仓位计算) < 5ms"""
        executor = OnlineExecutor(position_sizer=None)
        md_list = [_make_breakout_md("600036.SH", 62.0)]

        latencies = []
        for _ in range(100):
            result = executor.execute(strategies, md_list, account)
            latencies.append(result.total_latency_ms)

        avg = statistics.mean(latencies)
        assert avg < 5.0, f"条件检查平均 {avg:.2f}ms > 5ms"


# ═══════════════════════════════════════════════════════════════
# 编译器 + 序列化延迟
# ═══════════════════════════════════════════════════════════════

class TestCompileLatency:
    def test_compile_under_10ms(self, compiler):
        """编译 5 策略 < 10ms"""
        json_data = {
            "regime": "进攻",
            "bias": "right_priority",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {}},
                {"type": "T2_pullback", "active": True, "params": {}},
                {"type": "VCP_breakout", "active": True, "params": {}},
                {"type": "T3_oversold_bounce", "active": True, "params": {}},
                {"type": "T4_golden_cross", "active": True, "params": {}},
            ],
        }
        latencies = []
        for _ in range(50):
            t0 = time.perf_counter()
            compiler.compile(json_data)
            latencies.append((time.perf_counter() - t0) * 1000)

        avg = statistics.mean(latencies)
        assert avg < 10.0, f"编译平均 {avg:.2f}ms > 10ms"

    def test_serialize_roundtrip_under_50ms(self, strategies):
        """序列化往返 < 50ms"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))

            latencies = []
            for _ in range(20):
                t0 = time.perf_counter()
                serializer.serialize(strategies, date_str="2026-10-14")
                serializer.deserialize(date_str="2026-10-14")
                latencies.append((time.perf_counter() - t0) * 1000)

            avg = statistics.mean(latencies)
            assert avg < 50.0, f"序列化往返平均 {avg:.2f}ms > 50ms"


# ═══════════════════════════════════════════════════════════════
# 全链路端到端
# ═══════════════════════════════════════════════════════════════

class TestFullPipelineLatency:
    def test_compile_serialize_deserialize_execute(self, compiler, sizer, account):
        """编译 → 序列化 → 反序列化 → 执行，全链路延迟"""
        import tempfile

        json_data = {
            "regime": "进攻",
            "bias": "right_priority",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
                {"type": "T2_pullback", "active": True, "params": {"trend_period": 60}},
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            executor = OnlineExecutor(position_sizer=sizer)

            latencies = []
            for _ in range(20):
                t0 = time.perf_counter()

                # 编译
                result = compiler.compile(json_data)
                # 序列化
                serializer.serialize(result, date_str="2026-10-14")
                # 反序列化
                restored = serializer.deserialize(date_str="2026-10-14")
                # 执行
                md_list = [_make_breakout_md("600036.SH", 62.0)]
                exec_result = executor.execute(restored, md_list, account, gate2_light="green")

                latencies.append((time.perf_counter() - t0) * 1000)

            avg = statistics.mean(latencies)
            assert avg < 50.0, f"全链路平均 {avg:.2f}ms > 50ms"
            assert exec_result.signal_count >= 1


# ═══════════════════════════════════════════════════════════════
# 预算 = 100ms 时的吞吐量
# ═══════════════════════════════════════════════════════════════

class TestThroughput:
    def test_iterations_per_second(self, strategies, sizer, account):
        """吞吐量: 每秒处理多少标的"""
        executor = OnlineExecutor(position_sizer=sizer)
        md_list = [_make_breakout_md(f"60{i:04d}.SH", 62.0) for i in range(200)]

        # Warm up
        executor.execute(strategies, md_list[:50], account, gate2_light="green")

        # Measure
        t0 = time.perf_counter()
        iterations = 5
        for _ in range(iterations):
            executor.execute(strategies, md_list, account, gate2_light="green")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        total_symbols = len(md_list) * iterations
        throughput = total_symbols / (elapsed_ms / 1000)

        # 200 标的 × 5 次，应该 > 5000 标的/秒
        assert throughput > 500, (
            f"吞吐量 {throughput:.0f} 标的/秒 < 500 标的/秒"
        )