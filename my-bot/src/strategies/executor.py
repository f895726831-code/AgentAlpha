"""
在线执行框架 — 从加载策略到输出仓位，全链路 < 100ms。

TiMi 在线执行流程:
  1. load_strategies(date) → Dict[str, BaseStrategy]
  2. For each market_data tick:
     a. For each active strategy:
        - check_conditions(market_data)
        - if True → generate_signal(market_data)
     b. For each signal:
        - PositionSizer.calculate(signal, ...)
     c. AnomalyDetector.check(signals, positions)
        - if anomaly → FallbackHandler.invoke()
  3. Return: ExecutionResult

用法:
    from strategies.executor import OnlineExecutor
    from strategies.serializer import StrategySerializer
    from strategies.position_sizer import PositionSizer

    serializer = StrategySerializer()
    strategies = serializer.get_latest()

    executor = OnlineExecutor(position_sizer=PositionSizer(...))
    result = executor.execute(
        strategies=strategies,
        market_data_list=[{"symbol": "600036.SH", "close": 62.0, ...}],
        account=Account(total_value=500000, cash=500000),
        mode="normal",
        bias="right_priority",
    )
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import time

from .base import BaseStrategy, Signal
from .compiler import CompileResult
from .position_sizer import PositionSizer, Position, Account


@dataclass
class TickResult:
    """单标的执行结果"""
    symbol: str
    signal: Optional[Signal] = None
    position: Optional[Position] = None
    strategy_type: str = ""
    latency_ms: float = 0.0
    skipped: bool = False
    skip_reason: str = ""


@dataclass
class ExecutionResult:
    """完整执行结果"""
    tick_results: List[TickResult] = field(default_factory=list)
    signals: List[Signal] = field(default_factory=list)
    positions: List[Position] = field(default_factory=list)
    total_latency_ms: float = 0.0
    anomalies: List[Dict] = field(default_factory=list)
    fallback_triggered: bool = False
    fallback_result: Optional[Dict] = None
    warnings: List[str] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def signal_count(self) -> int:
        return len(self.signals)

    @property
    def buy_positions(self) -> List[Position]:
        return [p for p in self.positions if p.action == "buy"]

    @property
    def has_anomaly(self) -> bool:
        return len(self.anomalies) > 0


class OnlineExecutor:
    """
    在线执行器 — 策略模板 → 信号 → 仓位，全链路确定性执行。

    设计原则:
      - 纯 Python 计算，无 LLM 调用 → < 100ms
      - 所有参数从 risk-policy.json 加载，无硬编码
      - PIT 安全: 仅使用当日及之前的数据
      - 异常检测集成: 异常 → 跳过本次信号 → 标记 anomaly
    """

    def __init__(
        self,
        position_sizer: PositionSizer = None,
        anomaly_detector=None,
        fallback_handler=None,
        latency_budget_ms: float = 100.0,
    ):
        """
        Args:
            position_sizer: 仓位计算器
            anomaly_detector: 异常检测器 (可选)
            fallback_handler: LLM 回退处理器 (可选)
            latency_budget_ms: 延迟预算 (ms)，超时记录警告
        """
        self.position_sizer = position_sizer
        self.anomaly_detector = anomaly_detector
        self.fallback_handler = fallback_handler
        self.latency_budget_ms = latency_budget_ms

    def execute(
        self,
        strategies: CompileResult,
        market_data_list: List[Dict[str, Any]],
        account: Account,
        mode: str = "normal",
        bias: str = "balanced",
        gate2_light: str = "green",
        crowding_score: int = 0,
    ) -> ExecutionResult:
        """
        执行完整交易决策流程。

        Args:
            strategies: 编译后的策略结果 (含 active_strategies)
            market_data_list: 市场数据列表 (每个元素对应一个标的)
            account: 账户状态
            mode: 运行模式
            bias: 策略偏好
            gate2_light: Gate 2 信号灯
            crowding_score: 拥挤度评分 (0-10)

        Returns:
            ExecutionResult (signals + positions + anomalies)
        """
        t0 = time.perf_counter()
        warnings: List[str] = []

        # ── 获取活跃策略 ──
        active = strategies.active_strategies
        if not active:
            warnings.append("无活跃策略 — 执行终止")
            return ExecutionResult(
                signals=[], positions=[], warnings=warnings,
                total_latency_ms=(time.perf_counter() - t0) * 1000,
            )

        # ── 逐标执行 ──
        tick_results: List[TickResult] = []
        all_signals: List[Signal] = []
        all_positions: List[Position] = []

        for md in market_data_list:
            symbol = str(md.get("symbol", ""))
            t_tick = time.perf_counter()

            tick_result = self._process_tick(
                md, symbol, active, account, mode, bias, gate2_light, crowding_score
            )
            tick_results.append(tick_result)

            if tick_result.signal:
                all_signals.append(tick_result.signal)
                if tick_result.position:
                    all_positions.append(tick_result.position)

        total_latency = (time.perf_counter() - t0) * 1000

        # ── 异常检测 ──
        anomalies = []
        fallback_triggered = False
        fallback_result = None

        if self.anomaly_detector is not None:
            anomalies = self.anomaly_detector.check(
                signals=all_signals,
                positions=all_positions,
                bias=bias,
                mode=mode,
                strategy_count=len(active),
                data_count=len(market_data_list),
            )

            if anomalies and self.fallback_handler is not None:
                fallback_triggered = True
                fallback_result = self.fallback_handler.invoke(
                    anomalies=anomalies,
                    context={
                        "signals": [s.to_dict() for s in all_signals],
                        "positions": [p.__dict__ for p in all_positions],
                        "bias": bias,
                        "mode": mode,
                        "tick_count": len(market_data_list),
                    },
                )

        # ── 延迟检查 ──
        if total_latency > self.latency_budget_ms:
            warnings.append(
                f"延迟超标: {total_latency:.1f}ms > 预算 {self.latency_budget_ms}ms"
            )

        return ExecutionResult(
            tick_results=tick_results,
            signals=all_signals,
            positions=all_positions,
            total_latency_ms=total_latency,
            anomalies=anomalies,
            fallback_triggered=fallback_triggered,
            fallback_result=fallback_result,
            warnings=warnings,
            meta={
                "strategy_count": len(active),
                "tick_count": len(market_data_list),
                "mode": mode,
                "bias": bias,
                "gate2_light": gate2_light,
                "crowding_score": crowding_score,
            },
        )

    def _process_tick(
        self,
        md: Dict[str, Any],
        symbol: str,
        active: Dict[str, BaseStrategy],
        account: Account,
        mode: str,
        bias: str,
        gate2_light: str,
        crowding_score: int,
    ) -> TickResult:
        """处理单个标的: 策略匹配 → 信号 → 仓位"""
        t0 = time.perf_counter()

        # 运行所有活跃策略
        for stype, strategy in active.items():
            try:
                if not strategy.check_conditions(md):
                    continue

                signal = strategy.generate_signal(md)
                if signal is None:
                    continue

                # 仓位计算
                if self.position_sizer is not None:
                    position = self.position_sizer.calculate(
                        signal=signal,
                        account_value=account.total_value,
                        mode=mode,
                        bias=bias,
                        gate2_light=gate2_light,
                        crowding_bear_score=crowding_score,
                    )
                else:
                    position = None

                latency = (time.perf_counter() - t0) * 1000
                return TickResult(
                    symbol=symbol,
                    signal=signal,
                    position=position,
                    strategy_type=stype,
                    latency_ms=latency,
                )

            except Exception as e:
                # 单个策略异常不中断整体流程
                return TickResult(
                    symbol=symbol,
                    skipped=True,
                    skip_reason=f"{stype}: {e}",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                )

        # 无匹配信号
        return TickResult(
            symbol=symbol,
            skipped=True,
            skip_reason="no_strategy_match",
            latency_ms=(time.perf_counter() - t0) * 1000,
        )

    def execute_single(
        self,
        strategies: CompileResult,
        market_data: Dict[str, Any],
        account: Account,
        **kwargs,
    ) -> TickResult:
        """便捷方法: 单标的快速执行"""
        results = self.execute(
            strategies=strategies,
            market_data_list=[market_data],
            account=account,
            **kwargs,
        )
        if results.tick_results:
            return results.tick_results[0]
        return TickResult(symbol=str(market_data.get("symbol", "")))