"""macro_contract.py — Layer 0 资产配置 JSON 契约定义与校验

定义 asset_allocation.json 的完整 schema，提供 validate/serialize/deserialize。
被 FM_trade 的 build_report_context.py 和 quant-signal-factory 管线消费。

Contract version: 1.0.0
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Tuple


# ═══════════════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════════════

CONTRACT_VERSION = "1.0.0"

# Canonical asset keys used in contract output
ASSET_KEYS = ["oil", "gold", "bond", "nasdaq", "hstech", "a_share", "cash",
              "star_50", "nikkei", "india", "cb_bond"]

# Aliases accepted on input (mapped to canonical key)
# scoring modules (asset_scorer, portfolio_builder) use hk_tech internally
ASSET_KEY_ALIASES: Dict[str, str] = {
    "hk_tech": "hstech",
}

ASSET_NAMES: Dict[str, str] = {
    "oil": "华宝油气",
    "gold": "华安黄金ETF",
    "bond": "国债ETF",
    "nasdaq": "纳指ETF",
    "hstech": "恒生科技ETF",
    "hk_tech": "恒生科技ETF",  # alias
    "a_share": "沪深300ETF",
    "cash": "国债逆回购",
}

ASSET_ETF_CODES: Dict[str, str] = {
    "oil": "162411",
    "gold": "518880",
    "bond": "511010",
    "nasdaq": "513100",
    "hstech": "513130",
    "hk_tech": "513130",  # alias
    "a_share": "159919",
    "cash": "GC001",
}

ASSET_CATEGORIES: Dict[str, str] = {
    "oil": "commodity",
    "gold": "commodity",
    "bond": "bond",
    "nasdaq": "overseas_equity",
    "hstech": "overseas_equity",
    "hk_tech": "overseas_equity",  # alias
    "a_share": "domestic_equity",
    "cash": "cash",
}

CATEGORY_LABELS_CN: Dict[str, str] = {
    "commodity": "商品",
    "bond": "债券",
    "overseas_equity": "海外权益",
    "domestic_equity": "A股权益",
    "cash": "现金",
}

REGIME_LABELS: Dict[str, str] = {
    "expansion": "扩张",
    "overheating": "过热",
    "recovery": "复苏",
    "stagflation": "滞胀",
    "recession": "衰退",
    "deflation": "通缩",
}


# ═══════════════════════════════════════════════════════════════════════
# Data Classes — Match asset_allocation.json schema
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class DimensionJudgment:
    """单一宏观维度判断"""
    value: str = ""
    direction: str = "→"  # ↑ ↑↑ ↓ ↓↓ →
    judgment: str = ""
    signal: float = 0.0  # -1.0 to +1.0


@dataclass
class MacroQuadrant:
    """宏观四象限判断"""
    quadrant: str = ""  # expansion / overheating / recovery / stagflation / recession / deflation
    quadrant_label_cn: str = ""
    confidence: float = 0.0  # 0.0-1.0
    growth: DimensionJudgment = field(default_factory=DimensionJudgment)
    inflation: DimensionJudgment = field(default_factory=DimensionJudgment)
    liquidity: DimensionJudgment = field(default_factory=DimensionJudgment)
    risk_appetite: DimensionJudgment = field(default_factory=DimensionJudgment)


@dataclass
class AssetTargetWeight:
    """单个资产的目标权重"""
    asset: str = ""
    asset_name: str = ""
    etf_code: str = ""
    category: str = ""
    target_weight: float = 0.0  # 0.0-1.0
    current_weight: float = 0.0
    score: float = 0.0  # 0-100
    recommendation: str = "neutral"  # overweight / neutral / underweight / avoid
    method: str = "risk_parity"  # risk_parity / active / constrained


@dataclass
class RebalanceAction:
    """单条调仓建议"""
    asset: str = ""
    asset_name: str = ""
    action: str = "hold"  # buy / sell / hold / new
    current_weight: float = 0.0
    target_weight: float = 0.0
    drift: float = 0.0
    suggestion: str = ""  # 中文操作建议
    urgency: str = "normal"  # normal / soon / now


@dataclass
class ModeOverride:
    """模式覆盖"""
    mode: str = "normal"
    a_share_cap: Optional[float] = None
    bond_floor: Optional[float] = None
    gold_floor: Optional[float] = None
    cash_floor: Optional[float] = None
    note: str = ""


@dataclass
class AssetAllocation:
    """完整的资产配置方案 — asset_allocation.json 顶层结构"""
    # Metadata
    version: str = CONTRACT_VERSION
    updated: str = ""  # YYYY-MM-DD
    generated_at: str = ""  # ISO timestamp

    # Macro environment
    macro_quadrant: MacroQuadrant = field(default_factory=MacroQuadrant)

    # Asset weights
    target_weights: Dict[str, float] = field(default_factory=dict)
    current_weights: Dict[str, float] = field(default_factory=dict)
    asset_details: List[AssetTargetWeight] = field(default_factory=list)

    # Rebalance plan
    rebalance_suggestions: List[RebalanceAction] = field(default_factory=list)
    total_drift: float = 0.0  # sum of |target - current|
    needs_rebalance: bool = False

    # Mode & constraints
    mode_override: ModeOverride = field(default_factory=ModeOverride)
    constraints_check: Dict[str, bool] = field(default_factory=dict)

    # Summary
    summary: str = ""


# ═══════════════════════════════════════════════════════════════════════
# Key normalization
# ═══════════════════════════════════════════════════════════════════════


def normalize_asset_key(key: str) -> str:
    """Normalize asset key: hk_tech → hstech (scoring module → contract canonical)."""
    return ASSET_KEY_ALIASES.get(key, key)


def normalize_weights(weights: Dict[str, float]) -> Dict[str, float]:
    """Normalize all keys in a weights dict to canonical form."""
    return {normalize_asset_key(k): v for k, v in weights.items()}


# ═══════════════════════════════════════════════════════════════════════
# Validation
# ═══════════════════════════════════════════════════════════════════════


def validate_signal(signal: AssetAllocation) -> Tuple[bool, List[str]]:
    """验证 AssetAllocation 对象的完整性和一致性。

    Returns:
        (is_valid, errors) — errors 为空列表表示通过。
    """
    errors: List[str] = []

    # 1. Version check
    if signal.version != CONTRACT_VERSION:
        errors.append(f"Contract version mismatch: {signal.version} != {CONTRACT_VERSION}")

    # 2. Date present
    if not signal.updated:
        errors.append("Missing 'updated' date")

    # 3. All 7 assets in target_weights (accept aliases)
    normalized_tw = normalize_weights(signal.target_weights)
    missing = set(ASSET_KEYS) - set(normalized_tw.keys())
    if missing:
        errors.append(f"Missing assets in target_weights: {missing}")

    extra = set(normalized_tw.keys()) - set(ASSET_KEYS)
    if extra:
        errors.append(f"Unknown assets in target_weights: {extra}")

    # 4. Weights sum to ~1.0
    if normalized_tw:
        total = sum(normalized_tw.values())
        if abs(total - 1.0) > 0.01:
            errors.append(f"Target weights sum to {total:.4f}, expected 1.0")

    # 5. Each weight in [0, 1]
    for k, v in normalized_tw.items():
        if v < 0 or v > 1:
            errors.append(f"Weight out of range: {k}={v}")

    # 6. asset_details count matches
    if signal.asset_details and len(signal.asset_details) != len(ASSET_KEYS):
        errors.append(f"asset_details has {len(signal.asset_details)} entries, expected {len(ASSET_KEYS)}")

    # 7. Macro quadrant exists
    if not signal.macro_quadrant.quadrant:
        errors.append("Missing macro_quadrant.quadrant")

    # 8. Rebalance actions reference valid assets (accept aliases)
    valid_assets = set(ASSET_KEYS) | set(ASSET_KEY_ALIASES.keys())
    for action in signal.rebalance_suggestions:
        if normalize_asset_key(action.asset) not in set(ASSET_KEYS):
            errors.append(f"Invalid asset in rebalance action: {action.asset}")

    return (len(errors) == 0, errors)


def validate_dict(data: dict) -> Tuple[bool, List[str]]:
    """Validate raw dict against contract schema (lightweight check).
    Accepts both hk_tech and hstech keys."""
    errors: List[str] = []

    # Required top-level keys
    required_keys = {"updated", "macro_quadrant", "target_weights"}
    missing = required_keys - set(data.keys())
    if missing:
        errors.append(f"Missing required keys: {missing}")

    # target_weights must be dict with expected keys (accept aliases)
    if "target_weights" in data:
        tw = normalize_weights(data["target_weights"])
        if not isinstance(data["target_weights"], dict):
            errors.append("target_weights must be a dict")
        else:
            extra = set(tw.keys()) - set(ASSET_KEYS)
            if extra:
                errors.append(f"Unknown asset keys in target_weights: {extra}")

    # macro_quadrant must have quadrant key
    if "macro_quadrant" in data:
        mq = data["macro_quadrant"]
        if not isinstance(mq, dict):
            errors.append("macro_quadrant must be a dict")
        elif "quadrant" not in mq:
            errors.append("macro_quadrant missing 'quadrant' key")

    return (len(errors) == 0, errors)


# ═══════════════════════════════════════════════════════════════════════
# Serialization
# ═══════════════════════════════════════════════════════════════════════


def _dimension_to_dict(d: DimensionJudgment) -> dict:
    return {
        "value": d.value,
        "direction": d.direction,
        "judgment": d.judgment,
        "signal": d.signal,
    }


def _dict_to_dimension(d: dict) -> DimensionJudgment:
    return DimensionJudgment(
        value=d.get("value", ""),
        direction=d.get("direction", "→"),
        judgment=d.get("judgment", ""),
        signal=float(d.get("signal", 0.0)),
    )


def signal_to_dict(signal: AssetAllocation) -> dict:
    """Serialize AssetAllocation to dict matching asset_allocation.json schema.

    Target weights and asset keys are normalized (hk_tech → hstech) on output.
    """
    # Normalize weight keys to canonical
    canonical_weights = normalize_weights(signal.target_weights)

    # Normalize asset_details
    canonical_details = []
    for a in signal.asset_details:
        canonical_details.append({
            "asset": normalize_asset_key(a.asset),
            "asset_name": a.asset_name,
            "etf_code": a.etf_code,
            "category": a.category,
            "target_weight": a.target_weight,
            "current_weight": a.current_weight,
            "score": a.score,
            "recommendation": a.recommendation,
            "method": a.method,
        })

    return {
        "version": signal.version,
        "updated": signal.updated,
        "generated_at": signal.generated_at,
        "macro_quadrant": {
            "quadrant": signal.macro_quadrant.quadrant,
            "quadrant_label_cn": signal.macro_quadrant.quadrant_label_cn,
            "confidence": signal.macro_quadrant.confidence,
            "growth": _dimension_to_dict(signal.macro_quadrant.growth),
            "inflation": _dimension_to_dict(signal.macro_quadrant.inflation),
            "liquidity": _dimension_to_dict(signal.macro_quadrant.liquidity),
            "risk_appetite": _dimension_to_dict(signal.macro_quadrant.risk_appetite),
        },
        "target_weights": canonical_weights,
        "current_weights": normalize_weights(signal.current_weights),
        "asset_details": canonical_details,
        "rebalance_suggestions": [
            {
                "asset": normalize_asset_key(r.asset),
                "asset_name": r.asset_name,
                "action": r.action,
                "current_weight": r.current_weight,
                "target_weight": r.target_weight,
                "drift": r.drift,
                "suggestion": r.suggestion,
                "urgency": r.urgency,
            }
            for r in signal.rebalance_suggestions
        ],
        "total_drift": signal.total_drift,
        "needs_rebalance": signal.needs_rebalance,
        "mode_override": {
            "mode": signal.mode_override.mode,
            "a_share_cap": signal.mode_override.a_share_cap,
            "bond_floor": signal.mode_override.bond_floor,
            "gold_floor": signal.mode_override.gold_floor,
            "cash_floor": signal.mode_override.cash_floor,
            "note": signal.mode_override.note,
        },
        "constraints_check": signal.constraints_check,
        "summary": signal.summary,
    }


def signal_to_json(signal: AssetAllocation, indent: int = 2) -> str:
    """Serialize to JSON string."""
    return json.dumps(signal_to_dict(signal), ensure_ascii=False, indent=indent)


def dict_to_signal(data: dict) -> AssetAllocation:
    """Deserialize dict from asset_allocation.json to AssetAllocation."""
    mq = data.get("macro_quadrant", {})
    mode = data.get("mode_override", {})

    return AssetAllocation(
        version=data.get("version", CONTRACT_VERSION),
        updated=data.get("updated", ""),
        generated_at=data.get("generated_at", ""),
        macro_quadrant=MacroQuadrant(
            quadrant=mq.get("quadrant", ""),
            quadrant_label_cn=mq.get("quadrant_label_cn", ""),
            confidence=float(mq.get("confidence", 0.0)),
            growth=_dict_to_dimension(mq.get("growth", {})),
            inflation=_dict_to_dimension(mq.get("inflation", {})),
            liquidity=_dict_to_dimension(mq.get("liquidity", {})),
            risk_appetite=_dict_to_dimension(mq.get("risk_appetite", {})),
        ),
        target_weights=data.get("target_weights", {}),
        current_weights=data.get("current_weights", {}),
        asset_details=[
            AssetTargetWeight(
                asset=a.get("asset", ""),
                asset_name=a.get("asset_name", ""),
                etf_code=a.get("etf_code", ""),
                category=a.get("category", ""),
                target_weight=float(a.get("target_weight", 0)),
                current_weight=float(a.get("current_weight", 0)),
                score=float(a.get("score", 0)),
                recommendation=a.get("recommendation", "neutral"),
                method=a.get("method", "risk_parity"),
            )
            for a in data.get("asset_details", [])
        ],
        rebalance_suggestions=[
            RebalanceAction(
                asset=r.get("asset", ""),
                asset_name=r.get("asset_name", ""),
                action=r.get("action", "hold"),
                current_weight=float(r.get("current_weight", 0)),
                target_weight=float(r.get("target_weight", 0)),
                drift=float(r.get("drift", 0)),
                suggestion=r.get("suggestion", ""),
                urgency=r.get("urgency", "normal"),
            )
            for r in data.get("rebalance_suggestions", [])
        ],
        total_drift=float(data.get("total_drift", 0)),
        needs_rebalance=bool(data.get("needs_rebalance", False)),
        mode_override=ModeOverride(
            mode=mode.get("mode", "normal"),
            a_share_cap=mode.get("a_share_cap"),
            bond_floor=mode.get("bond_floor"),
            gold_floor=mode.get("gold_floor"),
            cash_floor=mode.get("cash_floor"),
            note=mode.get("note", ""),
        ),
        constraints_check=data.get("constraints_check", {}),
        summary=data.get("summary", ""),
    )


def json_to_signal(json_str: str) -> AssetAllocation:
    """Deserialize JSON string to AssetAllocation."""
    data = json.loads(json_str)
    return dict_to_signal(data)


# ── 向后兼容别名 ──
MacroAllocationSignal = AssetAllocation


# ═══════════════════════════════════════════════════════════════════════
# Factory & Helpers
# ═══════════════════════════════════════════════════════════════════════


def create_default_allocation() -> AssetAllocation:
    """Create a safe default allocation (conservative, usable when data unavailable).

    默认配置：30% A股 / 30% 债券 / 20% 现金 / 10% 黄金 / 10% 恒生科技
    This is the fallback when macro analysis is not available.
    """
    today = date.today().isoformat()
    now = datetime.now().isoformat()

    return AssetAllocation(
        version=CONTRACT_VERSION,
        updated=today,
        generated_at=now,
        macro_quadrant=MacroQuadrant(
            quadrant="deflation",
            quadrant_label_cn="通缩（默认保守配置）",
            confidence=0.3,
            growth=DimensionJudgment(value="N/A", direction="→", judgment="数据不可用"),
            inflation=DimensionJudgment(value="N/A", direction="→", judgment="数据不可用"),
            liquidity=DimensionJudgment(value="N/A", direction="→", judgment="数据不可用"),
            risk_appetite=DimensionJudgment(value="N/A", direction="→", judgment="数据不可用"),
        ),
        target_weights={
            "oil": 0.00,
            "gold": 0.10,
            "bond": 0.30,
            "nasdaq": 0.00,
            "hstech": 0.10,
            "a_share": 0.25,
            "cash": 0.20,
            "star_50": 0.03,
            "nikkei": 0.00,
            "india": 0.00,
            "cb_bond": 0.02,
        },
        current_weights={
            "oil": 0.00,
            "gold": 0.00,
            "bond": 0.00,
            "nasdaq": 0.00,
            "hstech": 0.00,
            "a_share": 0.00,
            "cash": 1.00,
            "star_50": 0.00,
            "nikkei": 0.00,
            "india": 0.00,
            "cb_bond": 0.00,
        },
        mode_override=ModeOverride(
            mode="normal",
            note="默认兜底配置 — 宏观数据不可用时使用",
        ),
        summary="宏观数据不可用，使用默认保守配置：30% A股 + 30% 债券 + 20% 现金 + 10% 黄金 + 10% 恒生科技。",
    )


def load_allocation(path: Optional[str] = None) -> AssetAllocation:
    """Load asset_allocation.json from disk, fall back to default if missing."""
    default_path = Path("D:/AgentAlpha/my-bot/data/output/asset_allocation.json")
    filepath = Path(path) if path else default_path

    if not filepath.exists():
        return create_default_allocation()

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        allocation = dict_to_signal(data)
        is_valid, errors = validate_signal(allocation)

        if not is_valid:
            print(f"[WARN] asset_allocation.json validation errors: {errors}")
            print("[WARN] Falling back to default allocation")
            return create_default_allocation()

        return allocation
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        print(f"[ERROR] Failed to load asset_allocation.json: {e}")
        return create_default_allocation()


def save_allocation(signal: AssetAllocation, path: Optional[str] = None) -> bool:
    """Save AssetAllocation to disk as JSON."""
    default_path = Path("D:/AgentAlpha/my-bot/data/output/asset_allocation.json")
    filepath = Path(path) if path else default_path

    is_valid, errors = validate_signal(signal)
    if not is_valid:
        print(f"[ERROR] Validation failed, refusing to save: {errors}")
        return False

    filepath.parent.mkdir(parents=True, exist_ok=True)
    json_str = signal_to_json(signal)
    filepath.write_text(json_str, encoding="utf-8")

    print(f"[OK] Asset allocation saved to {filepath}")
    return True