"""
因子元数据注册表 — JSON 持久化的因子元数据管理系统

提供:
  - FactorMetadata: 因子元数据 dataclass
  - FactorRegistry: JSON 文件支持的因子注册表 (CRUD + 持久化)

存储格式: data/factor_registry.json

用法:
    from factory.metadata import FactorRegistry

    registry = FactorRegistry()
    registry.register("Rank($close)", channel="gflownet", ic=0.04)
    active = registry.list_active(min_ic=0.02)
    registry.save()
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════
# FactorMetadata
# ═══════════════════════════════════════════════════════════

@dataclass
class FactorMetadata:
    """单个因子的元数据"""

    formula: str                          # 原始因子公式
    normalized: str                       # 规范化形式 (唯一键)
    source_channel: str                   # "gflownet" | "llm_agent" | "rd_agent" | "merged"
    ic_mean: float                        # 当前最佳 |IC|
    ic_history: List[Dict] = field(default_factory=list)
    # ic_history: [{"date": "2026-10-13", "ic": 0.036, "evaluator": "ffo"}, ...]

    discovery_date: str = ""              # ISO 日期
    operators_used: List[str] = field(default_factory=list)
    complexity: int = 0                   # AST 节点数
    last_evaluated: str = ""              # ISO 时间戳
    tags: List[str] = field(default_factory=list)

    @classmethod
    def from_formula(
        cls,
        formula: str,
        ic: float,
        channel: str,
        operators: List[str] = None,
    ) -> "FactorMetadata":
        """从公式字符串构建元数据"""
        from factory.dedup import normalize_formula

        now = datetime.now().isoformat()
        normalized = normalize_formula(formula)

        if operators is None:
            operators = _extract_operators(formula)

        complexity = _compute_complexity(formula)

        return cls(
            formula=formula,
            normalized=normalized,
            source_channel=channel,
            ic_mean=abs(ic),
            ic_history=[{
                "date": datetime.now().strftime("%Y-%m-%d"),
                "ic": ic,
                "evaluator": "ffo",
            }],
            discovery_date=datetime.now().strftime("%Y-%m-%d"),
            operators_used=operators,
            complexity=complexity,
            last_evaluated=now,
        )

    def add_ic_record(self, ic: float, evaluator: str = "ffo"):
        """追加 IC 评估记录"""
        self.ic_history.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "ic": ic,
            "evaluator": evaluator,
        })
        if abs(ic) > abs(self.ic_mean):
            self.ic_mean = abs(ic)
        self.last_evaluated = datetime.now().isoformat()

    def to_dict(self) -> Dict:
        """序列化为字典"""
        return {
            "formula": self.formula,
            "normalized": self.normalized,
            "source_channel": self.source_channel,
            "ic_mean": self.ic_mean,
            "ic_history": self.ic_history,
            "discovery_date": self.discovery_date,
            "operators_used": self.operators_used,
            "complexity": self.complexity,
            "last_evaluated": self.last_evaluated,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "FactorMetadata":
        """从字典反序列化"""
        return cls(
            formula=d.get("formula", ""),
            normalized=d.get("normalized", ""),
            source_channel=d.get("source_channel", ""),
            ic_mean=d.get("ic_mean", 0.0),
            ic_history=d.get("ic_history", []),
            discovery_date=d.get("discovery_date", ""),
            operators_used=d.get("operators_used", []),
            complexity=d.get("complexity", 0),
            last_evaluated=d.get("last_evaluated", ""),
            tags=d.get("tags", []),
        )


# ═══════════════════════════════════════════════════════════
# FactorRegistry
# ═══════════════════════════════════════════════════════════

class FactorRegistry:
    """
    JSON 文件支持的因子注册表。

    存储位置: data/factor_registry.json (相对于 wsl2 目录)

    用法:
        registry = FactorRegistry()
        registry.register("Rank($close)", channel="gflownet", ic=0.04)
        registry.list_active(min_ic=0.02)
    """

    def __init__(self, storage_path: str = None):
        """
        参数:
            storage_path: JSON 存储路径。默认: data/factor_registry.json
                          相对于 wsl2 目录, 除非给出绝对路径
        """
        if storage_path is None:
            # 默认路径: wsl2/data/factor_registry.json
            base = Path(__file__).resolve().parent.parent
            storage_path = str(base / "data" / "factor_registry.json")

        self.storage_path = storage_path
        self._index: Dict[str, FactorMetadata] = {}  # normalized → metadata
        self._load()

    # ═══ CRUD ═══

    def register(
        self,
        formula: str,
        channel: str,
        ic: float,
        operators: List[str] = None,
        evaluation: Dict = None,
    ) -> FactorMetadata:
        """
        注册因子。

        - 如果 normalized key 已存在: 追加 IC 历史, 更新 channel 为 "merged"
        - 如果不存在: 创建新记录

        返回: FactorMetadata
        """
        from factory.dedup import normalize_formula

        key = normalize_formula(formula)

        if key in self._index:
            # 已存在: 追加 IC 历史
            meta = self._index[key]
            meta.add_ic_record(ic)
            if channel != meta.source_channel and meta.source_channel != "merged":
                meta.source_channel = "merged"
            if operators:
                for op in operators:
                    if op not in meta.operators_used:
                        meta.operators_used.append(op)
        else:
            # 新因子
            meta = FactorMetadata.from_formula(
                formula=formula, ic=ic, channel=channel, operators=operators,
            )
            self._index[key] = meta

        self._save()
        return meta

    def get(self, factor_id: str) -> Optional[FactorMetadata]:
        """
        按 ID 获取因子。

        factor_id 可以是规范化字符串或原始公式。
        """
        from factory.dedup import normalize_formula

        key = normalize_formula(factor_id)
        return self._index.get(key)

    def list_active(self, min_ic: float = 0.02) -> List[FactorMetadata]:
        """列出 |IC| >= min_ic 的活跃因子, 按 |IC| 降序"""
        active = [m for m in self._index.values()
                  if abs(m.ic_mean) >= min_ic]
        active.sort(key=lambda m: -abs(m.ic_mean))
        return active

    def get_all(self) -> List[FactorMetadata]:
        """返回所有已注册因子, 按 |IC| 降序"""
        all_factors = list(self._index.values())
        all_factors.sort(key=lambda m: -abs(m.ic_mean))
        return all_factors

    def get_by_channel(self, channel: str) -> List[FactorMetadata]:
        """按源通道筛选"""
        result = [m for m in self._index.values()
                   if m.source_channel == channel]
        result.sort(key=lambda m: -abs(m.ic_mean))
        return result

    def get_top(self, n: int = 10, min_abs_ic: float = 0.02) -> List[FactorMetadata]:
        """获取前 N 个因子"""
        active = self.list_active(min_ic=min_abs_ic)
        return active[:n]

    def merge_pool(
        self,
        pool: List[Tuple[str, float]],
        channel: str,
    ):
        """
        从通道因子池批量注册。

        参数:
            pool: [(formula, ic), ...]
            channel: 通道标签
        """
        for formula, ic in pool:
            self.register(formula, channel=channel, ic=ic)

    def merge_pools(
        self,
        pools: Dict[str, List[Tuple[str, float]]],
    ) -> List[FactorMetadata]:
        """
        合并多通道因子池 → 去重 → 注册。

        参数:
            pools: {"gflownet": [(f, ic), ...], "agent": [(f, ic), ...]}

        返回:
            新注册/更新的 FactorMetadata 列表
        """
        from factory.dedup import deduplicate_pool

        # 打平为 tagged 格式
        all_tagged = []
        for channel, pool in pools.items():
            for formula, ic in pool:
                all_tagged.append((formula, ic, channel))

        # 去重
        deduped = deduplicate_pool(all_tagged, mode="smart")

        # 注册
        results = []
        for formula, ic, channel in deduped:
            meta = self.register(formula, channel=channel, ic=ic)
            results.append(meta)

        return results

    def export_summary(self) -> Dict:
        """导出注册表汇总统计"""
        all_factors = self.get_all()
        channels = {}
        operators = {}
        total_ic = 0.0

        for m in all_factors:
            ch = m.source_channel
            channels[ch] = channels.get(ch, 0) + 1
            total_ic += abs(m.ic_mean)
            for op in m.operators_used:
                operators[op] = operators.get(op, 0) + 1

        return {
            "total_factors": len(all_factors),
            "active_factors": len([m for m in all_factors if abs(m.ic_mean) >= 0.02]),
            "mean_abs_ic": total_ic / max(len(all_factors), 1),
            "by_channel": channels,
            "top_operators": sorted(operators.items(), key=lambda x: -x[1])[:10],
            "top_factors": [
                {"formula": m.formula, "ic": m.ic_mean, "channel": m.source_channel}
                for m in all_factors[:5]
            ],
            "updated_at": datetime.now().isoformat(),
        }

    def clear(self):
        """清空注册表"""
        self._index.clear()
        self._save()

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, formula: str) -> bool:
        from factory.dedup import normalize_formula
        return normalize_formula(formula) in self._index

    # ═══ 持久化 ═══

    def _load(self):
        """从 JSON 文件加载"""
        if not os.path.exists(self.storage_path):
            self._index = {}
            return

        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            self._index = {}
            return

        factors_data = data.get("factors", {})
        self._index = {}
        for key, meta_dict in factors_data.items():
            try:
                self._index[key] = FactorMetadata.from_dict(meta_dict)
            except Exception:
                # 跳过损坏的条目
                continue

    def save(self):
        """别名—— 与外部调用兼容"""
        self._save()

    def _save(self):
        """持久化到 JSON 文件"""
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

        data = {
            "version": 2,
            "updated_at": datetime.now().isoformat(),
            "total_factors": len(self._index),
            "factors": {
                key: meta.to_dict()
                for key, meta in self._index.items()
            },
        }

        # 原子写入: 先写临时文件, 再重命名
        tmp_path = self.storage_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

        os.replace(tmp_path, self.storage_path)


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _extract_operators(formula: str) -> List[str]:
    """从公式字符串提取使用的操作符"""
    ops = set()

    # 函数调用
    for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(', formula):
        name = m.group(1)
        # 过滤掉常见非操作符
        if name.lower() not in ("", "if"):
            ops.add(name)

    # 特殊处理 If
    if re.search(r'\bIf\s*\(', formula, re.IGNORECASE):
        ops.add("If")

    # 中缀操作符
    infix_ops = {'+', '-', '*', '/', '>', '<', '>=', '<=', '==', '!=', '&', '|'}
    for op in infix_ops:
        if op in formula:
            ops.add(op)

    return sorted(ops)


def _compute_complexity(formula: str) -> int:
    """
    计算因子复杂度 (AST 节点数)。

    回退: 如果不能解析 AST, 用操作符数量估算。
    """
    try:
        from ffo.parser import parse as ffo_parse
        ast = ffo_parse(formula)
        return _count_ast_nodes(ast)
    except Exception:
        # 回退: 操作符数量 * 2
        return len(_extract_operators(formula)) * 2


def _count_ast_nodes(node: Any) -> int:
    """递归计算 AST 节点数"""
    from ffo.parser import FieldRef, Number, BinOp, UnaryOp, FuncCall

    if isinstance(node, (FieldRef, Number)):
        return 1
    if isinstance(node, UnaryOp):
        return 1 + _count_ast_nodes(node.operand)
    if isinstance(node, BinOp):
        return 1 + _count_ast_nodes(node.left) + _count_ast_nodes(node.right)
    if isinstance(node, FuncCall):
        return 1 + sum(_count_ast_nodes(a) for a in node.args)
    return 1


# ═══════════════════════════════════════════════════════════
# 模块级便捷函数
# ═══════════════════════════════════════════════════════════

_default_registry: Optional[FactorRegistry] = None


def get_registry(storage_path: str = None) -> FactorRegistry:
    """获取全局注册表实例"""
    global _default_registry
    if _default_registry is None:
        _default_registry = FactorRegistry(storage_path)
    return _default_registry