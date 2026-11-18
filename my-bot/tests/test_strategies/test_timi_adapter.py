"""
TiMiAdapter 单元测试 — 验证管线适配器。
"""

import json
import pytest
import sys
import tempfile
from datetime import date
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.timi_adapter import TiMiAdapter, AdapterResult
from strategies.compiler import StrategyCompiler
from strategies.serializer import StrategySerializer


@pytest.fixture
def breakout_md(symbol="600036.SH", close=62.0):
    history = [55 + i * 0.1 for i in range(30)]
    return {
        "symbol": symbol, "close": close,
        "open": close - 0.5, "high": close + 0.5, "low": close - 1.0,
        "volume": 15_000_000, "avg_volume_5d": 10_000_000,
        "prev_close": close - 1.0, "ma_60": 55.0,
        "price_history": history,
    }


def _seed_strategies(storage_dir):
    """预写入策略文件"""
    compiler = StrategyCompiler()
    result = compiler.compile({
        "regime": "进攻",
        "bias": "right_priority",
        "mode": "normal",
        "strategies": [
            {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
            {"type": "T2_pullback", "active": True, "params": {"trend_period": 60}},
        ],
    })
    serializer = StrategySerializer(storage_dir=Path(storage_dir))
    serializer.serialize(result, date_str=date.today().isoformat())
    return result


# ═══════════════════════════════════════════════════════════════
# 正常路径
# ═══════════════════════════════════════════════════════════════

class TestHappyPath:
    def test_run_produces_signals_and_positions(self, breakout_md):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))

            md_list = [breakout_md]
            result = adapter.run(md_list, bias="right_priority", gate2_light="green")

            assert result.success is True
            assert result.source == "timi"
            assert len(result.signals) >= 1
            assert len(result.positions) >= 1
            assert result.latency_ms >= 0

    def test_run_multiple_tickers(self, breakout_md):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))

            md_list = [
                breakout_md,
                {**breakout_md, "symbol": "601318.SH"},
                {**breakout_md, "symbol": "600519.SH"},
            ]
            result = adapter.run(md_list, bias="right_priority", gate2_light="green")

            assert result.success
            assert result.signals  # at least some triggers


# ═══════════════════════════════════════════════════════════════
# JSON 序列化
# ═══════════════════════════════════════════════════════════════

class TestJSONOutput:
    def test_to_json_valid(self, breakout_md):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))

            result = adapter.run([breakout_md], bias="right_priority", gate2_light="green")
            json_str = result.to_json()

            data = json.loads(json_str)
            assert data["success"] is True
            assert data["source"] == "timi"
            assert "signals" in data
            assert "positions" in data

    def test_signal_dict_has_required_fields(self, breakout_md):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))

            result = adapter.run([breakout_md], bias="right_priority", gate2_light="green")
            signal = result.signals[0]

            assert "symbol" in signal
            assert "strategy_type" in signal
            assert "direction" in signal
            assert "confidence" in signal
            assert "trigger_price" in signal

    def test_position_dict_has_required_fields(self, breakout_md):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))

            result = adapter.run([breakout_md], bias="right_priority", gate2_light="green")
            position = result.positions[0]

            assert "symbol" in position
            assert "action" in position
            assert "pct_of_portfolio" in position
            assert "shares" in position
            assert "board" in position


# ═══════════════════════════════════════════════════════════════
# 异常路径
# ═══════════════════════════════════════════════════════════════

class TestFallbackPath:
    def test_no_strategy_file_returns_fallback(self, breakout_md):
        """无策略文件 → 回退 LLM"""
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))
            result = adapter.run([breakout_md])

            assert result.success is False
            assert result.source == "llm_fallback"
            assert len(result.warnings) >= 1

    def test_empty_market_data_returns_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))
            result = adapter.run([])

            assert result.success is False
            assert result.source == "llm_fallback"

    def test_no_active_strategies_returns_fallback(self, breakout_md):
        """所有策略 inactive → 回退"""
        with tempfile.TemporaryDirectory() as tmpdir:
            compiler = StrategyCompiler()
            result = compiler.compile({
                "strategies": [
                    {"type": "T1_breakout", "active": False, "params": {}},
                ],
            })
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str=date.today().isoformat())

            adapter = TiMiAdapter(storage_dir=Path(tmpdir))
            adapter_result = adapter.run([breakout_md])

            assert adapter_result.success is False
            assert adapter_result.source == "llm_fallback"


# ═══════════════════════════════════════════════════════════════
# 适配器配置
# ═══════════════════════════════════════════════════════════════

class TestAdapterConfig:
    def test_custom_storage_dir(self, breakout_md):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter = TiMiAdapter(storage_dir=Path(tmpdir))
            result = adapter.run([breakout_md], bias="right_priority", gate2_light="green")
            assert result.success

    def test_account_value_affects_position(self, breakout_md):
        with tempfile.TemporaryDirectory() as tmpdir:
            _seed_strategies(tmpdir)
            adapter_small = TiMiAdapter(storage_dir=Path(tmpdir))

            r1 = adapter_small.run([breakout_md], account_value=100_000, cash=100_000,
                                   bias="right_priority", gate2_light="green")
            r2 = adapter_small.run([breakout_md], account_value=1_000_000, cash=1_000_000,
                                   bias="right_priority", gate2_light="green")

            if r1.positions and r2.positions:
                assert r2.positions[0]["total_risk_cny"] > r1.positions[0]["total_risk_cny"]


# ═══════════════════════════════════════════════════════════════
# AdapterResult
# ═══════════════════════════════════════════════════════════════

class TestAdapterResult:
    def test_to_dict_roundtrip(self):
        r = AdapterResult(
            success=True, source="timi",
            signals=[{"symbol": "X"}],
            positions=[{"symbol": "X", "action": "buy"}],
            execution_summary={"signal_count": 1},
            warnings=["test"],
            latency_ms=1.5,
        )
        d = r.to_dict()
        assert d["success"] is True
        assert d["signals"][0]["symbol"] == "X"
        assert d["latency_ms"] == 1.5