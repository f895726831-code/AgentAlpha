"""
MAP-Elites Factor Archive — 按结构类别分桶竞争，保护因子多样性。

v4 核心创新: 替代全局 IC 排序的 Pool，每个结构类别独立竞争 Top-K。
因子可以属于多个类别（如 Ts_Mean($close, 20) + $volume 同时属于
price_temporal 和 multi_field）。

用法:
    from .archive import MAPElitesArchive

    archive = MAPElitesArchive(config)
    archive.insert(formula, ic_mean, fields, operators)
    best_in_category = archive.get_top("price_temporal")
    coverage = archive.coverage_stats()
"""

from __future__ import annotations
import re
import math
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════
# 档案类别定义
# ═══════════════════════════════════════════════════════════════

CATEGORY_SPECS = {
    "returns_temporal": {
        "fields": {"returns"},
        "ops_pattern": r"Ts_|Delay|Delta|Return",
        "description": "$returns × 时序算子",
        "min_ic": 0.02,
    },
    "price_temporal": {
        "fields": {"close", "open", "high", "low"},
        "ops_pattern": r"Ts_|Delay|Delta",
        "description": "$close/$open/$high/$low × 时序",
        "min_ic": 0.02,
    },
    "volume_temporal": {
        "fields": {"volume"},
        "ops_pattern": r"Ts_|Delta",
        "description": "$volume × 时序",
        "min_ic": 0.015,
    },
    "vwap_cross": {
        "fields": {"vwap"},
        "description": "$vwap × 跨字段",
        "min_ic": 0.015,
    },
    "multi_field": {
        "min_fields": 2,
        "description": "多字段组合",
        "min_ic": 0.02,
    },
    "nonlinear": {
        "ops_pattern": r"SignedPower|Abs|Log|Sign|Scale|Quantile",
        "description": "非线性变换",
        "min_ic": 0.02,
    },
}


@dataclass
class ArchivedFactor:
    """档案中的一个因子条目"""
    formula: str
    ic_mean: float
    icir: float = 0.0
    fields: Set[str] = field(default_factory=set)
    operators: Set[str] = field(default_factory=set)
    struct_fingerprint: str = ""
    reward: float = 0.0
    categories: List[str] = field(default_factory=list)


class MAPElitesArchive:
    """
    MAP-Elites 因子档案库。

    每个结构类别独立维护 Top-K 因子。
    支持多标签（一个因子可属于多个类别）。
    自动建立新类别（orphan 因子不属于任何已有类别时）。

    用法:
        archive = MAPElitesArchive(top_k=5)
        archive.insert(formula, ic_mean, fields, operators)
        stats = archive.coverage_stats()
    """

    def __init__(self, top_k: int = 5, min_ic: float = 0.015):
        self.top_k = top_k
        self.min_ic = min_ic

        # 类别 → [ArchivedFactor] (按 |IC| 降序)
        self._categories: Dict[str, List[ArchivedFactor]] = {}
        for cat_name in CATEGORY_SPECS:
            self._categories[cat_name] = []

        # 全局索引: formula → ArchivedFactor
        self._all_factors: Dict[str, ArchivedFactor] = {}

        # 结构指纹索引: structural_fingerprint → True (用于快速去重)
        self._fingerprints: Set[str] = set()

        # 总插入计数
        self.total_insertions = 0
        self.accepted_count = 0

    # ── 公共 API ──

    def classify(
        self,
        fields: Set[str],
        operators: Set[str],
    ) -> List[str]:
        """
        将一组 (fields, operators) 分类到档案类别。

        返回: 匹配的类别名列表（可能为空，也可能多个）
        """
        matches = []

        for cat_name, spec in CATEGORY_SPECS.items():
            if self._matches_category(fields, operators, cat_name, spec):
                matches.append(cat_name)

        # 如果属于任何类别 → 自动建立新类别
        if not matches:
            cat_name = self._infer_new_category(fields, operators)
            matches.append(cat_name)

        return matches

    def insert(
        self,
        formula: str,
        ic_mean: float,
        fields: Set[str],
        operators: Set[str],
        struct_fingerprint: str = "",
        icir: float = 0.0,
        reward: float = 0.0,
    ) -> bool:
        """
        尝试将因子插入档案。

        参数:
            formula: 因子公式字符串
            ic_mean: 日均 Pearson IC
            fields: 使用的数据字段集合
            operators: 使用的操作符集合
            struct_fingerprint: 结构指纹 (L2 去重用)
            icir: ICIR (如有)
            reward: v4 综合奖励

        返回:
            True 如果因子被接受（至少加入了一个类别）
        """
        self.total_insertions += 1

        # 硬门槛: |IC| < min_ic → 拒绝
        if abs(ic_mean) < self.min_ic:
            return False

        # 结构指纹去重 (L2): 同一结构指纹只保留 |IC| 最大的
        if struct_fingerprint and struct_fingerprint in self._fingerprints:
            # 已存在同结构因子 → 如果新因子 IC 更高，替换
            if formula in self._all_factors:
                return False
            # 找到同指纹的已有因子
            existing = None
            for cat_factors in self._categories.values():
                for af in cat_factors:
                    if af.struct_fingerprint == struct_fingerprint:
                        existing = af
                        break
                if existing:
                    break
            if existing and abs(ic_mean) <= abs(existing.ic_mean):
                return False
            # 新因子 IC 更高 → 移除旧的，插入新的
            if existing:
                self._remove_from_all(existing)

        # 分类
        categories = self.classify(fields, operators)

        # 创建条目
        entry = ArchivedFactor(
            formula=formula,
            ic_mean=float(ic_mean),
            icir=float(icir),
            fields=fields,
            operators=operators,
            struct_fingerprint=struct_fingerprint,
            reward=float(reward),
            categories=categories,
        )

        # 插入到每个匹配类别
        accepted = False
        for cat_name in categories:
            if self._insert_into_category(cat_name, entry):
                accepted = True

        if accepted:
            self._all_factors[formula] = entry
            if struct_fingerprint:
                self._fingerprints.add(struct_fingerprint)
            self.accepted_count += 1

        return accepted

    def get_top(
        self,
        category: str,
        n: int = None,
    ) -> List[ArchivedFactor]:
        """获取某类别的 Top-N 因子"""
        if category not in self._categories:
            return []
        factors = self._categories[category]
        if n is None:
            n = self.top_k
        return factors[:n]

    def get_all_unique(self) -> List[ArchivedFactor]:
        """获取档案中所有唯一因子"""
        return sorted(
            self._all_factors.values(),
            key=lambda x: -abs(x.ic_mean),
        )

    def coverage_stats(self) -> dict:
        """
        档案覆盖度统计。

        返回:
            {
                "total_unique": int,           # 唯一因子总数
                "categories": {
                    "cat_name": {
                        "filled": int,          # 已填充数
                        "capacity": int,        # 容量 (top_k)
                        "fill_rate": float,     # 填充率
                        "max_ic": float,        # 该类最佳 IC
                        "top_formulas": [str],  # 该类 Top-3 公式
                    },
                    ...
                },
                "structure_diversity": int,     # 不同结构指纹数
                "field_usage": {field: int},    # 各字段使用次数
                "operator_usage": {op: int},    # 各操作符使用次数
            }
        """
        cat_stats = {}
        for cat_name, factors in self._categories.items():
            cat_stats[cat_name] = {
                "filled": min(len(factors), self.top_k),
                "capacity": self.top_k,
                "fill_rate": min(len(factors), self.top_k) / self.top_k,
                "max_ic": factors[0].ic_mean if factors else 0.0,
                "top_formulas": [f.formula for f in factors[:3]],
            }

        # 字段和操作符使用统计
        field_usage: Dict[str, int] = {}
        op_usage: Dict[str, int] = {}
        for af in self._all_factors.values():
            for field in af.fields:
                field_usage[field] = field_usage.get(field, 0) + 1
            for op in af.operators:
                op_usage[op] = op_usage.get(op, 0) + 1

        return {
            "total_unique": len(self._all_factors),
            "categories": cat_stats,
            "structure_diversity": len(self._fingerprints),
            "field_usage": field_usage,
            "operator_usage": op_usage,
        }

    def reset(self):
        """重置档案（用于新实验）"""
        for cat_name in self._categories:
            self._categories[cat_name] = []
        self._all_factors.clear()
        self._fingerprints.clear()
        self.total_insertions = 0
        self.accepted_count = 0

    # ── 内部方法 ──

    @staticmethod
    def _matches_category(
        fields: Set[str],
        operators: Set[str],
        cat_name: str,
        spec: dict,
    ) -> bool:
        """检查 (fields, operators) 是否匹配某个类别"""
        # 字段匹配
        if "fields" in spec:
            if not fields:
                return False
            has_match = bool(fields & spec["fields"])
            if not has_match and "min_fields" not in spec:
                return False

        # 操作符模式匹配
        if "ops_pattern" in spec:
            pattern = spec["ops_pattern"]
            if not operators:
                return False
            has_op_match = any(re.search(pattern, op, re.IGNORECASE) for op in operators)
            if not has_op_match:
                return False

        # 最小字段数匹配
        if "min_fields" in spec:
            if len(fields) < spec["min_fields"]:
                return False

        return True

    def _infer_new_category(
        self,
        fields: Set[str],
        operators: Set[str],
    ) -> str:
        """
        为 orphan 因子推断一个新类别名。

        规则: 基于主要字段 + 操作符类型
        例: $high × Rank → "high_unary"
        """
        if not fields and not operators:
            return "unknown"

        field_part = "_".join(sorted(fields)[:2]) if fields else "generic"
        op_part = self._dominant_op_type(operators) if operators else "identity"

        cat_name = f"auto_{field_part}_{op_part}"

        # 确保类别存在
        if cat_name not in self._categories:
            self._categories[cat_name] = []

        return cat_name

    @staticmethod
    def _dominant_op_type(operators: Set[str]) -> str:
        """推断操作符类型: temporal / nonlinear / arithmetic / comparison"""
        temporal = {"Ts_Mean", "Ts_Std", "Ts_Max", "Ts_Min", "Ts_Rank",
                    "Ts_Sum", "Ts_ArgMax", "Ts_ArgMin", "Delay", "Delta", "Return"}
        nonlinear = {"SignedPower", "Abs", "Log", "Sign", "Scale", "Quantile"}
        rank = {"Rank"}
        arithmetic = {"+", "-", "*", "/"}
        binary = {"Max", "Min"}

        ops_lower = {op.lower() for op in operators}

        if temporal & ops_lower:
            return "temporal"
        if nonlinear & ops_lower:
            return "nonlinear"
        if rank & ops_lower:
            return "rank"
        if arithmetic & ops_lower:
            return "arithmetic"
        if binary & ops_lower:
            return "binary"
        return "other"

    def _insert_into_category(
        self,
        cat_name: str,
        entry: ArchivedFactor,
    ) -> bool:
        """将因子插入某类别，保持 Top-K 排序"""
        if cat_name not in self._categories:
            self._categories[cat_name] = []

        factors = self._categories[cat_name]

        # 检查是否已存在相同公式
        for i, existing in enumerate(factors):
            if existing.formula == entry.formula:
                # 更新（如果 IC 更高）
                if abs(entry.ic_mean) > abs(existing.ic_mean):
                    factors[i] = entry
                    factors.sort(key=lambda x: -abs(x.ic_mean))
                return True

        # 插入新条目
        factors.append(entry)
        factors.sort(key=lambda x: -abs(x.ic_mean))

        # 裁剪到 Top-K
        while len(factors) > self.top_k:
            factors.pop()

        return True

    def _remove_from_all(self, entry: ArchivedFactor):
        """从所有类别中移除一个因子"""
        for cat_name in entry.categories:
            if cat_name in self._categories:
                self._categories[cat_name] = [
                    f for f in self._categories[cat_name]
                    if f.formula != entry.formula
                ]
        if entry.formula in self._all_factors:
            del self._all_factors[entry.formula]
        if entry.struct_fingerprint in self._fingerprints:
            self._fingerprints.discard(entry.struct_fingerprint)