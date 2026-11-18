"""
FFO 报告格式化 — 将 EvaluationResult 格式化为可读输出

输出格式:
  - Markdown 表格 (format_report)
  - JSON (format_report_json)
  - 简洁单行 (format_report_short)
"""

from .evaluator import EvaluationResult


def format_report(result: EvaluationResult) -> str:
    """格式化为 Markdown 报告"""
    if result.error:
        return f"## FFO 评估失败\n\n**公式**: `{result.formula}`\n**错误**: {result.error}"

    lines = []
    lines.append(f"## FFO 因子评估报告")
    lines.append(f"")
    lines.append(f"| 项目 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 公式 | `{result.formula}` |")
    lines.append(f"| 股票池 | {result.universe} |")
    lines.append(f"| 评估区间 | {result.start_date} → {result.end_date} |")
    lines.append(f"| 交易日数 | {result.n_days} |")
    lines.append(f"| 股票数 | {result.n_stocks} |")
    lines.append(f"")

    # IC
    lines.append(f"### IC 指标")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| IC Mean | {result.ic_mean:.6f} |")
    lines.append(f"| IC Std | {result.ic_std:.6f} |")
    lines.append(f"| **ICIR** | **{result.ic_ir:.4f}** |")
    lines.append(f"| IC > 0 占比 | {result.ic_positive_rate:.2%} |")
    lines.append(f"| IC 显著占比 | {result.ic_significant_rate:.2%} |")
    lines.append(f"")

    # Rank IC
    lines.append(f"### Rank IC 指标")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| Rank IC Mean | {result.rank_ic_mean:.6f} |")
    lines.append(f"| Rank IC Std | {result.rank_ic_std:.6f} |")
    lines.append(f"| **Rank ICIR** | **{result.rank_ic_ir:.4f}** |")
    lines.append(f"| Rank IC > 0 占比 | {result.rank_ic_positive_rate:.2%} |")
    lines.append(f"")

    # 分组收益
    lines.append(f"### 分组收益 (Top vs Bottom 20%)")
    lines.append(f"")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| Long (Top 20%) 年化 | {result.long_annual_return:.2%} |")
    lines.append(f"| Short (Bottom 20%) 年化 | {result.short_annual_return:.2%} |")
    lines.append(f"| **多空价差** | **{result.long_short_spread:.2%}** |")
    lines.append(f"| 日均换手率 | {result.avg_turnover:.2%} |")
    lines.append(f"")

    # 综合判断
    lines.append(f"### 综合判断")
    lines.append(f"")
    verdict, color = _get_verdict(result)
    lines.append(f"**{color} {verdict}**")
    lines.append(f"")

    reasons = _get_reasons(result)
    for r in reasons:
        lines.append(f"- {r}")

    if result.warnings:
        lines.append(f"")
        lines.append(f"### 警告")
        for w in result.warnings:
            lines.append(f"- ⚠️ {w}")

    return "\n".join(lines)


def format_report_json(result: EvaluationResult) -> dict:
    """格式化为 JSON"""
    return result.to_dict()


def format_report_short(result: EvaluationResult) -> str:
    """简洁单行输出"""
    if result.error:
        return f"[ERR] {result.formula}: {result.error}"

    verdict, _ = _get_verdict(result)
    return (
        f"[{verdict[:4]}] {result.formula[:40]:<40s} "
        f"IC={result.ic_mean:+.4f} "
        f"RIC={result.rank_ic_mean:+.4f} "
        f"ICIR={result.ic_ir:.2f} "
        f"LS={result.long_short_spread:+.2%} "
        f"TO={result.avg_turnover:.2%}"
    )


def _get_verdict(result: EvaluationResult) -> tuple:
    """综合判断"""
    abs_ic = abs(result.ic_mean)
    abs_ric = abs(result.rank_ic_mean)

    if abs_ic >= 0.05 and abs_ric >= 0.05 and result.ic_ir >= 0.5:
        return ("✓ 强烈推荐", "🟢")
    elif abs_ic >= 0.03 and result.ic_ir >= 0.3:
        return ("○ 推荐", "🟡")
    elif abs_ic >= 0.02 and result.ic_positive_rate >= 0.52:
        return ("△ 可用", "🟠")
    elif abs_ic >= 0.01:
        return ("× 弱信号", "🔴")
    else:
        return ("✗ 无效", "⚫")


def _get_reasons(result: EvaluationResult) -> list:
    """生成判断理由"""
    reasons = []

    abs_ic = abs(result.ic_mean)
    if abs_ic >= 0.05:
        reasons.append(f"IC 均值 ({result.ic_mean:.4f}) 优秀 (>0.05)")
    elif abs_ic >= 0.03:
        reasons.append(f"IC 均值 ({result.ic_mean:.4f}) 良好 (>0.03)")
    elif abs_ic >= 0.02:
        reasons.append(f"IC 均值 ({result.ic_mean:.4f}) 勉强可用 (>0.02)")
    else:
        reasons.append(f"IC 均值 ({result.ic_mean:.4f}) 不足 (<0.02)")

    if result.ic_ir >= 0.5:
        reasons.append(f"ICIR ({result.ic_ir:.3f}) 优秀 (>0.5)")
    elif result.ic_ir >= 0.3:
        reasons.append(f"ICIR ({result.ic_ir:.3f}) 可接受 (>0.3)")
    else:
        reasons.append(f"ICIR ({result.ic_ir:.3f}) 偏低 (<0.3)")

    if result.ic_positive_rate >= 0.55:
        reasons.append(f"IC 正向率 ({result.ic_positive_rate:.1%}) 稳定 (>55%)")
    elif result.ic_positive_rate >= 0.52:
        reasons.append(f"IC 正向率 ({result.ic_positive_rate:.1%}) 略高于随机")

    if abs(result.rank_ic_mean) >= 0.03:
        reasons.append(f"Rank IC ({result.rank_ic_mean:.4f}) 证实了 IC 的稳健性")

    if result.long_short_spread > 0.05:
        reasons.append(f"多空价差 ({result.long_short_spread:.2%}) 显著 (>5%)")
    elif result.long_short_spread > 0:
        reasons.append(f"多空价差 ({result.long_short_spread:.2%}) 方向正确")
    else:
        reasons.append(f"多空价差为负 —— 因子方向可能需要反转")

    if result.avg_turnover > 0.5:
        reasons.append(f"换手率 ({result.avg_turnover:.2%}) 偏高 (>50%) —— 可能有高交易成本")
    elif result.avg_turnover < 0.1:
        reasons.append(f"换手率 ({result.avg_turnover:.2%}) 低 —— 低交易成本")

    return reasons