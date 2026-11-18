#!/usr/bin/env python3
"""
Step D: 评估双通道 76 个活跃因子在 2020-2024 全周期的稳定性。

用法:
  cd D:/AgentAlpha/my-bot/scripts/wsl2
  PYTHONIOENCODING=utf-8 python -u scripts/evaluate_stability.py
"""

import sys, os, json
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).resolve().parent
FACTOR_LAB_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(FACTOR_LAB_DIR))

from ffo.evaluator import FactorEvaluator

REGISTRY_FILE = FACTOR_LAB_DIR / "data" / "factor_registry_p0.json"
OUTPUT_FILE = FACTOR_LAB_DIR / "data" / "factor_stability_2020_2024.json"
REPORT_FILE = Path("D:/AgentAlpha/reports/planning/v5.0-factor-stability-2020-2024.md")

# 评估周期
SHORT_START = "2020-01-01"
SHORT_END = "2020-06-30"
FULL_START = "2020-01-01"
FULL_END = "2024-12-31"
UNIVERSE = "csi500"


def main():
    # 加载活跃因子
    with open(REGISTRY_FILE) as f:
        reg = json.load(f)

    factors = reg.get("factors", {})
    # 只取 IC >= 0.02 的活跃因子
    active = {
        k: v for k, v in factors.items()
        if abs(v.get("ic_mean", 0)) >= 0.02
    }
    print(f"Registry: {len(factors)} total, {len(active)} active (|IC| >= 0.02)")

    # 按通道分
    by_channel = {}
    for fid, meta in active.items():
        ch = meta.get("source_channel", "unknown")
        by_channel.setdefault(ch, []).append(fid)

    for ch, fids in by_channel.items():
        print(f"  {ch}: {len(fids)} factors")

    # 评估
    print(f"\nEvaluating {len(active)} factors on {FULL_START} -> {FULL_END}...")
    evaluator = FactorEvaluator()

    results = []
    for i, (fid, meta) in enumerate(sorted(active.items())):
        formula = meta["formula"]
        channel = meta.get("source_channel", "unknown")
        short_ic = meta.get("ic_mean", 0)

        full_ic = 0.0
        full_icir = 0.0
        full_rank_ic = 0.0
        error = None

        try:
            result = evaluator.evaluate(
                formula,
                start=FULL_START,
                end=FULL_END,
                universe=UNIVERSE,
            )
            if result.error:
                error = result.error
            else:
                full_ic = round(float(result.ic_mean), 6)
                full_icir = round(float(result.ic_ir), 4)
                full_rank_ic = round(float(result.rank_ic_mean), 6)
        except Exception as e:
            error = str(e)[:200]

        ic_decay = round(abs(short_ic) - abs(full_ic), 6) if not error else None
        survived = not error and abs(full_ic) >= 0.02

        results.append({
            "formula": formula,
            "normalized": fid,
            "channel": channel,
            "short_ic": short_ic,
            "full_ic": full_ic,
            "full_rank_ic": full_rank_ic,
            "full_icir": full_icir,
            "ic_decay": ic_decay,
            "survived": survived,
            "error": error,
            "operators": meta.get("operators_used", []),
            "complexity": meta.get("complexity", 0),
        })

        if (i + 1) % 15 == 0:
            print(f"  {i + 1}/{len(active)}... (survived: {sum(1 for r in results if r['survived'])})")

    # 统计
    survived = [r for r in results if r["survived"]]
    failed = [r for r in results if not r["survived"]]
    errors = [r for r in results if r["error"]]

    print(f"\n{'='*60}")
    print(f"Stability Report: {FULL_START} -> {FULL_END}")
    print(f"{'='*60}")
    print(f"  Total evaluated: {len(results)}")
    print(f"  Survived (|IC| >= 0.02): {len(survived)} ({len(survived)/len(results)*100:.0f}%)")
    print(f"  Failed (|IC| < 0.02): {len(failed) - len(errors)}")
    print(f"  Errors: {len(errors)}")

    # 按通道统计
    for ch in sorted(by_channel.keys()):
        ch_results = [r for r in results if r["channel"] == ch]
        ch_survived = [r for r in ch_results if r["survived"]]
        avg_decay = sum(r["ic_decay"] for r in ch_results if r["ic_decay"] is not None) / max(len([r for r in ch_results if r["ic_decay"] is not None]), 1)
        print(f"  {ch}: {len(ch_survived)}/{len(ch_results)} survived, avg IC decay={avg_decay:.4f}")

    # Top 10 稳定因子
    survived.sort(key=lambda r: -abs(r["full_ic"]))
    print(f"\nTop 10 Stable Factors:")
    for r in survived[:10]:
        print(f"  [{r['channel']}] {r['formula']}")
        print(f"    short_IC={r['short_ic']:.4f}  full_IC={r['full_ic']:.4f}  decay={r['ic_decay']:.4f}  ICIR={r['full_icir']}")

    # 保存 JSON
    output = {
        "evaluated_at": datetime.now().isoformat(),
        "eval_period": f"{FULL_START} -> {FULL_END}",
        "universe": UNIVERSE,
        "total": len(results),
        "survived": len(survived),
        "survival_rate": round(len(survived) / len(results), 4),
        "by_channel": {
            ch: {
                "total": len([r for r in results if r["channel"] == ch]),
                "survived": len([r for r in results if r["channel"] == ch and r["survived"]]),
            }
            for ch in sorted(by_channel.keys())
        },
        "results": results,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nJSON saved to: {OUTPUT_FILE}")

    # 生成 Markdown 报告
    generate_report(output, survived, results, by_channel)


def generate_report(output, survived, results, by_channel):
    lines = []
    lines.append("# v5.0 因子稳定性评估: 2020-2024")
    lines.append("")
    lines.append(f"> 日期: {datetime.now().strftime('%Y-%m-%d')} | 评估周期: {FULL_START} → {FULL_END} | 股票池: {UNIVERSE}")
    lines.append(">")
    lines.append(f"> 数据源: GFlowNet v2 (200ep, IC=0.4/div=0.5) + LLM Agent (30 rounds, DeepSeek)")
    lines.append("")
    lines.append("## 总览")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|-----|")
    lines.append(f"| 评估因子总数 | {output['total']} |")
    lines.append(f"| 存活因子 (\\|IC\\| ≥ 0.02) | {output['survived']} ({output['survival_rate']*100:.0f}%) |")
    lines.append(f"| 消亡因子 | {output['total'] - output['survived']} |")
    lines.append("")
    lines.append("## 按通道统计")
    lines.append("")
    lines.append(f"| 通道 | 总数 | 存活 | 存活率 |")
    lines.append(f"|------|------|------|--------|")
    for ch in sorted(by_channel.keys()):
        info = output["by_channel"][ch]
        rate = info["survived"] / info["total"] * 100 if info["total"] else 0
        lines.append(f"| {ch} | {info['total']} | {info['survived']} | {rate:.0f}% |")
    lines.append("")

    # Top 15 稳定因子
    lines.append("## Top 15 稳定因子 (按 full_IC 排序)")
    lines.append("")
    lines.append(f"| # | 通道 | 公式 | Short IC | Full IC | Decay | ICIR |")
    lines.append(f"|---|------|------|----------|---------|-------|------|")
    for i, r in enumerate(survived[:15], 1):
        formula_short = r["formula"][:60] + ("..." if len(r["formula"]) > 60 else "")
        lines.append(f"| {i} | {r['channel']} | `{formula_short}` | {r['short_ic']:.4f} | {r['full_ic']:.4f} | {r['ic_decay']:.4f} | {r['full_icir']} |")
    lines.append("")

    # IC decay 分布
    decays = [r["ic_decay"] for r in results if r["ic_decay"] is not None]
    if decays:
        import statistics
        lines.append("## IC Decay 分析")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| 平均 decay | {statistics.mean(decays):.4f} |")
        lines.append(f"| 中位 decay | {statistics.median(decays):.4f} |")
        lines.append(f"| 最大 decay | {max(decays):.4f} |")
        lines.append(f"| 因子 IC 提升 (decay < 0) | {sum(1 for d in decays if d < 0)} ({sum(1 for d in decays if d < 0)/len(decays)*100:.0f}%) |")
        lines.append("")
        lines.append("> 负 decay 表示因子在更长周期表现更好——这是最强稳定性信号。")
        lines.append("")

    # 建议
    lines.append("## 建议")
    lines.append("")
    # 找出 decay < 0 的因子（更长周期反而更好）
    improved = [r for r in survived if r.get("ic_decay") is not None and r["ic_decay"] < 0]
    lines.append(f"1. **{len(improved)} 个因子在全周期 IC 反而提升**（decay < 0）—— 这些是最稳健的候选，优先集成到 daily signal 管线")
    lines.append(f"2. **存活率 {output['survival_rate']*100:.0f}%** —— 表明双通道因子工厂产出的因子具有一定稳定性")
    lines.append(f"3. 建议将 decay < 0 且 full_IC > 0.03 的因子注册到 factor_compute 管线，替换部分手工因子")

    report = "\n".join(lines)
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Report saved to: {REPORT_FILE}")


if __name__ == "__main__":
    main()