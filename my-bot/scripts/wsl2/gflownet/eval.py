"""
多目标评价体系 — 替代单一 max IC 评价，全面诊断 GFlowNet 训练质量。

v4 核心创新: 从多个维度评估生成质量，包括结构多样性、有效因子数、
策略熵（检测模式坍缩）、组合边际 IR 等。

用法:
    from .eval import MultiObjectiveEvaluator

    evaluator = MultiObjectiveEvaluator()
    evaluator.record_episode(factors, rewards, policy_entropy)
    report = evaluator.summary()
"""

from __future__ import annotations
import math
from typing import Dict, List, Optional, Tuple
from collections import defaultdict

import numpy as np


class MultiObjectiveEvaluator:
    """
    多目标评价器 — 追踪 v4 训练质量。

    在每个 episode 结束时调用 record_episode()，
    训练结束后调用 summary() 获取完整报告。

    指标:
      - best_oos_ic: 最高样本外 IC
      - best_oos_icir: 最高样本外 ICIR
      - unique_structures: 唯一结构指纹数
      - effective_factors: IC>0.03 且 pairwise_corr<0.7 的因子数
      - mean_pairwise_corr: 因子间平均相关性
      - incremental_portfolio_ir: 加入组合后的边际 IR 改善
      - valid_generation_rate: 每 batch 有效因子比例
      - category_coverage: 各类别 Top-K 填充率
      - policy_entropy: 策略熵（检测模式坍缩）
    """

    def __init__(self):
        self.history: List[dict] = []

        # 跨 episode 累积
        self._all_factors: Dict[str, dict] = {}     # formula → {ic, fields, ops, fingerprint}
        self._fingerprints_seen: set = set()
        self._category_fill_history: List[dict] = []
        self._valid_counts: List[int] = []
        self._total_counts: List[int] = []

    # ── 公共 API ──

    def record_episode(
        self,
        episode: int,
        valid_formulas: List[Tuple[str, float]],  # [(formula, reward), ...]
        total_sampled: int,
        archive=None,  # Optional[MAPElitesArchive]
        policy_entropy: float = 0.0,
        pool_max_ic: float = 0.0,
        pool_size: int = 0,
        factor_pool: List[Tuple[str, float]] = None,  # [(formula, ic), ...]
    ):
        """
        记录一个 episode 的评价指标。

        参数:
            episode: 当前 episode 编号 (1-indexed)
            valid_formulas: 本 episode 生成的有效因子 [(formula, reward)]
            total_sampled: 本 episode 采样的总轨迹数
            archive: MAPElitesArchive 实例 (可选)
            policy_entropy: 策略熵 (从 policy 分布计算)
            pool_max_ic: 全局池最佳 IC
            pool_size: 全局池大小
            factor_pool: 全局因子池 [(formula, ic)]
        """
        n_valid = len(valid_formulas)
        n_total = max(total_sampled, 1)
        valid_rate = n_valid / n_total

        self._valid_counts.append(n_valid)
        self._total_counts.append(n_total)

        # 累积因子
        if factor_pool:
            for formula, ic in factor_pool:
                if formula not in self._all_factors:
                    self._all_factors[formula] = {
                        "ic": float(ic),
                        "fields": _extract_fields(formula),
                        "operators": _extract_operators(formula),
                        "fingerprint": _structural_fingerprint(formula),
                    }
                    fp = self._all_factors[formula]["fingerprint"]
                    if fp:
                        self._fingerprints_seen.add(fp)

        # 有效因子数 (IC>0.03, pairwise_corr<0.7)
        eff_count = _count_effective_factors(self._all_factors)

        # 平均 pairwise 相关性
        mean_corr = _mean_pairwise_corr(self._all_factors) if len(self._all_factors) >= 2 else 0.0

        # Archive 覆盖度
        category_stats = {}
        if archive is not None:
            stats = archive.coverage_stats()
            category_stats = {
                cat: info["fill_rate"]
                for cat, info in stats["categories"].items()
            }

        record = {
            "episode": episode,
            "valid_rate": round(valid_rate, 4),
            "pool_max_ic": round(pool_max_ic, 4),
            "pool_size": pool_size,
            "unique_structures": len(self._fingerprints_seen),
            "total_unique_factors": len(self._all_factors),
            "effective_factors": eff_count,
            "mean_pairwise_corr": round(mean_corr, 4),
            "policy_entropy": round(policy_entropy, 4),
            "category_fill_rates": category_stats,
        }

        self.history.append(record)

    def summary(self) -> dict:
        """
        生成训练质量综合报告。

        返回:
            {
                "final_metrics": dict,       # 最后一个 episode 的指标
                "best_metrics": dict,        # 全训练过程中的最佳值
                "trends": dict,              # 趋势分析
                "mode_collapse_detected": bool,  # 是否检测到模式坍缩
                "recommendations": [str],    # 改进建议
            }
        """
        if not self.history:
            return {"error": "no data recorded"}

        final = self.history[-1]

        # 最佳值
        best_pool_max_ic = max(h["pool_max_ic"] for h in self.history)
        best_eff_factors = max(h["effective_factors"] for h in self.history)
        best_valid_rate = max(h["valid_rate"] for h in self.history)
        best_structures = max(h["unique_structures"] for h in self.history)

        best_metrics = {
            "best_pool_max_ic": best_pool_max_ic,
            "best_effective_factors": best_eff_factors,
            "best_valid_rate": round(best_valid_rate, 4),
            "best_unique_structures": best_structures,
        }

        # 趋势分析
        trends = self._analyze_trends()

        # 模式坍缩检测
        mode_collapse = self._detect_mode_collapse(trends)

        # 建议
        recommendations = self._generate_recommendations(
            final, best_metrics, trends, mode_collapse
        )

        return {
            "final_metrics": final,
            "best_metrics": best_metrics,
            "trends": trends,
            "mode_collapse_detected": mode_collapse,
            "recommendations": recommendations,
        }

    # ── 内部方法 ──

    def _analyze_trends(self) -> dict:
        """分析各指标的训练趋势"""
        if len(self.history) < 2:
            return {"note": "insufficient data for trend analysis"}

        first = self.history[0]
        last = self.history[-1]

        # 有效因子增长率趋势 (最后 20% vs 前 20%)
        n = len(self.history)
        split = max(1, n // 5)
        early_eff = np.mean([h["effective_factors"] for h in self.history[:split]])
        late_eff = np.mean([h["effective_factors"] for h in self.history[-split:]])

        # 有效生成率趋势
        early_vr = np.mean([h["valid_rate"] for h in self.history[:split]])
        late_vr = np.mean([h["valid_rate"] for h in self.history[-split:]])

        # 策略熵趋势 (检测坍缩)
        early_entropy = np.mean([h["policy_entropy"] for h in self.history[:split]
                                 if h["policy_entropy"] > 0])
        late_entropy = np.mean([h["policy_entropy"] for h in self.history[-split:]
                                if h["policy_entropy"] > 0])

        # IC 停滞检测
        ic_values = [h["pool_max_ic"] for h in self.history]
        ic_stagnant = _is_stagnant(ic_values, threshold=0.001, window=30)

        return {
            "structure_growth": last["unique_structures"] - first["unique_structures"],
            "effective_factor_trend": "improving" if late_eff > early_eff + 1 else
                                      "declining" if late_eff < early_eff - 1 else "flat",
            "valid_rate_trend": "improving" if late_vr > early_vr * 1.1 else
                               "declining" if late_vr < early_vr * 0.9 else "stable",
            "policy_entropy_early": round(early_entropy, 4),
            "policy_entropy_late": round(late_entropy, 4),
            "entropy_trend": "collapsing" if (late_entropy > 0 and
                                             early_entropy > 0 and
                                             late_entropy < early_entropy * 0.5)
                            else "stable",
            "ic_stagnant": ic_stagnant,
            "ic_stagnated_at_ep": _stagnation_start(ic_values, threshold=0.001),
        }

    def _detect_mode_collapse(self, trends: dict) -> bool:
        """检测是否发生模式坍缩"""
        signals = 0

        # 信号 1: 结构多样性停止增长
        if trends.get("structure_growth", 1) == 0:
            signals += 1

        # 信号 2: 有效因子数下降
        if trends.get("effective_factor_trend") == "declining":
            signals += 1

        # 信号 3: 策略熵急剧下降
        if trends.get("entropy_trend") == "collapsing":
            signals += 1

        # 信号 4: IC 停滞
        if trends.get("ic_stagnant"):
            signals += 1

        # 信号 5: 有效生成率下降
        if trends.get("valid_rate_trend") == "declining":
            signals += 1

        return signals >= 3  # 3+ 信号 → 模式坍缩确认

    def _generate_recommendations(
        self,
        final: dict,
        best: dict,
        trends: dict,
        mode_collapse: bool,
    ) -> List[str]:
        """生成改进建议"""
        recs = []

        if mode_collapse:
            recs.append(
                "⚠️ MODE COLLAPSE DETECTED: 3+ collapse signals. "
                "Consider: (1) increase L3 novelty weight, "
                "(2) raise exploration channel ratio in dual-channel sampler, "
                "(3) lower L1 min_ic to allow broader exploration."
            )

        if trends.get("ic_stagnant"):
            ep = trends.get("ic_stagnated_at_ep", "?")
            recs.append(
                f"IC stagnated at episode {ep}. "
                "Suggest: sharper L2 stability penalty or higher L3 structure bonus."
            )

        if final["unique_structures"] < 5 and final["pool_size"] > 20:
            recs.append(
                f"Low structure diversity ({final['unique_structures']} unique / "
                f"{final['pool_size']} total). MAP-Elites archive may need "
                f"higher top_k or relaxed category matching."
            )

        if final["mean_pairwise_corr"] > 0.5:
            recs.append(
                f"High mean pairwise correlation ({final['mean_pairwise_corr']:.2f}). "
                "L3 behavioral dedup (correlation >0.85 filter) should help."
            )

        # 各类别填充率
        fill_rates = final.get("category_fill_rates", {})
        empty_cats = [cat for cat, rate in fill_rates.items() if rate == 0]
        if empty_cats:
            recs.append(
                f"Empty categories: {', '.join(empty_cats)}. "
                "Consider: (1) seed pool needs factors in these categories, "
                "(2) exploration channel bias toward uncovered fields/ops."
            )

        if not recs:
            recs.append("Training looks healthy. All diversity signals positive.")

        return recs

    def export_history(self) -> List[dict]:
        """导出完整历史记录 (用于序列化)"""
        return self.history


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _extract_fields(formula: str) -> set:
    """提取公式中的字段名"""
    import re
    pattern = re.compile(r'\$(close|open|high|low|volume|vwap|returns)', re.IGNORECASE)
    return set(pattern.findall(formula))


def _extract_operators(formula: str) -> set:
    """提取公式中的操作符名"""
    import re
    pattern = re.compile(
        r'\b(Rank|Scale|Sign|Abs|Log|Ts_Mean|Ts_Std|Ts_Max|Ts_Min|Ts_Rank|'
        r'Ts_Sum|Ts_ArgMax|Ts_ArgMin|Delay|Delta|Return|Quantile|SignedPower|'
        r'Max|Min|If|Ts_Corr|Ts_Cov)\b',
        re.IGNORECASE,
    )
    return {m.lower() for m in pattern.findall(formula)}


def _structural_fingerprint(formula: str) -> str:
    """
    生成结构指纹 — 参数分桶。

    例: SignedPower($returns, 20) → "SignedPower|returns|bucket_10_20"
        SignedPower($returns, 10) → "SignedPower|returns|bucket_10_20" (相同!)
    """
    try:
        from ffo.parser import parse as ffo_parse
        ast = ffo_parse(formula)
        return _ast_to_fingerprint(ast)
    except Exception:
        return ""


def _ast_to_fingerprint(node) -> str:
    """递归将 AST 转换为结构指纹字符串"""
    from ffo.parser import FieldRef, Number, BinOp, UnaryOp, FuncCall

    PARAM_BUCKETS = [
        (2, 5), (10, 20), (30, 60), (120, 250)
    ]

    def bucketize(value: float) -> str:
        for lo, hi in PARAM_BUCKETS:
            if lo <= value <= hi:
                return f"_{lo}_{hi}"
        return f"_{int(value)}"

    if isinstance(node, FieldRef):
        return node.name.lower()

    if isinstance(node, Number):
        return bucketize(node.value)

    if isinstance(node, UnaryOp):
        return f"(-{_ast_to_fingerprint(node.operand)})"

    if isinstance(node, BinOp):
        left = _ast_to_fingerprint(node.left)
        right = _ast_to_fingerprint(node.right)
        if node.op in ('+', '*'):
            return f"({min(left, right)}{node.op}{max(left, right)})"
        return f"({left}{node.op}{right})"

    if isinstance(node, FuncCall):
        name = node.name.lower()
        args = ",".join(_ast_to_fingerprint(a) for a in node.args)
        return f"{name}|{args}"

    return str(node)


def _count_effective_factors(
    all_factors: Dict[str, dict],
    ic_threshold: float = 0.03,
    corr_threshold: float = 0.7,
) -> int:
    """
    计算有效因子数: IC > threshold 且 pairwise correlation < corr_threshold。

    简化实现: 基于结构指纹，同指纹视为高度相关。
    完整实现需要因子收益序列（Phase 2 的 L3 行为去重）。
    """
    if len(all_factors) < 2:
        return len(all_factors)

    # 使用结构指纹近似去重
    fingerprints_seen = set()
    effective = 0
    for formula, info in all_factors.items():
        if abs(info["ic"]) < ic_threshold:
            continue
        fp = info.get("fingerprint", "")
        if fp and fp in fingerprints_seen:
            continue
        fingerprints_seen.add(fp)
        effective += 1

    return effective


def _mean_pairwise_corr(all_factors: Dict[str, dict]) -> float:
    """
    因子间平均相关性 (结构近似)。

    使用字段重叠 + 操作符重叠近似相关性。
    完整实现需因子收益序列。
    """
    formulas = list(all_factors.values())
    if len(formulas) < 2:
        return 0.0

    n = len(formulas)
    similarities = []
    for i in range(min(n, 50)):  # 采样不超过 50×50
        for j in range(i + 1, min(n, 50)):
            fi, fj = formulas[i], formulas[j]
            fi_fields = fi.get("fields", set()) or _extract_fields(str(fi))
            fj_fields = fj.get("fields", set()) or _extract_fields(str(fj))
            fi_ops = fi.get("operators", set()) or _extract_operators(str(fi))
            fj_ops = fj.get("operators", set()) or _extract_operators(str(fj))

            # Field Jaccard
            field_union = len(fi_fields | fj_fields)
            field_sim = len(fi_fields & fj_fields) / max(field_union, 1)

            # Op Jaccard
            op_union = len(fi_ops | fj_ops)
            op_sim = len(fi_ops & fj_ops) / max(op_union, 1)

            # 综合相似度
            sim = 0.6 * field_sim + 0.4 * op_sim
            similarities.append(sim)

    return float(np.mean(similarities)) if similarities else 0.0


def _is_stagnant(
    values: List[float],
    threshold: float = 0.001,
    window: int = 30,
) -> bool:
    """检测值序列是否停滞 (最近 window 步无显著变化)"""
    if len(values) < window:
        return False
    recent = values[-window:]
    return max(recent) - min(recent) < threshold


def _stagnation_start(
    values: List[float],
    threshold: float = 0.001,
) -> int:
    """返回停滞开始的 episode"""
    if len(values) < 2:
        return 1
    best = values[0]
    for i, v in enumerate(values):
        if v > best + threshold:
            best = v
            continue
        # 检查从 i 开始是否持续停滞
        remaining = values[i:]
        if len(remaining) >= 20 and (max(remaining) - min(remaining) < threshold):
            return i + 1  # 1-indexed
    return len(values)  # 未停滞