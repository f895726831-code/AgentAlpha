"""
因子验证器 — 三道关编排器

统一三道验证关，任何一道不通过即拒绝（或降级）。
三道关执行顺序不可跳过，确保"验证先于产能"。

Gate 1: 统计纪律 — 结果是试了多少次才出现的？
Gate 2: 样本外纪律 — 测试集只能碰一次
Gate 3: 经济逻辑 — 谁在亏钱给你？他为什么会继续亏？

用法:
    from src.factors.validator import FactorValidator

    validator = FactorValidator(strict=True)
    result = validator.validate({
        "factor_id": "factor_001",
        "factor_name": "momentum_20d",
        "backtest_result": { ... },
        "ic_series": [0.03, 0.02, ...],
        "factor_code": "def momentum(df): ...",
    }, hypothesis_id="hyp_20260904_001")

    print(result.verdict)  # Deploy | Refine | Abandon

CLI:
    python -m src.factors.validator --factor-id factor_001 --date 2026-09-04
    python -m src.factors.validator --batch --factor-source paper
"""

import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── 数据类 ───


@dataclass
class GateResult:
    """单道关的结果"""

    gate: int
    name: str
    passed: bool
    score: Optional[float] = None
    details: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "name": self.name,
            "passed": self.passed,
            "score": self.score,
            "details": self.details,
            "warnings": self.warnings,
            "errors": self.errors,
        }


@dataclass
class ValidationResult:
    """三道验证的完整结果"""

    factor_id: str
    factor_name: str = ""
    experiment_id: str = ""
    timestamp: str = ""
    passed: bool = False
    stage: int = 0  # 在哪一关失败（0=全部通过）
    verdict: str = "Abandon"  # Deploy | Refine | Abandon
    overall_score: float = 0.0
    gates: List[GateResult] = field(default_factory=list)
    hypothesis_id: Optional[str] = None
    hypothesis_bonus: float = 0.0  # 0 或 -0.2（未注册假设）
    mining_rounds: int = 1
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "experiment_id": self.experiment_id,
            "timestamp": self.timestamp,
            "passed": self.passed,
            "stage": self.stage,
            "verdict": self.verdict,
            "overall_score": self.overall_score,
            "gates": [g.to_dict() for g in self.gates],
            "hypothesis_id": self.hypothesis_id,
            "hypothesis_bonus": self.hypothesis_bonus,
            "mining_rounds": self.mining_rounds,
            "warnings": self.warnings,
            "errors": self.errors,
        }

    def summary(self) -> str:
        """单行摘要"""
        gate_icons = "|".join(
            "O" if g.passed else "X" for g in self.gates
        )
        return (
            f"[{self.verdict}] {self.factor_id}: gates=[{gate_icons}] "
            f"score={self.overall_score:.0f} rounds={self.mining_rounds}"
        )


# ─── 验证器 ───


class FactorValidator:
    """三道关因子验证器"""

    def __init__(
        self,
        strict: bool = True,
        oos_test_set_dir: Optional[str] = None,
        log_dir: Optional[str] = None,
    ):
        self.strict = strict
        self._oos_test_set_dir = oos_test_set_dir
        self._log_dir = Path(log_dir) if log_dir else Path("D:/AgentAlpha/signal-factory/validation_logs")
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # 惰性初始化
        self._oos_guard = None
        self._hypothesis_registry = None
        self._logger = None

    # ─── 核心接口 ───

    def validate(
        self,
        factor: Dict[str, Any],
        hypothesis_id: Optional[str] = None,
        strict: Optional[bool] = None,
    ) -> ValidationResult:
        """
        对一个因子运行全部三道验证关。

        参数:
            factor: 因子信息 dict，必须包含:
                - factor_id: str
                - factor_name: str (可选)
                - backtest_result: dict (Gate 1 需要)
                - ic_series: List[float] (可选，Gate 1 增强)
                - factor_code: str (可选，Gate 2 PIT 检测)
                - param_count: int (可选，复杂度罚分)
            hypothesis_id: 假设 ID（Gate 3 需要）
            strict: 是否严格模式（覆盖构造时的设置）

        返回:
            ValidationResult
        """
        if strict is None:
            strict = self.strict

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("factor_name", factor_id)

        result = ValidationResult(
            factor_id=factor_id,
            factor_name=factor_name,
            experiment_id="",
            timestamp=datetime.now().isoformat(),
            hypothesis_id=hypothesis_id,
        )

        t0 = time.time()

        try:
            # ── Gate 1: 统计纪律 ──
            g1 = self._run_gate_1(factor, hypothesis_id)
            result.gates.append(g1)
            if not g1.passed:
                result.stage = 1
                result.overall_score = self._compute_overall_score(result)
                result.verdict = "Abandon" if g1.score is not None and g1.score < 30 else "Refine"
                result.passed = False
                result.warnings.extend(g1.warnings)
                result.errors.extend(g1.errors)
                self._log_result(result, t0)
                return result

            # ── Gate 2: 样本外纪律 ──
            g2 = self._run_gate_2(factor)
            result.gates.append(g2)
            if not g2.passed and strict:
                result.stage = 2
                result.verdict = "Abandon"
                result.passed = False
                result.warnings.extend(g2.warnings)
                result.errors.extend(g2.errors)
                self._log_result(result, t0)
                return result
            elif not g2.passed:
                result.warnings.append("Gate 2 未通过，非严格模式下降级为 Refine")
                result.verdict = "Refine"

            # ── Gate 3: 经济逻辑 ──
            g3 = self._run_gate_3(factor, hypothesis_id)
            result.gates.append(g3)
            if not g3.passed and strict:
                result.stage = 3
                result.verdict = "Refine"  # Gate 3 不 Abandon，只降级
                result.warnings.extend(g3.warnings)
            elif not g3.passed:
                result.warnings.extend(g3.warnings)

            # ── 汇总 ──
            result.overall_score = self._compute_overall_score(result)
            result.passed = all(g.passed for g in result.gates)
            result.verdict = self._determine_verdict(result)

            # 假设未注册惩罚
            if hypothesis_id is None:
                result.hypothesis_bonus = -0.2
                result.warnings.append("假设未注册，置信度降低 20%")

            # 挖掘轮数
            result.mining_rounds = self._get_logger().get_mining_rounds(factor_id) + 1

        except Exception as e:
            result.errors.append(f"验证异常: {e}")
            result.verdict = "Abandon"
            result.passed = False

        self._log_result(result, t0)
        return result

    def validate_batch(
        self,
        factors: List[Dict[str, Any]],
        hypothesis_ids: Optional[Dict[str, str]] = None,
        strict: Optional[bool] = None,
    ) -> List[ValidationResult]:
        """
        批量验证多个因子。

        参数:
            factors: 因子列表
            hypothesis_ids: {factor_id: hypothesis_id} 映射
            strict: 严格模式

        返回:
            ValidationResult 列表
        """
        results = []
        hids = hypothesis_ids or {}

        for factor in factors:
            fid = factor.get("factor_id", "unknown")
            hid = hids.get(fid)
            result = self.validate(factor, hypothesis_id=hid, strict=strict)
            results.append(result)

        return results

    # ─── Gate 1: 统计纪律 ───

    def _run_gate_1(self, factor: Dict[str, Any], hypothesis_id: Optional[str]) -> GateResult:
        """运行 Gate 1: 统计纪律"""
        from src.backtest.scorer import score_gate1

        backtest_result = factor.get("backtest_result", {})
        if not backtest_result:
            return GateResult(
                gate=1, name="统计纪律",
                passed=False, score=0,
                errors=["无回测结果，无法评分"],
            )

        # 获取挖掘轮数
        mining_rounds = self._get_logger().get_mining_rounds(factor.get("factor_id", "")) + 1

        # 调用 scorer
        scorecard = score_gate1(
            backtest_result=backtest_result,
            param_count=factor.get("param_count", 0),
            multiple_comparison_count=factor.get("multiple_comparison_count", 1),
            pit_safe=factor.get("pit_safe", True),
            ic_series=factor.get("ic_series"),
            mining_rounds=mining_rounds,
        )

        verdict = scorecard.get("verdict", "Abandon")
        score = scorecard.get("adjusted_score", 0)

        passed = verdict == "Deploy"
        warnings = []
        if verdict == "Refine":
            warnings.append(f"Gate 1 评分 {score:.0f}，建议优化")
        if verdict == "Abandon":
            warnings.append(f"Gate 1 评分 {score:.0f}，建议放弃")

        # IC 衰减警告
        ic_decay = scorecard.get("anti_overfitting", {}).get("layer_4_ic_decay", {})
        if ic_decay.get("is_decaying"):
            warnings.append("IC 衰减检测: 因子预测能力在下降")

        # Bonferroni 警告
        bonf = scorecard.get("anti_overfitting", {}).get("layer_6_multiple_testing", {}).get("bonferroni", {})
        if bonf.get("n_comparisons", 1) > 5:
            warnings.append(f"多重比较 {bonf.get('n_comparisons')} 次，Bonferroni 修正后 α={bonf.get('corrected_alpha')}")

        return GateResult(
            gate=1, name="统计纪律",
            passed=passed,
            score=score,
            details={
                "scorecard": scorecard,
                "mining_rounds": mining_rounds,
            },
            warnings=warnings,
        )

    # ─── Gate 2: 样本外纪律 ───

    def _run_gate_2(self, factor: Dict[str, Any]) -> GateResult:
        """运行 Gate 2: 样本外纪律"""
        factor_id = factor.get("factor_id", "unknown")
        factor_code = factor.get("factor_code", "")
        warnings = []
        errors = []

        # 2a. OOS 测试集访问控制
        guard = self._get_oos_guard()
        access_result = guard.check_access(factor_id)

        oos_passed = access_result.allowed
        if not access_result.allowed:
            warnings.append(f"OOS 测试集已被污染: {access_result.reason}")

        # 2b. PIT 泄漏检测（如果有代码）
        pit_passed = True
        if factor_code:
            try:
                from src.data.leakage_checker import check_factor_code
                pit_check = check_factor_code(factor_code, factor_id)
                pit_passed = pit_check.get("passed", True)
                if not pit_passed:
                    warnings.append(f"PIT 泄漏: {pit_check.get('summary', '')}")
                    for v in pit_check.get("combined_violations", [])[:3]:
                        warnings.append(f"  行 {v.get('line')}: {v.get('pattern_name')}")
            except Exception as e:
                warnings.append(f"PIT 检测异常: {e}")

        # Gate 2 通过条件: OOS 未污染 + PIT 无泄漏
        passed = oos_passed and pit_passed

        return GateResult(
            gate=2, name="样本外纪律",
            passed=passed,
            details={
                "oos": access_result.to_dict(),
                "pit_passed": pit_passed,
            },
            warnings=warnings,
            errors=errors,
        )

    # ─── Gate 3: 经济逻辑 ───

    def _run_gate_3(self, factor: Dict[str, Any], hypothesis_id: Optional[str]) -> GateResult:
        """运行 Gate 3: 经济逻辑"""
        warnings = []

        if hypothesis_id is None:
            # 无假设 → 降级但不拒绝
            return GateResult(
                gate=3, name="经济逻辑",
                passed=False,
                details={
                    "hypothesis_id": None,
                    "status": "unregistered",
                    "penalty": 0.2,
                },
                warnings=["假设未注册，置信度降低 20%"],
            )

        # 验证假设是否存在
        registry = self._get_hypothesis_registry()
        hypothesis = registry.get_hypothesis(hypothesis_id)

        if hypothesis is None:
            return GateResult(
                gate=3, name="经济逻辑",
                passed=False,
                details={
                    "hypothesis_id": hypothesis_id,
                    "status": "not_found",
                    "penalty": 0.2,
                },
                warnings=[f"假设 {hypothesis_id} 不存在"],
            )

        # 检查是否已绑定结果
        if hypothesis.bound_result:
            warnings.append(
                f"假设 {hypothesis_id} 已被绑定到结果 "
                f"({hypothesis.bound_at})，重复使用？"
            )

        return GateResult(
            gate=3, name="经济逻辑",
            passed=True,
            details={
                "hypothesis_id": hypothesis_id,
                "status": "registered",
                "text": hypothesis.text[:200],
                "category": hypothesis.category,
                "registered_at": hypothesis.registered_at,
            },
            warnings=warnings,
        )

    # ─── 评分与判決 ───

    def _compute_overall_score(self, result: ValidationResult) -> float:
        """计算综合得分（Gate 1 权重 70%, Gate 2 权重 20%, Gate 3 权重 10%）"""
        scores = []
        weights = []

        for g in result.gates:
            if g.gate == 1 and g.score is not None:
                scores.append(g.score)
                weights.append(0.7)
            elif g.gate == 2:
                scores.append(100.0 if g.passed else 0.0)
                weights.append(0.2)
            elif g.gate == 3:
                scores.append(100.0 if g.passed else 50.0)
                weights.append(0.1)

        if not weights:
            return 0.0

        total = sum(s * w for s, w in zip(scores, weights))
        total_weight = sum(weights)
        return round(total / total_weight, 1) if total_weight > 0 else 0.0

    def _determine_verdict(self, result: ValidationResult) -> str:
        """根据各关结果确定最终判決"""
        if result.stage > 0:
            return "Abandon" if result.stage == 1 else "Refine"

        if result.overall_score >= 70:
            return "Deploy"
        elif result.overall_score >= 40:
            return "Refine"
        else:
            return "Abandon"

    # ─── 日志 ───

    def _log_result(self, result: ValidationResult, t0: float):
        """自动记录验证结果"""
        try:
            elapsed = round(time.time() - t0, 1)
            logger = self._get_logger()
            result.experiment_id = logger.log_validation(
                factor_id=result.factor_id,
                factor_name=result.factor_name,
                hypothesis_id=result.hypothesis_id,
                gates={f"gate{i+1}": g.to_dict() for i, g in enumerate(result.gates)},
                passed=result.passed,
                stage=result.stage,
                extra={
                    "verdict": result.verdict,
                    "overall_score": result.overall_score,
                    "hypothesis_bonus": result.hypothesis_bonus,
                    "elapsed": elapsed,
                },
            )
        except Exception:
            pass  # 日志失败不影响验证

    # ─── 惰性初始化 ───

    def _get_oos_guard(self):
        if self._oos_guard is None:
            from src.data.oos_guard import OOSGuard
            test_set = Path(self._oos_test_set_dir) if self._oos_test_set_dir else None
            self._oos_guard = OOSGuard(test_set_path=test_set, log_dir=self._log_dir)
        return self._oos_guard

    def _get_hypothesis_registry(self):
        if self._hypothesis_registry is None:
            from src.factors.hypothesis_registry import HypothesisRegistry
            self._hypothesis_registry = HypothesisRegistry(registry_dir=self._log_dir)
        return self._hypothesis_registry

    def _get_logger(self):
        if self._logger is None:
            from src.factors.validation_logger import ValidationLogger
            self._logger = ValidationLogger(log_dir=self._log_dir)
        return self._logger


# ─── 便捷函数 ───

_validator_instance: Optional[FactorValidator] = None


def get_validator(strict: bool = True) -> FactorValidator:
    """获取默认验证器实例"""
    global _validator_instance
    if _validator_instance is None:
        _validator_instance = FactorValidator(strict=strict)
    return _validator_instance


def validate_factor(
    factor: Dict[str, Any],
    hypothesis_id: Optional[str] = None,
    strict: bool = True,
) -> ValidationResult:
    """便捷函数：验证单个因子"""
    validator = get_validator(strict=strict)
    return validator.validate(factor, hypothesis_id=hypothesis_id)


# ─── CLI ───


def main():
    """CLI 入口"""
    import argparse
    parser = argparse.ArgumentParser(description="因子验证器 — 三道关")
    parser.add_argument("--factor-id", help="因子 ID")
    parser.add_argument("--factor-file", help="因子 JSON 文件路径")
    parser.add_argument("--hypothesis-id", help="假设 ID")
    parser.add_argument("--batch", action="store_true", help="批量模式")
    parser.add_argument("--factor-source", default="paper", help="因子来源 (paper/rd_agent)")
    parser.add_argument("--strict/--no-strict", default=True, help="严格模式")
    parser.add_argument("--output", help="输出 JSON 文件路径")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="验证日期")
    args = parser.parse_args()

    validator = FactorValidator(strict=args.strict)

    if args.batch:
        # 批量模式
        print(f"批量验证模式: source={args.factor_source}, date={args.date}")
        # TODO: 从 factor-lab 或 FactorStore 加载因子列表
        print("批量模式: 请通过 Python API 调用 validate_batch()")
        return

    # 单个因子模式
    if args.factor_file:
        with open(args.factor_file, "r", encoding="utf-8") as f:
            factor = json.load(f)
    elif args.factor_id:
        factor = {"factor_id": args.factor_id}
    else:
        parser.print_help()
        return

    result = validator.validate(factor, hypothesis_id=args.hypothesis_id)

    print(f"\n{'='*60}")
    print(f"因子验证结果: {result.factor_id}")
    print(f"{'='*60}")
    for g in result.gates:
        icon = "✅" if g.passed else "❌"
        print(f"  {icon} Gate {g.gate} ({g.name}): {'通过' if g.passed else '未通过'}")
        for w in g.warnings:
            print(f"     ⚠ {w}")
    print(f"\n  综合得分: {result.overall_score:.0f}")
    print(f"  判決: {result.verdict}")
    print(f"  挖掘轮数: {result.mining_rounds}")
    if result.warnings:
        print(f"  警告: {len(result.warnings)} 条")

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存: {args.output}")


if __name__ == "__main__":
    main()