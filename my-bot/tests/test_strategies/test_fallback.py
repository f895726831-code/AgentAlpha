"""
FallbackHandler 单元测试 — 验证异常回退机制。
"""

import json
import pytest
import sys
import tempfile
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.fallback import FallbackHandler, FallbackResult
from strategies.anomaly_detector import Anomaly


@pytest.fixture
def mock_anomalies():
    return [
        Anomaly(
            code="SIGNAL_FLOOD",
            severity="CRITICAL",
            message="信号数异常过多: 15 > 8",
            data={"signal_count": 15, "max_limit": 8},
            recommendation="触发 LLM 回退",
        ),
    ]


@pytest.fixture
def warning_anomalies():
    return [
        Anomaly(
            code="BIAS_CONTRADICTION",
            severity="WARNING",
            message="市场判档为 right_priority，矛盾信号占比 60%",
            data={"bias": "right_priority", "contradiction_ratio": 0.6},
            recommendation="重跑 market-watcher",
        ),
    ]


@pytest.fixture
def context():
    return {
        "signals": [{"symbol": "600036.SH", "strategy_type": "T1_breakout"}],
        "positions": [{"symbol": "600036.SH", "action": "buy", "pct": 5.0}],
        "bias": "right_priority",
        "mode": "normal",
        "tick_count": 50,
    }


# ═══════════════════════════════════════════════════════════════
# Mock 模式
# ═══════════════════════════════════════════════════════════════

class TestMockMode:
    def test_critical_aborts(self, mock_anomalies, context):
        """CRITICAL 异常 → abort"""
        handler = FallbackHandler(mock_mode=True)
        result = handler.invoke(mock_anomalies, context)
        assert result.decision == "abort"
        assert result.source == "mock"

    def test_warning_holds(self, warning_anomalies, context):
        """WARNING 异常 → hold (默认)"""
        handler = FallbackHandler(mock_mode=True, default_decision="hold")
        result = handler.invoke(warning_anomalies, context)
        assert result.decision == "hold"
        assert result.source == "mock"

    def test_warning_custom_default(self, warning_anomalies, context):
        """自定义默认决策"""
        handler = FallbackHandler(mock_mode=True, default_decision="execute")
        result = handler.invoke(warning_anomalies, context)
        assert result.decision == "execute"

    def test_mixed_severity_critical_wins(self, context):
        """混合严重度 → CRITICAL 决定"""
        anomalies = [
            Anomaly(code="W1", severity="WARNING", message="w"),
            Anomaly(code="C1", severity="CRITICAL", message="c"),
        ]
        handler = FallbackHandler(mock_mode=True)
        result = handler.invoke(anomalies, context)
        assert result.decision == "abort"

    def test_force_llm_ignores_mock(self, mock_anomalies, context):
        """force_llm=True → 跳过 mock，走真实 LLM 路径"""
        handler = FallbackHandler(mock_mode=True)
        result = handler.invoke(mock_anomalies, context, force_llm=True)
        assert result.source == "llm_fallback"
        # v2.3: 真实 LLM 回退已实现。CRITICAL 异常 → abort，API 不可用 → hold
        assert result.decision in ("abort", "hold")


# ═══════════════════════════════════════════════════════════════
# 异常缓存
# ═══════════════════════════════════════════════════════════════

class TestAnomalyCache:
    def test_caches_anomaly_data(self, mock_anomalies, context):
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = FallbackHandler(cache_dir=Path(tmpdir), mock_mode=True)
            result = handler.invoke(mock_anomalies, context)

            assert result.cached_anomaly_id != ""

            # 验证缓存文件存在
            cache_files = list(Path(tmpdir).glob("*.json"))
            assert len(cache_files) == 1

            # 验证内容
            with open(cache_files[0], "r", encoding="utf-8") as f:
                cached = json.load(f)

            assert cached["anomalies"][0]["code"] == "SIGNAL_FLOOD"
            assert cached["context"]["bias"] == "right_priority"

    def test_load_anomaly(self, mock_anomalies, context):
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = FallbackHandler(cache_dir=Path(tmpdir), mock_mode=True)
            result = handler.invoke(mock_anomalies, context)

            loaded = handler.load_anomaly(result.cached_anomaly_id)
            assert loaded is not None
            assert loaded["anomaly_id"] == result.cached_anomaly_id

    def test_load_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = FallbackHandler(cache_dir=Path(tmpdir))
            assert handler.load_anomaly("nonexistent_id") is None

    def test_cache_cleanup(self, mock_anomalies, context):
        """旧缓存超过 MAX_CACHE_FILES 时自动清理"""
        with tempfile.TemporaryDirectory() as tmpdir:
            handler = FallbackHandler(cache_dir=Path(tmpdir), mock_mode=True)
            handler.MAX_CACHE_FILES = 3

            for _ in range(5):
                handler.invoke(mock_anomalies, context)

            cache_files = list(Path(tmpdir).glob("*.json"))
            assert len(cache_files) <= 3


# ═══════════════════════════════════════════════════════════════
# FallbackResult 数据类
# ═══════════════════════════════════════════════════════════════

class TestFallbackResult:
    def test_fields_set_correctly(self):
        r = FallbackResult(
            decision="hold",
            signals=[{"symbol": "X"}],
            positions=[{"symbol": "X", "pct": 5}],
            reasoning="test reason",
            source="mock",
            cached_anomaly_id="abc123",
        )
        assert r.decision == "hold"
        assert len(r.signals) == 1
        assert r.source == "mock"
        assert r.cached_anomaly_id == "abc123"


# ═══════════════════════════════════════════════════════════════
# 边缘情况
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_anomalies(self, context):
        handler = FallbackHandler(mock_mode=True)
        result = handler.invoke([], context)
        assert result.decision == "hold"
        assert result.source == "mock"

    def test_empty_context(self):
        handler = FallbackHandler(mock_mode=True)
        result = handler.invoke([Anomaly(code="T", severity="WARNING", message="m")], {})
        assert result is not None
        assert result.decision == "hold"

    def test_cache_failure_non_blocking(self, mock_anomalies, context):
        """缓存写入失败不阻塞主流程"""
        # 使用无效路径
        handler = FallbackHandler(
            cache_dir=Path("Z:/nonexistent_drive/anomalies"),
            mock_mode=True,
        )
        result = handler.invoke(mock_anomalies, context)
        # 应该正常返回，只是没有缓存 ID
        assert result is not None
        assert result.decision in ("abort", "hold", "execute")

    def test_llm_fallback_real_path(self, mock_anomalies, context):
        """v2.3: 真实 LLM 回退路径 — API 不可用时降级到 mock hold"""
        handler = FallbackHandler(mock_mode=False)
        result = handler.invoke(mock_anomalies, context)
        assert result.source == "llm_fallback"
        # 无 API Key 时 LLM 调用失败 → 降级 hold；有 API Key 时 CRITICAL → abort
        assert result.decision in ("abort", "hold")