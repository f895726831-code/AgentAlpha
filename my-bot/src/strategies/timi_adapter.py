"""
TiMi 管线适配器 — Python 在线执行入口，供 TypeScript 管线调用。

这是 TiMi 与现有 v4.3 管线的桥梁:
  - 盘前/周末: LLM Agent → compiler → serializer → disk
  - 盘中: pipeline calls timi_adapter.run() → signals + positions as JSON
  - 异常: timi_adapter 触发 fallback → 管线降级到 LLM Agent

用法:
    # 作为模块
    from strategies.timi_adapter import TiMiAdapter
    adapter = TiMiAdapter()
    result = adapter.run(market_data_list, mode="normal", bias="right_priority")

    # 作为 CLI (供 TypeScript 管线调用)
    python -m strategies.timi_adapter --date 2026-10-14 --bias right_priority
"""

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from .compiler import StrategyCompiler
from .serializer import StrategySerializer
from .position_sizer import PositionSizer, Account
from .multipliers import Multipliers
from .executor import OnlineExecutor, ExecutionResult
from .anomaly_detector import AnomalyDetector
from .fallback import FallbackHandler


@dataclass
class AdapterResult:
    """TiMi 适配器输出 — 可直接 JSON 序列化"""
    success: bool
    source: str                                # "timi" | "timi_fallback" | "llm_fallback"
    signals: List[Dict[str, Any]] = field(default_factory=list)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    execution_summary: Dict[str, Any] = field(default_factory=dict)
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    fallback: Optional[Dict[str, Any]] = None
    warnings: List[str] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "source": self.source,
            "signals": self.signals,
            "positions": self.positions,
            "execution_summary": self.execution_summary,
            "anomalies": self.anomalies,
            "fallback": self.fallback,
            "warnings": self.warnings,
            "latency_ms": self.latency_ms,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


class TiMiAdapter:
    """
    TiMi 管线适配器。

    整合 compiler → serializer → executor → anomaly → fallback 全链路,
    为管线提供统一的 Python 调用入口。

    正常路径:
      disk → deserialize → execute → AdapterResult(signals, positions, source="timi")

    异常路径:
      disk → deserialize → execute → anomaly detected → fallback
        → AdapterResult(signals, positions, source="timi_fallback")

    无策略文件:
      → AdapterResult(success=False, source="llm_fallback")
    """

    def __init__(
        self,
        storage_dir: Path = None,
        risk_policy_path: Path = None,
    ):
        self.storage_dir = storage_dir or Path("data/strategies")
        self.risk_policy_path = risk_policy_path or Path("data/risk-policy.json")

        # 初始化组件
        self.multipliers = Multipliers.from_risk_policy(str(self.risk_policy_path))
        self.sizer = PositionSizer(self.multipliers)
        self.compiler = StrategyCompiler()
        self.serializer = StrategySerializer(storage_dir=self.storage_dir)
        self.detector = AnomalyDetector()
        self.fallback = FallbackHandler(
            mock_mode=not os.environ.get("DEEPSEEK_API_KEY"),
        )

    def run(
        self,
        market_data_list: List[Dict[str, Any]],
        mode: str = "normal",
        bias: str = "balanced",
        gate2_light: str = "green",
        crowding_score: int = 0,
        account_value: float = 500_000,
        cash: float = 500_000,
        date_str: str = None,
        **kwargs,
    ) -> AdapterResult:
        """
        执行 TiMi 管线。
        """
        date_str = date_str or date.today().isoformat()

        if not market_data_list:
            return AdapterResult(
                success=False,
                source="llm_fallback",
                warnings=["market_data_list is empty"],
            )

        # ── Step 1: 加载今日策略 ──
        try:
            strategies = self.serializer.get_latest()
        except Exception:
            strategies = None

        if strategies is None:
            return AdapterResult(
                success=False,
                source="llm_fallback",
                warnings=[f"无可用策略文件 ({date_str}) — 回退 LLM Agent"],
            )

        # risk_off 模式: 合法, 0 活跃策略, 不需要 fallback
        if not strategies.active_strategies:
            if strategies.mode == "risk_off":
                return AdapterResult(
                    success=True,
                    source="timi",
                    signals=[],
                    positions=[],
                    execution_summary={
                        "strategy_count": 0,
                        "tick_count": len(market_data_list),
                        "signal_count": 0,
                        "position_count": 0,
                        "mode": "risk_off",
                        "bias": strategies.bias,
                        "gate2_light": gate2_light,
                    },
                    warnings=[f"STABLE 判 risk_off — 全部策略停用, 禁止开仓"],
                    latency_ms=0.0,
                )
            else:
                return AdapterResult(
                    success=False,
                    source="llm_fallback",
                    warnings=[f"编译策略无活跃项 (mode={strategies.mode}) — 回退 LLM Agent"],
                )

        # ── Step 2: 在线执行 ──
        account = Account(total_value=account_value, cash=cash, positions={})
        executor = OnlineExecutor(
            position_sizer=self.sizer,
            anomaly_detector=self.detector,
            fallback_handler=self.fallback,
        )

        result = executor.execute(
            strategies=strategies,
            market_data_list=market_data_list,
            account=account,
            mode=mode,
            bias=bias,
            gate2_light=gate2_light,
            crowding_score=crowding_score,
        )

        # ── Step 3: 构建输出 ──
        signals_dicts = [s.to_dict() for s in result.signals]
        positions_dicts = [
            {
                "symbol": p.symbol,
                "action": p.action,
                "pct_of_portfolio": p.pct_of_portfolio,
                "shares": p.shares,
                "max_shares": p.max_shares,
                "risk_per_share": p.risk_per_share,
                "total_risk_cny": p.total_risk_cny,
                "board": p.board,
                "warnings": p.warnings,
            }
            for p in result.positions
        ]
        anomalies_dicts = [
            {"code": a.code, "severity": a.severity, "message": a.message,
             "recommendation": a.recommendation}
            for a in result.anomalies
        ]

        # 确定 source
        if result.fallback_triggered:
            source = "timi_fallback"
            fallback_dict = {
                "decision": result.fallback_result.decision,
                "reasoning": result.fallback_result.reasoning,
                "source": result.fallback_result.source,
            }
        else:
            source = "timi"
            fallback_dict = None

        return AdapterResult(
            success=True,
            source=source,
            signals=signals_dicts,
            positions=positions_dicts,
            execution_summary={
                "strategy_count": result.meta.get("strategy_count", 0),
                "tick_count": result.meta.get("tick_count", 0),
                "signal_count": result.signal_count,
                "position_count": len(result.buy_positions),
                "mode": mode,
                "bias": bias,
                "gate2_light": gate2_light,
            },
            anomalies=anomalies_dicts,
            fallback=fallback_dict,
            warnings=result.warnings,
            latency_ms=result.total_latency_ms,
        )


# ─── CLI 入口 ───

def main():
    """CLI 入口 — 供 TypeScript 管线 / cron 调用"""
    import argparse

    parser = argparse.ArgumentParser(description="TiMi 在线执行")
    parser.add_argument("--input", type=str, default=None,
                        help="市场数据 JSON 文件路径 (stdin if omitted)")
    parser.add_argument("--date", type=str, default=date.today().isoformat(),
                        help="策略日期 (YYYY-MM-DD)")
    parser.add_argument("--mode", type=str, default="normal")
    parser.add_argument("--bias", type=str, default="balanced")
    parser.add_argument("--gate2", type=str, default="green")
    parser.add_argument("--crowding", type=int, default=0)
    parser.add_argument("--account-value", type=float, default=500000)
    parser.add_argument("--account-cash", type=float, default=500000)
    parser.add_argument("--output", type=str, default=None,
                        help="输出 JSON 文件路径 (stdout if omitted)")

    args = parser.parse_args()

    # 读取输入
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            market_data_list = json.load(f)
    else:
        market_data_list = json.load(sys.stdin)

    # 执行
    adapter = TiMiAdapter()
    result = adapter.run(
        market_data_list=market_data_list,
        mode=args.mode,
        bias=args.bias,
        gate2_light=args.gate2,
        crowding_score=args.crowding,
        account_value=args.account_value,
        cash=args.account_cash,
        date_str=args.date,
    )

    # 输出
    output_json = result.to_json()
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
    else:
        print(output_json)


if __name__ == "__main__":
    main()