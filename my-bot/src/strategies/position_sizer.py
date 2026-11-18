"""
仓位计算器 — 基于 risk-policy.json 的规则化仓位计算。

当前 risk-policy.json 的 Fractional-Kelly 0.25 只有声明没有实现代码。
本模块是 v4.3 中首次实现确定性仓位计算。

公式:
    base_pct = kelly_fraction(edge, win_rate) × 0.25
    final_pct = base_pct × mode_mult × bias_mult × gate2_mult
    upper_bound = min(final_pct, concentration_limit[board])
    拥挤超驰: crowding_bear >= 4 → upper_bound × 0.5

用法:
    from strategies.position_sizer import PositionSizer
    from strategies.multipliers import Multipliers

    m = Multipliers.from_risk_policy()
    sizer = PositionSizer(m)

    position = sizer.calculate(
        signal=Signal(...),
        account_value=500000,
        mode="normal",
        bias="right_priority",
        gate2_light="green",
    )
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import math

from .multipliers import Multipliers
from .base import Signal as BaseSignal  # canonical Signal from base.py


# Re-export for backward compatibility
Signal = BaseSignal


@dataclass
class Position:
    """仓位计算结果"""
    symbol: str
    action: str             # "buy" | "sell" | "hold"
    pct_of_portfolio: float # 仓位百分比 (如 5.0 = 5%)
    shares: int             # 实际股数 (已整手取整)
    max_shares: int         # 集中度上限对应股数
    risk_per_share: float   # 每股风险 (entry - stop_loss)
    total_risk_cny: float   # 总风险金额
    board: str              # "main_board" | "star_board" | "hk_board"
    multipliers: Dict       # 各乘数明细 (调试用)
    warnings: List[str]     # 风控警告


@dataclass
class Account:
    """账户状态"""
    total_value: float      # 总资产
    cash: float             # 现金
    positions: Dict[str, float]  # symbol → 持仓市值
    total_risk_exposure: float = 0.0  # 已占用风险金额


class PositionSizer:
    """
    仓位计算器。

    从 risk-policy.json 读取所有参数，计算标准化仓位。
    所有公式均来自 risk-policy.json 的声明。

    计算公式:
        1. base_pct = fractional_kelly(edge, win_rate) × kelly_multiplier(0.25)
        2. adjusted = base_pct × mode_mult × bias_mult × gate2_mult
        3. cap = min(adjusted, concentration_limit[board])
        4. crowding_override: cap × 0.5 if extreme crowding
        5. shares = floor(cap × account_value / price / lot_size) × lot_size
    """

    def __init__(self, multipliers: Multipliers = None):
        """
        Args:
            multipliers: 乘数系统。如不提供，自动从 risk-policy.json 加载。
        """
        self.m = multipliers or Multipliers.from_risk_policy()

    def calculate(
        self,
        signal: Signal,
        account_value: float,
        mode: str = "normal",
        bias: str = "balanced",
        gate2_net_score: float = None,
        gate2_light: str = None,
        crowding_bear_score: float = 0,
        account: Account = None,
    ) -> Position:
        """
        计算仓位大小。

        Args:
            signal: 标准化交易信号
            account_value: 账户总资产 (CNY)
            mode: normal / earnings_season / risk_off
            bias: right_priority / left_priority / balanced
            gate2_net_score: Gate 2 Net_Score (Bull - Bear), 与 gate2_light 二选一
            gate2_light: Gate 2 灯号 (green/yellow/orange/red), 与 net_score 二选一
            crowding_bear_score: 拥挤度 Bear 得分 (1-5)
            account: 账户对象 (可选，用于计算已占用风险)

        Returns:
            Position 对象，包含仓位百分比、股数、风险指标
        """
        warnings = []

        # Step 0: 确定 Gate 2 灯号
        if gate2_light is None:
            if gate2_net_score is not None:
                gate2_light = self.m.get_gate2_light(gate2_net_score)
            else:
                gate2_light = "yellow"  # 默认保守

        # Step 1: 基础仓位 = fractional Kelly × 0.25
        base_pct = self._fractional_kelly(signal.edge, signal.win_rate)

        # Step 2: 模式乘数
        mode_mult = self.m.get_mode_multiplier(mode)
        if mode_mult == 0:
            return Position(
                symbol=signal.symbol,
                action="hold",
                pct_of_portfolio=0,
                shares=0,
                max_shares=0,
                risk_per_share=0,
                total_risk_cny=0,
                board=self.m.get_board_type(signal.symbol),
                multipliers={"reason": "risk_off — 禁止建仓"},
                warnings=["risk_off 模式禁止新建仓"],
            )

        # Step 3: 策略偏好乘数
        bias_mult = self.m.get_strategy_weight(bias, signal.strategy_type)
        if bias_mult == 0:
            return Position(
                symbol=signal.symbol,
                action="hold",
                pct_of_portfolio=0,
                shares=0,
                max_shares=0,
                risk_per_share=0,
                total_risk_cny=0,
                board=self.m.get_board_type(signal.symbol),
                multipliers={"reason": f"bias={bias} 禁用策略 {signal.strategy_type}", "bias_mult": 0.0, "mode_mult": mode_mult, "gate2_mult": 0.0, "crowding_mult": 1.0, "concentration_limit": 0},
                warnings=[f"当前 bias ({bias}) 禁用 {signal.strategy_type}"],
            )

        # Step 4: Gate 2 乘数
        gate2_mult = self.m.get_gate2_multiplier(gate2_light)
        if gate2_mult == 0:
            return Position(
                symbol=signal.symbol,
                action="hold",
                pct_of_portfolio=0,
                shares=0,
                max_shares=0,
                risk_per_share=0,
                total_risk_cny=0,
                board=self.m.get_board_type(signal.symbol),
                multipliers={"reason": f"Gate 2 {gate2_light} — 不建仓", "gate2_light": gate2_light, "gate2_mult": 0.0, "mode_mult": mode_mult, "bias_mult": bias_mult, "crowding_mult": 1.0, "concentration_limit": 0},
                warnings=[f"Gate 2 {gate2_light} — 不建仓"],
            )

        # Step 5: 计算调整后仓位
        adjusted_pct = base_pct * mode_mult * bias_mult * gate2_mult

        # Step 6: 集中度上限
        board = self.m.get_board_type(signal.symbol)
        concentration_limit = self.m.get_concentration_limit(mode, board)

        # Step 7: 拥挤度超驰
        crowding_override_mult = 1.0
        if crowding_bear_score >= 4:
            override = self.m.get_crowding_override()
            crowding_override_mult = override.get("position_size_mult", 0.5)
            warnings.append(f"拥挤度 Bear={crowding_bear_score} → 仓位 × {crowding_override_mult}")

        # 止损收紧
        stop_loss_tighten = 0
        if crowding_bear_score >= 4:
            stop_loss_tighten = 1.0  # 拥挤超驰: 止损收紧 1%
            warnings.append(f"拥挤超驰: 止损收紧 {stop_loss_tighten}%")

        capped_pct = min(adjusted_pct * crowding_override_mult, concentration_limit)

        # Step 8: 计算股数 (整手取整)
        board_type = board
        lot_size = self.m.get_trade_unit(board_type)
        raw_shares = (capped_pct / 100.0) * account_value / signal.trigger_price
        shares = max(0, math.floor(raw_shares / lot_size) * lot_size)
        max_shares = math.floor(
            (concentration_limit / 100.0) * account_value / signal.trigger_price / lot_size
        ) * lot_size

        # Step 9: 风险计算
        if signal.direction == "long":
            risk_per_share = signal.trigger_price - signal.stop_loss * (1 - stop_loss_tighten / 100)
        else:
            risk_per_share = signal.stop_loss - signal.trigger_price

        total_risk_cny = risk_per_share * shares

        # Step 10: 格式化
        return Position(
            symbol=signal.symbol,
            action="buy" if shares > 0 else "hold",
            pct_of_portfolio=round(capped_pct, 2),
            shares=shares,
            max_shares=max_shares,
            risk_per_share=round(risk_per_share, 3),
            total_risk_cny=round(total_risk_cny, 2),
            board=board,
            multipliers={
                "base_pct": round(base_pct, 4),
                "mode_mult": mode_mult,
                "bias_mult": bias_mult,
                "gate2_mult": gate2_mult,
                "crowding_mult": crowding_override_mult,
                "concentration_limit": concentration_limit,
                "gate2_light": gate2_light,
            },
            warnings=warnings,
        )

    def _fractional_kelly(self, edge: float, win_rate: float) -> float:
        """
        Fractional-Kelly 仓位计算。

        Kelly formula: f* = (p × b - q) / b
        where p = win_rate, q = 1 - p, b = edge

        简化版 (edge 已经是期望收益/风险比):
            f* = edge × win_rate - (1 - win_rate)

        然后乘以 kelly_multiplier (0.25) 得到最终基础仓位。

        Args:
            edge: 期望优势 (reward/risk ratio), 默认 1.0
            win_rate: 胜率估计, 默认 0.5

        Returns:
            基础仓位百分比 (如 12.5 = 12.5%)
        """
        # 参数约束
        edge = max(edge, 0.1)
        win_rate = max(0.3, min(win_rate, 0.8))

        # Kelly formula
        kelly_f = (win_rate * edge - (1 - win_rate)) / max(edge, 0.01)

        # Fractional Kelly × 0.25
        fractional = max(kelly_f, 0) * self.m.kelly_multiplier

        # 上限: 不超过总资产的 50% (Kelly 理论上限)
        return min(fractional * 100, 50.0)


def compute_position(
    symbol: str,
    strategy_type: str,
    direction: str,
    confidence: float,
    trigger_price: float,
    stop_loss: float,
    account_value: float,
    mode: str = "normal",
    bias: str = "balanced",
    gate2_net_score: float = None,
    edge: float = 1.0,
    win_rate: float = 0.5,
    take_profit: List[float] = None,
) -> Position:
    """
    快捷函数 — 一行计算仓位大小。

    Args:
        symbol: 股票代码
        strategy_type: 策略类型 (T1_breakout, T2_pullback, ...)
        direction: long / short
        confidence: 0-1 置信度
        trigger_price: 触发价格
        stop_loss: 止损价格
        account_value: 账户总资产
        mode: normal / earnings_season / risk_off
        bias: right_priority / left_priority / balanced
        gate2_net_score: Gate 2 Net_Score

    Returns:
        Position 对象
    """
    sizer = PositionSizer()
    signal = Signal(
        symbol=symbol,
        strategy_type=strategy_type,
        direction=direction,
        confidence=confidence,
        trigger_price=trigger_price,
        stop_loss=stop_loss,
        take_profit=take_profit or [],
        edge=edge,
        win_rate=win_rate,
    )
    return sizer.calculate(
        signal=signal,
        account_value=account_value,
        mode=mode,
        bias=bias,
        gate2_net_score=gate2_net_score,
    )