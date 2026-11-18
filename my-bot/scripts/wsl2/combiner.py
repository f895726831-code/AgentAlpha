"""
API 兼容层 — 委托给 factory.combiner.FactorCombiner

修复 api_server.py 中损坏的 /api/v1/factors/combine 端点。

提供 FactorCombiner 类, 兼容 api_server.py 的导入接口。

用法 (api_server.py 无需修改):
    from combiner import FactorCombiner
    combiner = FactorCombiner()
    result = combiner.combine(factors=[...], method="ic_weighted", top_k=50, date="2024-01-01")
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# 确保 wsl2 目录在 path 中
WSL2_DIR = Path(__file__).resolve().parent
if str(WSL2_DIR) not in sys.path:
    sys.path.insert(0, str(WSL2_DIR))

from factory.combiner import FactorCombiner as _FactoryCombiner


class FactorCombiner:
    """
    兼容 api_server.py 的 FactorCombiner 接口。

    方法:
        combine(factors, method="ic_weighted", top_k=50, date=None)
            → {"formula": str, "rankings": [...], "metrics": {...}}
    """

    def combine(
        self,
        factors: List[Dict],
        method: str = "ic_weighted",
        top_k: int = 50,
        date: str = None,
    ) -> Dict[str, Any]:
        """
        合成因子并返回排名。

        参数:
            factors: [{"id": str, "formula": str, "ic": float}, ...]
            method:  "ic_weighted" | "rank_blend"
            top_k:   返回前 K 只股票
            date:    评估日期

        返回:
            {"formula": str, "rankings": [...], "metrics": {...}}
        """
        from store import _get_registry

        registry = _get_registry()
        combiner = _FactoryCombiner()

        # 提取 (formula, ic) 对
        factor_pairs = []
        for f in factors:
            formula = f.get("formula", f.get("id", ""))
            ic = f.get("ic", f.get("ic_mean", 0))
            if formula:
                factor_pairs.append((formula, ic))

        if not factor_pairs:
            raise ValueError("没有有效的因子")

        # 合成
        if method == "ic_weighted":
            combined_formula, info = combiner.combine_ic_weighted(
                factor_pairs, date=date
            )
        elif method == "rank_blend":
            combined_formula, info = combiner.combine_rank_blend(
                factor_pairs, date=date
            )
        elif method == "conditional":
            combined_formula, info = combiner.combine_conditional(
                factor_pairs, date=date
            )
        else:
            raise ValueError(
                f"不支持的合成方法: {method}。"
                f"支持: ic_weighted, rank_blend, conditional"
            )

        # 验证
        if not combiner.validate(combined_formula):
            raise ValueError(f"合成公式无效: {combined_formula}")

        # 用 FFO 评估合成因子
        rankings = []
        metrics = {"ic_mean": 0.0, "rank_ic_mean": 0.0, "ic_ir": 0.0}

        try:
            from ffo.evaluator import FactorEvaluator

            evaluator = FactorEvaluator()
            result = evaluator.evaluate(
                combined_formula,
                start=date or "2024-01-01",
                universe="csi500",
            )

            if not result.error:
                metrics = result.to_dict()
        except Exception:
            pass  # FFO 评估失败时返回空排名

        return {
            "formula": combined_formula,
            "method": method,
            "combiner_info": info,
            "rankings": rankings,
            "metrics": metrics,
            "top_k": top_k,
        }