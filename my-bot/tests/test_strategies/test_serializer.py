"""
StrategySerializer 单元测试 — 序列化往返 + 文件持久化。
"""

import json
import pytest
import sys
import tempfile
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.compiler import StrategyCompiler, CompileResult
from strategies.serializer import StrategySerializer


@pytest.fixture
def compiler():
    return StrategyCompiler()


@pytest.fixture
def compiled_result(compiler):
    return compiler.compile({
        "regime": "进攻",
        "bias": "right_priority",
        "mode": "normal",
        "strategies": [
            {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
            {"type": "T2_pullback", "active": True, "params": {"trend_period": 60}},
            {"type": "VCP_breakout", "active": True, "params": {"contraction_ratio": 0.5}},
        ],
        "filters": {"exclude_st": True},
        "meta": {"compiled_by": "test"},
    })


# ═══════════════════════════════════════════════════════════════
# 序列化 / 反序列化
# ═══════════════════════════════════════════════════════════════

class TestSerializeDeserialize:
    def test_roundtrip_preserves_strategies(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            path = serializer.serialize(compiled_result, date_str="2026-10-14")
            assert path.exists()

            restored = serializer.deserialize(date_str="2026-10-14")
            assert restored.is_valid
            assert len(restored.strategies) == 3
            assert set(restored.strategies.keys()) == {"T1_breakout", "T2_pullback", "VCP_breakout"}

    def test_roundtrip_preserves_regime_bias_mode(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(compiled_result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            assert restored.regime == "进攻"
            assert restored.bias == "right_priority"
            assert restored.mode == "normal"

    def test_roundtrip_preserves_filters(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(compiled_result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            assert restored.filters["exclude_st"] is True

    def test_roundtrip_preserves_strategy_params(self, compiled_result):
        """参数在序列化往返后无损"""
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(compiled_result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            breakout = restored.strategies["T1_breakout"]
            assert breakout._get_filter("lookback_days") == 30
            assert breakout.params.active is True
            assert breakout.params.mode == "normal"
            assert breakout.params.bias == "right_priority"

    def test_roundtrip_inactive_strategy_preserved(self, compiler):
        """不活跃策略也应被保留 (以便后续激活)"""
        result = compiler.compile({
            "strategies": [
                {"type": "T1_breakout", "active": False, "params": {"lookback_days": 20}},
            ],
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")

            assert "T1_breakout" in restored.strategies
            assert restored.strategies["T1_breakout"].params.active is False
            assert restored.strategies["T1_breakout"]._get_filter("lookback_days") == 20

    def test_roundtrip_warnings_preserved(self, compiler):
        result = compiler.compile({
            "strategies": [{"type": "T1_breakout", "active": True, "params": {"lookback_days": 5}}],
        })
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")
            restored = serializer.deserialize(date_str="2026-10-14")
            assert len(restored.warnings) >= 1


# ═══════════════════════════════════════════════════════════════
# 文件管理
# ═══════════════════════════════════════════════════════════════

class TestFileManagement:
    def test_custom_path_serialize(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            custom_path = Path(tmpdir) / "custom" / "strategies.json"
            serializer = StrategySerializer()
            path = serializer.serialize(compiled_result, path=custom_path)
            assert path == custom_path
            assert custom_path.exists()

    def test_list_available(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(compiled_result, date_str="2026-10-14")
            serializer.serialize(compiled_result, date_str="2026-10-13")
            serializer.serialize(compiled_result, date_str="2026-10-12")

            dates = serializer.list_available()
            assert len(dates) == 3
            assert dates[0] == "2026-10-14"  # newest first

    def test_get_latest(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            # Save older first
            serializer.serialize(compiled_result, date_str="2026-10-13")

            # Create a different config for newest
            from strategies.compiler import StrategyCompiler
            cmp = StrategyCompiler()
            newer = cmp.compile({
                "regime": "防守",
                "bias": "left_priority",
                "strategies": [
                    {"type": "T2_pullback", "active": True, "params": {"trend_period": 120}},
                ],
            })
            serializer.serialize(newer, date_str="2026-10-14")

            latest = serializer.get_latest(max_age_days=-1)
            assert latest is not None
            assert latest.regime == "防守"
            assert latest.bias == "left_priority"

    def test_get_latest_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            assert serializer.get_latest() is None
            assert serializer.list_available() == []

    def test_missing_file_raises(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            with pytest.raises(FileNotFoundError):
                serializer.deserialize(date_str="2099-01-01")


# ═══════════════════════════════════════════════════════════════
# JSON 格式验证
# ═══════════════════════════════════════════════════════════════

class TestJSONFormat:
    def test_json_is_valid_utf8(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            path = serializer.serialize(compiled_result, date_str="2026-10-14")

            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            assert data["version"] == "1.0"
            assert "compiled_at" in data
            assert "strategies" in data
            assert "T1_breakout" in data["strategies"]

    def test_strategy_params_structure(self, compiled_result):
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            path = serializer.serialize(compiled_result, date_str="2026-10-14")

            with open(path, "r") as f:
                data = json.load(f)

            t1 = data["strategies"]["T1_breakout"]
            assert t1["active"] is True
            assert t1["params"]["lookback_days"] == 30
            assert "confidence_threshold" in t1

    def test_readable_json(self, compiled_result):
        """JSON 应是人类可读的 (有缩进)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            path = serializer.serialize(compiled_result, date_str="2026-10-14")

            content = path.read_text(encoding="utf-8")
            assert "  " in content  # indentation
            lines = content.split("\n")
            assert len(lines) > 10  # not minified


# ═══════════════════════════════════════════════════════════════
# 集成: compile → serialize → deserialize → execute
# ═══════════════════════════════════════════════════════════════

class TestIntegration:
    def test_full_cycle(self):
        """完整生命周期: compile → serialize → deserialize → use strategy"""
        compiler = StrategyCompiler()

        # Step 1: 模拟 LLM 编译
        llm_json = {
            "regime": "进攻",
            "bias": "right_priority",
            "mode": "normal",
            "strategies": [
                {"type": "T1_breakout", "active": True, "params": {
                    "lookback_days": 30, "volume_ratio_min": 1.5,
                    "stop_loss_pct": 5.0, "take_profit_pct": 12.0,
                    "gap_max_pct": 5.0,  # allow wider gap for this test
                }},
            ],
            "meta": {"compiled_by": "deepseek-v3"},
        }
        result = compiler.compile(llm_json)
        assert result.is_valid

        # Step 2: 序列化
        with tempfile.TemporaryDirectory() as tmpdir:
            serializer = StrategySerializer(storage_dir=Path(tmpdir))
            serializer.serialize(result, date_str="2026-10-14")

            # Step 3: 反序列化
            restored = serializer.deserialize(date_str="2026-10-14")

            # Step 4: 使用策略
            strategy = restored.active_strategies["T1_breakout"]
            assert strategy.strategy_type == "T1_breakout"

            # 构造市场数据 (模拟在线执行)
            history = [55 + i * 0.1 for i in range(30)]  # 30 天盘整
            market_data = {
                "symbol": "600036.SH",
                "close": 62.0,
                "open": 61.5,
                "high": 62.5,
                "low": 61.0,
                "volume": 15_000_000,
                "avg_volume_5d": 10_000_000,
                "prev_close": 60.0,
                "ma_60": 55.0,
                "price_history": history,
            }

            conditions_met = strategy.check_conditions(market_data)
            assert conditions_met is True

            signal = strategy.generate_signal(market_data)
            assert signal is not None
            assert signal.trigger_price == 62.0
            assert signal.strategy_type == "T1_breakout"