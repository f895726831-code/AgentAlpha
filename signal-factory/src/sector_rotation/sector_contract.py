# SPDX-License-Identifier: MIT
# sector_contract.py — 板块信号 JSON 契约
#
# 定义 Layer 1（板块轮动）与 AgentAlpha 之间的数据契约。
# 输出格式：sector_signals.json
#
# 契约版本：1.0
# 同步频率：每日 15:30

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


CONTRACT_VERSION = "1.0"


@dataclass
class SectorSignal:
    """单个板块的信号"""
    sw_code: str                # 申万行业代码
    sector_name: str            # 板块名称
    category: str               # 板块类别 (cycle/growth/consumer/defense/financial/other)
    ml_score: float             # ML 模型预测分数
    ml_rank: int                # ML 排名 (1-31)
    baseline_score: Optional[float] = None  # B 方案基准线分数
    baseline_rank: Optional[int] = None
    is_defense: bool = False    # 是否防御板块
    allowed_in_risk_off: bool = False  # risk_off 时是否允许


@dataclass
class SectorAllocation:
    """板块配置建议"""
    sw_code: str
    sector_name: str
    weight: float               # 建议权重
    allocation_type: str        # top_pick / allowed / restricted
    etf_fallback: Optional[str] = None  # 行业 ETF 兜底


@dataclass
class SectorAllocationSignal:
    """板块轮动完整信号"""
    # 元数据
    version: str = CONTRACT_VERSION
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))

    # 模型信息
    model_name: str = "sector_gbdt"
    model_ensemble: List[str] = field(default_factory=list)

    # 板块排名
    sector_signals: List[SectorSignal] = field(default_factory=list)

    # 配置建议
    allocations: List[SectorAllocation] = field(default_factory=list)

    # 模式信息
    pipeline_mode: str = "normal"  # normal / earnings_season / risk_off
    top_n: int = 5
    defense_only: bool = False

    # 质量指标
    ic_estimate: Optional[float] = None
    confidence: Optional[float] = None  # 0-100

    # 元数据
    status: str = "ok"
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at,
            "date": self.date,
            "model_name": self.model_name,
            "model_ensemble": self.model_ensemble,
            "sector_signals": [asdict(s) for s in self.sector_signals],
            "allocations": [asdict(a) for a in self.allocations],
            "pipeline_mode": self.pipeline_mode,
            "top_n": self.top_n,
            "defense_only": self.defense_only,
            "ic_estimate": self.ic_estimate,
            "confidence": self.confidence,
            "status": self.status,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def save(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def from_json(cls, json_str: str) -> "SectorAllocationSignal":
        data = json.loads(json_str)

        signals = [SectorSignal(**s) for s in data.get("sector_signals", [])]
        allocations = [SectorAllocation(**a) for a in data.get("allocations", [])]

        return cls(
            version=data.get("version", CONTRACT_VERSION),
            generated_at=data.get("generated_at", ""),
            date=data.get("date", ""),
            model_name=data.get("model_name", ""),
            model_ensemble=data.get("model_ensemble", []),
            sector_signals=signals,
            allocations=allocations,
            pipeline_mode=data.get("pipeline_mode", "normal"),
            top_n=data.get("top_n", 5),
            defense_only=data.get("defense_only", False),
            ic_estimate=data.get("ic_estimate"),
            confidence=data.get("confidence"),
            status=data.get("status", "ok"),
            warnings=data.get("warnings", []),
            errors=data.get("errors", []),
        )


def validate_sector_signal(signal: SectorAllocationSignal) -> List[str]:
    """验证板块信号有效性"""
    issues = []

    # 至少有 31 个板块
    if len(signal.sector_signals) < 31:
        issues.append(f"板块信号不足 31 个：{len(signal.sector_signals)}")

    # 排名必须不重复
    ranks = [s.ml_rank for s in signal.sector_signals]
    if len(set(ranks)) != len(ranks):
        issues.append("板块排名存在重复")

    # risk_off 时必须是防御板块
    if signal.pipeline_mode == "risk_off" and not signal.defense_only:
        issues.append("risk_off 模式但 defense_only=False")

    # 权重总和
    total_weight = sum(a.weight for a in signal.allocations)
    if abs(total_weight - 1.0) > 0.02:
        issues.append(f"板块权重总和偏离 100%：{total_weight:.2%}")

    return issues


def sector_signal_to_json(signal: SectorAllocationSignal, path: Optional[Path] = None) -> str:
    json_str = signal.to_json()
    if path:
        signal.save(path)
    return json_str


def json_to_sector_signal(json_str: str) -> SectorAllocationSignal:
    return SectorAllocationSignal.from_json(json_str)