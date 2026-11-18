"""
异常检测器 — 检测信号/仓位异常，触发 LLM 回退。

三种异常:
  1. 信号数量异常: 信号数 > 正常范围 3σ 或 0（数据问题）
  2. 市场状态矛盾: bias=right_priority 但触发大量左侧信号（逻辑矛盾）
  3. 数据完整性问题: Qlib 数据不可用 / akshare 断连 / 数据缺失

异常严重度:
  - CRITICAL: 必须回退 LLM，拒绝执行
  - WARNING: 记录但继续执行 (降级仓位)
  - INFO: 仅通知，不影响执行

用法:
    from strategies.anomaly_detector import AnomalyDetector

    detector = AnomalyDetector(
        max_signals_per_tick=8,
        signal_zero_is_anomaly=True,
    )
    anomalies = detector.check(signals, positions, bias, mode, ...)
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .base import Signal


@dataclass
class Anomaly:
    """异常记录"""
    code: str                       # 异常码
    severity: str                   # "CRITICAL" | "WARNING" | "INFO"
    message: str                    # 人类可读描述
    data: Dict[str, Any] = field(default_factory=dict)
    recommendation: str = ""        # 建议处理方式


class AnomalyDetector:
    """
    异常检测器 — 判断信号/仓位是否正常。

    配置项:
        max_signals_per_tick: 单次执行最大信号数 (超此值触发 CRITICAL)
        signal_zero_is_anomaly: 无信号是否视为异常 (默认 True，仅在 tick 数多时)
        bias_contradiction_threshold: 矛盾信号占比阈值
    """

    def __init__(
        self,
        max_signals_per_tick: int = 8,
        signal_zero_is_anomaly: bool = True,
        bias_contradiction_threshold: float = 0.5,
        min_tick_count_for_zero_check: int = 10,
    ):
        self.max_signals_per_tick = max_signals_per_tick
        self.signal_zero_is_anomaly = signal_zero_is_anomaly
        self.bias_contradiction_threshold = bias_contradiction_threshold
        self.min_tick_count_for_zero_check = min_tick_count_for_zero_check

    def check(
        self,
        signals: List[Signal],
        positions: List,
        bias: str = "balanced",
        mode: str = "normal",
        strategy_count: int = 0,
        data_count: int = 0,
    ) -> List[Anomaly]:
        """
        检测异常。

        Returns:
            异常列表 (空 = 无异常)。按严重度排序: CRITICAL → WARNING → INFO
        """
        anomalies: List[Anomaly] = []

        # ── 1. 信号数量异常 ──
        anomalies.extend(self._check_signal_count(signals, data_count))

        # ── 2. 市场状态矛盾 ──
        anomalies.extend(self._check_bias_contradiction(signals, bias, strategy_count))

        # ── 3. 数据完整性 ──
        anomalies.extend(self._check_data_completeness(signals, positions, data_count))

        # 按严重度排序
        severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        anomalies.sort(key=lambda a: severity_order.get(a.severity, 99))

        return anomalies

    def _check_signal_count(
        self, signals: List[Signal], data_count: int
    ) -> List[Anomaly]:
        """检查信号数量是否异常"""
        anomalies = []
        signal_count = len(signals)

        # 信号过多 — 可能是数据问题或策略过于激进
        if signal_count > self.max_signals_per_tick:
            anomalies.append(Anomaly(
                code="SIGNAL_FLOOD",
                severity="CRITICAL",
                message=f"信号数异常过多: {signal_count} > {self.max_signals_per_tick}",
                data={
                    "signal_count": signal_count,
                    "max_limit": self.max_signals_per_tick,
                },
                recommendation="触发 LLM 回退，检查数据源和策略编译结果",
            ))

        # 信号为零 — 可能是数据源断连
        if (self.signal_zero_is_anomaly
                and signal_count == 0
                and data_count >= self.min_tick_count_for_zero_check):
            anomalies.append(Anomaly(
                code="SIGNAL_DROUGHT",
                severity="WARNING",
                message=f"零信号: {data_count} 个标的中无任何策略触发",
                data={
                    "data_count": data_count,
                    "signal_count": 0,
                },
                recommendation="检查数据源可用性；可能市场状态极端偏防守，确认后再执行",
            ))

        return anomalies

    def _check_bias_contradiction(
        self, signals: List[Signal], bias: str, strategy_count: int
    ) -> List[Anomaly]:
        """检查 bias 与信号类型是否矛盾"""
        anomalies = []

        if bias == "balanced" or not signals:
            return anomalies

        # 定义各 bias 下的矛盾信号类型
        contradiction_map = {
            "right_priority": {"T3_oversold_bounce"},
            "left_priority": {"T1_breakout", "T4_golden_cross", "VCP_breakout"},
        }

        contradictory_types = contradiction_map.get(bias, set())
        if not contradictory_types:
            return anomalies

        # 统计矛盾信号
        contradictory_signals = [
            s for s in signals if s.strategy_type in contradictory_types
        ]
        contradiction_ratio = len(contradictory_signals) / max(len(signals), 1)

        if contradiction_ratio >= self.bias_contradiction_threshold:
            anomalies.append(Anomaly(
                code="BIAS_CONTRADICTION",
                severity="WARNING",
                message=(
                    f"市场判档为 {bias}，但 {contradiction_ratio:.0%} 的信号来自矛盾策略: "
                    f"{[s.strategy_type for s in contradictory_signals]}"
                ),
                data={
                    "bias": bias,
                    "contradiction_ratio": contradiction_ratio,
                    "contradictory_count": len(contradictory_signals),
                    "total_signals": len(signals),
                },
                recommendation="市场判档可能过时，建议重跑 market-watcher；或降级为 balanced",
            ))

        return anomalies

    def _check_data_completeness(
        self, signals: List, positions: List, data_count: int
    ) -> List[Anomaly]:
        """检查数据完整性"""
        anomalies = []

        # 有数据但零仓位 (可能是 position_sizer 全部 rejected)
        if data_count > 0 and signals and not positions:
            anomalies.append(Anomaly(
                code="ALL_REJECTED",
                severity="INFO",
                message=f"{len(signals)} 个信号通过但全部被 position_sizer 拒绝",
                data={
                    "signal_count": len(signals),
                    "position_count": 0,
                },
                recommendation="检查 Gate 2 / 模式乘数 是否过于严格",
            ))

        return anomalies

    @property
    def is_healthy(self) -> bool:
        """便捷属性: 上一次检查是否健康 (子类使用)"""
        return True