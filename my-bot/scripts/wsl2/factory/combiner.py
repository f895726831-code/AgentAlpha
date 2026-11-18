"""
Factor Combiner v3.0 — AST 安全的多因子合成

核心改进 (vs v2.0 eval):
  - ffo.parser.parse() 替代 eval() — 安全, 可验证
  - 三种合成方法: IC 加权 / Rank Blend / 条件切换
  - 所有权重基于历史 IC
  - 所有输出通过 AST 安全验证

用法:
    from factory.combiner import FactorCombiner

    cb = FactorCombiner()
    combined, info = cb.combine_ic_weighted(
        [("Rank($close)", 0.04), ("Ts_Mean($returns,5)", 0.03)]
    )
"""

import math
from typing import Any, Dict, List, Optional, Tuple


# ═══════════════════════════════════════════════════════════
# FactorCombiner
# ═══════════════════════════════════════════════════════════

class FactorCombiner:
    """
    因子组合器 v3.0。

    所有方法返回 (combined_formula, info_dict)。
    combined_formula 是有效的 FFO 表达式字符串。

    用法:
        cb = FactorCombiner()
        combined, info = cb.combine_ic_weighted(factors)
        combined, info = cb.combine_rank_blend(factors)
        combined, info = cb.combine_conditional(factors)
    """

    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir
        self._max_factors = 10  # 最多合成因子数 (防止公式过长)

    # ═══ 公共 API ═══

    def combine_ic_weighted(
        self,
        factors: List[Tuple[str, float]],
        weights: List[float] = None,
        date: str = None,
    ) -> Tuple[str, Dict]:
        """
        IC 加权线性合成。

        算法:
          1. 取 |IC| 最大的前 N 个因子
          2. 权重 = abs(IC_i) / sum(abs(IC_j))  (或用户指定)
          3. 每个因子用 Scale() 标准化
          4. 组合: Scale(f1)*w1 + Scale(f2)*w2 + ...

        参数:
            factors: [(formula, ic), ...]
            weights: 自定义权重 (默认: 按 |IC|)
            date:    评估日期 (仅用于元数据, 不影响合成)

        返回:
            (combined_formula, {"weights": [...], "parsed_ok": bool, "n_factors": int})
        """
        # 截断到 max_factors
        factors = sorted(factors, key=lambda x: -abs(x[1]))[:self._max_factors]
        n = len(factors)

        if n == 0:
            raise ValueError("至少需要 1 个因子")
        if n == 1:
            return factors[0][0], {"weights": [1.0], "parsed_ok": True, "n_factors": 1}

        # 计算权重
        if weights is None:
            abs_ics = [abs(ic) for _, ic in factors]
            total = sum(abs_ics)
            if total == 0:
                weights = [1.0 / n] * n
            else:
                weights = [w / total for w in abs_ics]
        else:
            # 归一化用户提供的权重
            total = sum(abs(w) for w in weights[:n])
            weights = [abs(w) / total for w in weights[:n]]

        # 构建合成表达式: Scale(f1)*w1 + Scale(f2)*w2 + ...
        terms = []
        for (formula, _), weight in zip(factors, weights):
            # 标准化因子
            scaled = f"Scale({formula})"
            if abs(weight - 1.0) < 1e-10:
                terms.append(scaled)
            else:
                terms.append(f"({scaled} * {weight:.6f})")

        combined = " + ".join(terms)

        # 验证
        parsed_ok = self.validate(combined)

        return combined, {
            "weights": weights,
            "parsed_ok": parsed_ok,
            "n_factors": n,
            "method": "ic_weighted",
        }

    def combine_rank_blend(
        self,
        factors: List[Tuple[str, float]],
        date: str = None,
    ) -> Tuple[str, Dict]:
        """
        截面排名平均合成 (非参数, 对异常值鲁棒)。

        算法:
          对每个因子取 Rank(f_i), 然后平均排名:
            (Rank(f1) + Rank(f2) + ... + Rank(fN)) / N

        返回:
            (combined_formula, {"n_factors": int, "parsed_ok": bool})
        """
        factors = sorted(factors, key=lambda x: -abs(x[1]))[:self._max_factors]
        n = len(factors)

        if n == 0:
            raise ValueError("至少需要 1 个因子")
        if n == 1:
            # 单因子 Rank Blend 就是 Rank
            combined = f"Rank({factors[0][0]})"
        else:
            ranks = " + ".join(f"Rank({f[0]})" for f in factors)
            combined = f"({ranks}) / {n}"

        parsed_ok = self.validate(combined)

        return combined, {
            "n_factors": n,
            "parsed_ok": parsed_ok,
            "method": "rank_blend",
        }

    def combine_conditional(
        self,
        factors: List[Tuple[str, float]],
        condition_formula: str = None,
        date: str = None,
    ) -> Tuple[str, Dict]:
        """
        条件切换合成 (if-then-else)。

        如果提供 condition_formula, 使用它:
          If(condition, factor_best, factor_second)

        否则, 生成默认成交量条件:
          If($volume > Ts_Mean($volume, 20), factor_best, factor_second)

        返回:
            (combined_formula, {"condition": str, "parsed_ok": bool})
        """
        factors = sorted(factors, key=lambda x: -abs(x[1]))
        n = len(factors)

        if n < 2:
            if n == 1:
                return factors[0][0], {"condition": None, "parsed_ok": True, "n_factors": 1}
            raise ValueError("至少需要 2 个因子用于条件合成")

        # 取 IC 最高的两个
        best = factors[0][0]
        second = factors[1][0]

        if condition_formula is None:
            # 默认: 放量时用最佳因子, 缩量时用次佳
            condition_formula = "$volume > Ts_Mean($volume, 20)"

        # 验证条件公式
        cond_ok = self.validate(condition_formula)
        if not cond_ok:
            raise ValueError(f"条件公式无效: {condition_formula}")

        combined = f"If({condition_formula}, {best}, {second})"

        parsed_ok = self.validate(combined)

        return combined, {
            "condition": condition_formula,
            "true_branch": best,
            "false_branch": second,
            "parsed_ok": parsed_ok,
            "n_factors": n,
            "method": "conditional",
        }

    def combine_temporal(
        self,
        factors: List[Tuple[str, float]],
        window: int = 20,
        date: str = None,
    ) -> Tuple[str, Dict]:
        """
        滚动 IC 加权动态组合 (时间平滑)。

        算法:
          1. 先做 IC 加权合成: Scale(f1)*w1 + Scale(f2)*w2 + ...
          2. 再做时序平滑: Ts_Mean(combined, window)

        返回:
            (combined_formula, {"window": int, "parsed_ok": bool})
        """
        # 先做 IC 加权
        base_formula, info = self.combine_ic_weighted(factors, date=date)

        if info["n_factors"] == 1:
            # 单因子: 直接时序平滑
            combined = f"Ts_Mean({base_formula}, {window})"
        else:
            combined = f"Ts_Mean({base_formula}, {window})"

        parsed_ok = self.validate(combined)

        return combined, {
            **info,
            "window": window,
            "parsed_ok": parsed_ok,
            "method": "temporal",
        }

    def validate(self, formula: str) -> bool:
        """
        验证公式可被 FFO parser 安全解析。

        返回 True 表示公式语法有效。
        """
        try:
            from ffo.parser import parse as ffo_parse
            ffo_parse(formula)
            return True
        except Exception:
            return False

    # ═══ 权重优化 ═══

    def optimize_weights(
        self,
        factors: List[str],
        start: str = "2020-01-01",
        end: str = "2020-12-31",
        universe: str = "csi500",
    ) -> List[float]:
        """
        基于历史 IC 协方差矩阵优化权重。

        简化版 Markowitz: 最大化 IR = w·μ / sqrt(w·Σ·w)
        其中 μ 是各因子 IC 均值向量, Σ 是 IC 协方差矩阵。

        返回:
            最优权重向量 (归一化到和为 1)
        """
        import numpy as np
        from ffo.evaluator import FactorEvaluator

        n = len(factors)
        if n <= 1:
            return [1.0] * n

        evaluator = FactorEvaluator(data_dir=self.data_dir)

        # 收集 IC 序列
        ic_series = {}
        for formula in factors:
            result = evaluator.evaluate(formula, start, end, universe)
            if result.error or result.n_days == 0:
                continue
            # 用 Rank IC 更稳定
            ic_series[formula] = result.rank_ic_mean

        if len(ic_series) < 2:
            # 回退到等权重
            return [1.0 / n] * n

        # 构建 IC 均值向量
        mu = np.array([ic_series.get(f, 0.0) for f in factors])

        # 简化: 使用对角协方差 (假设因子 IC 独立)
        # 完整协方差需要 IC 日序列, 这里用简化方法
        sigma = np.eye(n) * 0.01  # 对角协方差近似

        # 等权重作为初始值
        w0 = np.ones(n) / n

        try:
            # 简化求解: w ∝ μ / σ² (对角情况下的解析解)
            w_opt = mu / np.diag(sigma)
            w_opt = np.maximum(w_opt, 0)  # 非负权重
            if w_opt.sum() > 0:
                w_opt = w_opt / w_opt.sum()
            else:
                w_opt = w0
        except Exception:
            w_opt = w0

        return w_opt.tolist()


# ═══════════════════════════════════════════════════════════
# 便捷函数
# ═══════════════════════════════════════════════════════════

def combine_ic_weighted(
    factors: List[Tuple[str, float]],
    weights: List[float] = None,
    data_dir: str = None,
) -> Tuple[str, Dict]:
    """便捷函数: IC 加权合成"""
    cb = FactorCombiner(data_dir=data_dir)
    return cb.combine_ic_weighted(factors, weights=weights)


def combine_rank_blend(
    factors: List[Tuple[str, float]],
    data_dir: str = None,
) -> Tuple[str, Dict]:
    """便捷函数: Rank Blend 合成"""
    cb = FactorCombiner(data_dir=data_dir)
    return cb.combine_rank_blend(factors)


def combine_conditional(
    factors: List[Tuple[str, float]],
    condition: str = None,
    data_dir: str = None,
) -> Tuple[str, Dict]:
    """便捷函数: 条件组合"""
    cb = FactorCombiner(data_dir=data_dir)
    return cb.combine_conditional(factors, condition_formula=condition)