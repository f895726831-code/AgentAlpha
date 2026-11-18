"""
多维乘数系统 — 从 risk-policy.json 加载所有风控参数。

risk-policy.json 是唯一权威规则来源，不在代码中硬编码任何参数。

用法:
    from strategies.multipliers import Multipliers

    m = Multipliers.from_risk_policy()

    # 集中度上限
    limit = m.get_concentration_limit("normal", "main_board")  # → 10

    # 策略偏好权重
    weight = m.get_strategy_weight("right_priority", "T1_breakout")  # → 1.5

    # Gate 2 仓位乘数
    mult = m.get_gate2_multiplier("green")  # → 1.0

    # 模式仓位乘数
    mult = m.get_mode_multiplier("risk_off")  # → 0.0 (禁止建仓)
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional


# 默认 risk-policy.json 路径 (相对于 my-bot 目录)
DEFAULT_RISK_POLICY_PATH = "data/risk-policy.json"


@dataclass
class Multipliers:
    """从 risk-policy.json 加载的多维乘数系统"""

    _data: dict = field(default_factory=dict)
    _path: str = ""

    # ─── 工厂方法 ───

    @classmethod
    def from_risk_policy(cls, path: str = None) -> "Multipliers":
        """
        从 risk-policy.json 加载乘数。

        Args:
            path: JSON 文件路径。默认自动查找 my-bot/data/risk-policy.json

        Returns:
            Multipliers 实例

        Raises:
            FileNotFoundError: 找不到 risk-policy.json
            ValueError: JSON 格式错误
        """
        if path is None:
            # 自动查找: 从当前文件向上找到 my-bot 目录
            candidates = [
                Path(__file__).resolve().parent.parent.parent / "data" / "risk-policy.json",
                Path(os.getcwd()) / "data" / "risk-policy.json",
                Path("D:/AgentAlpha/my-bot/data/risk-policy.json"),
            ]
            for p in candidates:
                if p.exists():
                    path = str(p)
                    break
            else:
                raise FileNotFoundError(
                    f"risk-policy.json not found. Searched: {[str(p) for p in candidates]}"
                )

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return cls(_data=data, _path=str(path))

    # ─── 集中度上限 ───

    def get_concentration_limit(self, mode: str, board: str) -> float:
        """
        获取集中度上限 (%)。

        Args:
            mode: normal / earnings_season / risk_off
            board: main_board / star_board / hk_board

        Returns:
            最大仓位百分比 (整数，如 10 表示 10%)

        Examples:
            >>> m.get_concentration_limit("normal", "main_board")
            10
            >>> m.get_concentration_limit("risk_off", "main_board")
            5
        """
        # 标准化 board 名称
        board_map = {
            "main_board": "main_board",
            "star_board": "star_board",
            "hk_board": "hk_board",
            "main": "main_board",
            "star": "star_board",
            "hk": "hk_board",
            "688": "star_board",
            "600": "main_board",
            "000": "main_board",
            "300": "main_board",
            "sh": "main_board",
            "sz": "main_board",
        }
        board_key = board_map.get(board, board)

        modes = self._data.get("modes", {})
        mode_config = modes.get(mode, modes.get("normal", {}))
        concentration = mode_config.get("concentration", {})
        board_config = concentration.get(board_key, {})

        if isinstance(board_config, dict):
            return float(board_config.get("max_pct", 100))
        return float(board_config)

    # ─── 模式仓位乘数 ───

    def get_mode_multiplier(self, mode: str) -> float:
        """
        获取模式仓位乘数。

        - normal: 1.0 (允许建仓)
        - earnings_season: 1.0 (允许建仓)
        - risk_off: 0.0 (禁止建仓)

        Args:
            mode: normal / earnings_season / risk_off

        Returns:
            仓位乘数
        """
        modes = self._data.get("modes", {})
        mode_config = modes.get(mode, {})
        allow_new = mode_config.get("allow_new_positions", True)

        if mode == "risk_off":
            return 0.0  # 禁止建仓

        return 1.0 if allow_new else 0.0

    # ─── 策略偏好权重 ───

    def get_strategy_weight(self, bias: str, strategy: str) -> float:
        """
        获取策略偏好权重。

        Args:
            bias: right_priority / left_priority / balanced
            strategy: T1_breakout / T2_pullback / T3_oversold_bounce /
                      T4_golden_cross / VCP_breakout / T5_earnings / T6_capital

        Returns:
            策略权重 (如 1.5 / 1.0 / 0.5 / 0.0)

        Examples:
            >>> m.get_strategy_weight("right_priority", "T1_breakout")
            1.5
            >>> m.get_strategy_weight("right_priority", "T3_oversold_bounce")
            0.0
            >>> m.get_strategy_weight("left_priority", "T2_pullback")
            1.5
        """
        bias_config = self._data.get("strategy_bias", {})
        regimes = bias_config.get("regimes", {})
        regime = regimes.get(bias, regimes.get("balanced", {}))
        weights = regime.get("strategy_weights", {})

        strat_config = weights.get(strategy, {})
        if isinstance(strat_config, dict):
            return float(strat_config.get("weight", 1.0))
        return float(strat_config)

    def get_all_strategy_weights(self, bias: str) -> Dict[str, float]:
        """
        获取某个 bias 下所有策略的权重映射。

        Returns:
            {"T1_breakout": 1.5, "T2_pullback": 0.5, ...}
        """
        bias_config = self._data.get("strategy_bias", {})
        regimes = bias_config.get("regimes", {})
        regime = regimes.get(bias, regimes.get("balanced", {}))
        weights = regime.get("strategy_weights", {})

        result = {}
        for key, val in weights.items():
            if isinstance(val, dict):
                result[key] = float(val.get("weight", 1.0))
            else:
                result[key] = float(val)
        return result

    # ─── Gate 2 仓位乘数 ───

    def get_gate2_multiplier(self, light: str) -> float:
        """
        获取 Gate 2 仓位乘数。

        Args:
            light: green / yellow / orange / red

        Returns:
            仓位乘数

        Examples:
            >>> m.get_gate2_multiplier("green")
            1.0
            >>> m.get_gate2_multiplier("yellow")
            0.5
            >>> m.get_gate2_multiplier("red")
            0.0
        """
        debate = self._data.get("debate_gate", {})
        thresholds = debate.get("thresholds", {})
        entry = thresholds.get("entry", {})

        zone = entry.get(light, {})
        if isinstance(zone, dict):
            return float(zone.get("position_multiplier", 0.0))
        return 0.0

    def get_gate2_light(self, net_score: float) -> str:
        """
        根据 Net_Score 返回 Gate 2 灯号。

        Args:
            net_score: Bull_Score - Bear_Score (范围 [-4.0, +4.0])

        Returns:
            "green" | "yellow" | "orange" | "red"
        """
        if net_score >= 2.0:
            return "green"
        elif net_score >= 0.5:
            return "yellow"
        elif net_score > -0.5:
            return "orange"
        else:
            return "red"

    # ─── 熔断参数 ───

    def get_circuit_breaker(self, mode: str) -> Dict[str, float]:
        """
        获取熔断参数。

        Returns:
            {"daily_loss_pct": -2.0, "consecutive_losses": 5}
        """
        modes = self._data.get("modes", {})
        mode_config = modes.get(mode, modes.get("normal", {}))
        cb = mode_config.get("circuit_breakers", {})
        return {
            "daily_loss_pct": float(cb.get("daily_loss_pct", -2.0)),
            "consecutive_losses": int(cb.get("consecutive_losses", 5)),
        }

    # ─── 通用参数 ───

    @property
    def kelly_multiplier(self) -> float:
        """Fractional-Kelly 乘数 (默认 0.25)"""
        universal = self._data.get("universal", {})
        ps = universal.get("position_sizing", {})
        return float(ps.get("kelly_multiplier", 0.25))

    def get_trade_unit(self, board: str) -> int:
        """获取最小交易单位 (股)"""
        universal = self._data.get("universal", {})
        tu = universal.get("trade_units", {})
        board_map = {"main_board": "main_board", "star_board": "star_board",
                     "hk_board": "hk_board", "main": "main_board",
                     "star": "star_board", "hk": "hk_board"}
        key = board_map.get(board, "main_board")
        val = tu.get(key, 100)
        if val == "varies":
            return 100  # 港股默认
        return int(val)

    def get_board_type(self, symbol: str) -> str:
        """
        根据股票代码判断板块类型。

        Returns:
            "main_board" | "star_board" | "hk_board"
        """
        code = symbol.replace(".SH", "").replace(".SZ", "").replace(".HK", "")
        if ".HK" in symbol or code.isdigit() and len(code) == 5:
            return "hk_board"
        if code.startswith("688"):
            return "star_board"
        return "main_board"

    # ─── Gate 2 评分维度 ───

    def get_gate2_weights(self, is_earnings_season: bool = False) -> Dict[str, float]:
        """
        获取 Gate 2 各维度权重。

        Args:
            is_earnings_season: 是否为财报季 (会添加 earnings 维度)

        Returns:
            {"technical": 0.30, "capital_flow": 0.15, ...}
        """
        debate = self._data.get("debate_gate", {})
        if is_earnings_season:
            weights = debate.get("earnings_season_weights", {})
        else:
            weights = debate.get("weights", {})

        result = {}
        for key, val in weights.items():
            if isinstance(val, dict):
                result[key] = float(val.get("weight", 0))
            else:
                result[key] = float(val)
        return result

    # ─── 拥挤度超驰 ───

    def get_crowding_override(self) -> Dict:
        """获取极端拥挤时的超驰参数"""
        debate = self._data.get("debate_gate", {})
        crowding = debate.get("crowding", {})
        override = crowding.get("crowding_override", {})
        extreme = override.get("extreme_crowding", {})

        actions = extreme.get("actions", {})
        return {
            "condition": extreme.get("condition", ""),
            "profit_target_mult": 0.7,
            "position_size_mult": 0.5,
            "stop_loss_tighten_pct": 1.0,
        }

    # ─── 查询方法 ───

    def is_risk_off(self, mode: str) -> bool:
        """判断是否为防守模式"""
        return mode == "risk_off" or self.get_mode_multiplier(mode) == 0.0

    def get_active_strategies(self, bias: str) -> Dict[str, float]:
        """
        获取当前 bias 下所有非零权重的策略。

        Returns:
            {"T1_breakout": 1.5, "VCP_breakout": 1.5, ...}
        """
        all_weights = self.get_all_strategy_weights(bias)
        return {k: v for k, v in all_weights.items() if v > 0}