"""
OnlineExecutor 单元测试 — 验证在线执行框架。
"""

import pytest
import sys
from pathlib import Path

MY_BOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(MY_BOT / "src"))

from strategies.executor import OnlineExecutor, ExecutionResult, TickResult
from strategies.compiler import StrategyCompiler
from strategies.position_sizer import PositionSizer, Account
from strategies.multipliers import Multipliers
from strategies.base import Signal


@pytest.fixture
def compiler():
    return StrategyCompiler()


@pytest.fixture
def active_strategies(compiler):
    return compiler.compile({
        "regime": "进攻",
        "bias": "right_priority",
        "mode": "normal",
        "strategies": [
            {"type": "T1_breakout", "active": True, "params": {"lookback_days": 30}},
            {"type": "T2_pullback", "active": True, "params": {"trend_period": 60}},
        ],
    })


@pytest.fixture
def sizer():
    m = Multipliers.from_risk_policy()
    return PositionSizer(m)


@pytest.fixture
def account():
    return Account(total_value=500000, cash=500000, positions={})


@pytest.fixture
def executor(sizer):
    return OnlineExecutor(position_sizer=sizer)


def _breakout_md(symbol="600036.SH", close=62.0):
    """构造触发 T1 breakout 的市场数据"""
    history = [55 + i * 0.1 for i in range(30)]
    return {
        "symbol": symbol,
        "close": close,
        "open": close - 0.5,
        "high": close + 0.5,
        "low": close - 1.0,
        "volume": 15_000_000,
        "avg_volume_5d": 10_000_000,
        "prev_close": close - 1.0,
        "ma_60": 55.0,
        "price_history": history,
    }


# ═══════════════════════════════════════════════════════════════
# 基本执行
# ═══════════════════════════════════════════════════════════════

class TestBasicExecution:
    def test_execute_produces_signals(self, executor, active_strategies, account):
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account,
                                  mode="normal", bias="right_priority", gate2_light="green")
        assert result.signal_count == 1
        assert result.signals[0].symbol == "600036.SH"
        assert result.signals[0].strategy_type == "T1_breakout"

    def test_execute_no_match_returns_empty(self, executor, active_strategies, account):
        """无标的触发任何策略 → 空信号"""
        md_list = [{"symbol": "000001.SZ", "close": 50.0, "volume": 1}]
        result = executor.execute(active_strategies, md_list, account)
        assert result.signal_count == 0

    def test_multiple_symbols(self, executor, active_strategies, account):
        """多标的执行"""
        md_list = [
            _breakout_md("600036.SH", 62.0),
            _breakout_md("601318.SH", 62.0),
        ]
        result = executor.execute(active_strategies, md_list, account,
                                  gate2_light="green")
        assert result.signal_count == 2
        assert len(result.tick_results) == 2

    def test_active_strategies_only(self, executor, active_strategies, account):
        """不活跃策略不产生信号"""
        # 编译时 T2_pullback active=True，但 breakout_md 不触发 pullback
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account, gate2_light="green")
        # 只有 T1 触发
        triggered_types = [s.strategy_type for s in result.signals]
        assert "T1_breakout" in triggered_types
        assert "T2_pullback" not in triggered_types


# ═══════════════════════════════════════════════════════════════
# 仓位输出
# ═══════════════════════════════════════════════════════════════

class TestPositionOutput:
    def test_execute_produces_positions(self, executor, active_strategies, account):
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account,
                                  mode="normal", bias="right_priority", gate2_light="green")
        assert len(result.positions) == 1
        assert result.positions[0].action == "buy"

    def test_execute_without_sizer(self, active_strategies, account):
        """无 PositionSizer: 有信号但无仓位"""
        executor = OnlineExecutor(position_sizer=None)
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account,
                                  gate2_light="green")
        assert result.signal_count == 1
        assert len(result.positions) == 0

    def test_execute_single_convenience(self, executor, active_strategies, account):
        """execute_single 便捷方法"""
        md = _breakout_md("600036.SH", 62.0)
        tick = executor.execute_single(active_strategies, md, account,
                                       gate2_light="green")
        assert tick.signal is not None
        assert tick.symbol == "600036.SH"


# ═══════════════════════════════════════════════════════════════
# 延迟
# ═══════════════════════════════════════════════════════════════

class TestLatency:
    def test_total_latency_tracked(self, executor, active_strategies, account):
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account, gate2_light="green")
        assert result.total_latency_ms >= 0
        assert result.total_latency_ms < 50  # 纯 Python 应远低于 100ms

    def test_per_tick_latency(self, executor, active_strategies, account):
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account, gate2_light="green")
        for tick in result.tick_results:
            assert tick.latency_ms >= 0

    def test_latency_budget_warning(self, executor, active_strategies, account):
        """延迟预算设为 0 → 必然触发警告"""
        executor.latency_budget_ms = 0.0
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account, gate2_light="green")
        assert any("延迟超标" in w for w in result.warnings)


# ═══════════════════════════════════════════════════════════════
# ExecutionResult 属性
# ═══════════════════════════════════════════════════════════════

class TestExecutionResult:
    def test_buy_positions_filter(self):
        from strategies.position_sizer import Position
        p1 = Position(symbol="A", action="buy", pct_of_portfolio=5.0, shares=100,
                      max_shares=100, risk_per_share=1.0, total_risk_cny=500,
                      board="main_board", multipliers={}, warnings=[])
        p2 = Position(symbol="B", action="hold", pct_of_portfolio=0, shares=0,
                      max_shares=0, risk_per_share=0, total_risk_cny=0,
                      board="main_board", multipliers={}, warnings=[])
        result = ExecutionResult(positions=[p1, p2], signals=[])
        assert len(result.buy_positions) == 1
        assert result.buy_positions[0].symbol == "A"

    def test_signal_count_property(self):
        s = Signal(symbol="TEST", strategy_type="T1_breakout", confidence=0.7,
                   trigger_price=60.0, stop_loss=57.0)
        result = ExecutionResult(signals=[s])
        assert result.signal_count == 1

    def test_meta_has_context(self, executor, active_strategies, account):
        md_list = [_breakout_md("600036.SH", 62.0)]
        result = executor.execute(active_strategies, md_list, account,
                                  mode="earnings_season", bias="right_priority",
                                  gate2_light="green", crowding_score=5)
        assert result.meta["mode"] == "earnings_season"
        assert result.meta["bias"] == "right_priority"
        assert result.meta["gate2_light"] == "green"
        assert result.meta["crowding_score"] == 5


# ═══════════════════════════════════════════════════════════════
# 边缘情况
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_market_data(self, executor, active_strategies, account):
        result = executor.execute(active_strategies, [], account)
        assert result.signal_count == 0
        assert len(result.positions) == 0

    def test_empty_strategies(self, executor, account):
        compiler = StrategyCompiler()
        no_active = compiler.compile({
            "strategies": [
                {"type": "T1_breakout", "active": False, "params": {}},
            ],
        })
        result = executor.execute(no_active, [_breakout_md()], account)
        assert result.signal_count == 0
        assert any("无活跃策略" in w for w in result.warnings)

    def test_data_missing_keywords(self, executor, active_strategies, account):
        """市场数据缺字段 — 不崩溃"""
        incomplete_md = {"symbol": "000001.SZ", "close": 50.0}
        md_list = [incomplete_md]
        result = executor.execute(active_strategies, md_list, account)
        assert result is not None
        # 数据不足 → 不触发信号
        assert result.signal_count == 0

    def test_no_none_signal_stored(self, executor, active_strategies, account):
        """确保 None 信号不被收集"""
        md_list = [{"symbol": "000001.SZ", "close": 50.0, "volume": 1}]
        result = executor.execute(active_strategies, md_list, account)
        assert result.signals == []
        assert result.signal_count == 0

    def test_strategy_exception_does_not_crash(self, executor, active_strategies, account):
        """单个策略异常不中断执行"""
        # 构造会导致除零的数据但 executor 应优雅处理
        bad_md = {"symbol": "000001.SZ", "close": 0, "open": 0, "volume": 0}
        md_list = [_breakout_md("600036.SH", 62.0), bad_md]
        result = executor.execute(active_strategies, md_list, account, gate2_light="green")
        # 600036 仍然应触发
        triggered = [t.symbol for t in result.tick_results if t.signal]
        assert "600036.SH" in triggered