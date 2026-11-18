"""
v4.3 vs v5.0 Alpha Signal Comparison — quantify P1 factor consensus incremental value.

v4.3 (baseline): ML ensemble signals (CatBoost + XGBoost + GBDT)
v5.0 (enhanced): ML ensemble + P1 factor consensus (37-factor IC-weighted Z-score)

Both signal sources are tracked in separate tracker files by signal_tracker.py.
This script reads both and produces a side-by-side comparison report.

Key metrics:
  - Hit rate (Win rate): % of signals with positive 5-day return
  - Average return: mean 5-day return
  - Excess vs CSI300: average alpha over benchmark
  - Sharpe: annualized from 5-day returns
  - Orthogonality: % overlap between ML and P1 signal picks
  - Signal coverage: unique stocks per source

Usage:
    python signal_comparison.py               # generate comparison report
    python signal_comparison.py --json        # JSON output only
"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, stdev
from typing import Dict, List, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUTPUT_DIR = Path("D:/AgentAlpha/my-bot/reports/signals")
DATA_OUTPUT_DIR = Path("D:/AgentAlpha/my-bot/data/output")
ML_TRACKER = Path("D:/AgentAlpha/my-bot/data/output/signal_tracker.json")
P1_TRACKER = Path("D:/AgentAlpha/my-bot/data/output/signal_tracker_p1.json")
COMPARISON_REPORT = OUTPUT_DIR / "v4.3-vs-v5.0-comparison.md"
COMPARISON_JSON = OUTPUT_DIR / "v4.3-vs-v5.0-comparison.json"
COMPARISON_JSON_PIPELINE = DATA_OUTPUT_DIR / "v4.3-vs-v5.0-comparison.json"

METRICS_LABELS = {
    "total_closed": "已到期信号数",
    "avg_return_pct": "平均 5 日收益",
    "win_rate_pct": "胜率 (%)",
    "sharpe_annual": "年化 Sharpe",
    "max_gain_pct": "最大单笔收益",
    "max_loss_pct": "最大单笔亏损",
    "avg_excess_csi300": "超额收益 vs CSI300",
    "excess_win_csi300": "超额胜率 vs CSI300 (%)",
}


def load_tracker(path: Path) -> dict:
    """Load a signal tracker JSON file."""
    if not path.exists():
        return {"records": [], "stats": {}, "updated": None}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_metrics(records: list) -> dict:
    """Compute performance metrics from a list of closed signal records."""
    closed = [r for r in records if r.get("return_5d") is not None]
    if not closed:
        return {
            "total_closed": 0, "avg_return_pct": 0, "win_rate_pct": 0,
            "max_gain_pct": 0, "max_loss_pct": 0, "sharpe_annual": 0,
            "total_tracked": len(records),
            "avg_excess_csi300": None, "excess_win_csi300": None,
            "avg_excess_csi500": None, "excess_win_csi500": None,
        }

    returns = [r["return_5d"] for r in closed]
    wins = [r for r in returns if r > 0]
    avg = mean(returns)
    std = stdev(returns) if len(returns) > 1 else 1.0
    sharpe = (avg / std) * (252 ** 0.5) if std > 0 else 0

    excess_csi300 = [r.get("excess_csi300") for r in closed if r.get("excess_csi300") is not None]
    excess_csi500 = [r.get("excess_csi500") for r in closed if r.get("excess_csi500") is not None]

    stats = {
        "total_closed": len(closed),
        "avg_return_pct": round(avg, 2),
        "win_rate_pct": round(len(wins) / len(closed) * 100, 1),
        "max_gain_pct": round(max(returns), 2),
        "max_loss_pct": round(min(returns), 2),
        "sharpe_annual": round(sharpe, 3),
        "total_tracked": len(records),
    }

    if excess_csi300:
        stats["avg_excess_csi300"] = round(mean(excess_csi300), 2)
        stats["excess_win_csi300"] = round(len([x for x in excess_csi300 if x > 0]) / len(excess_csi300) * 100, 1)

    if excess_csi500:
        stats["avg_excess_csi500"] = round(mean(excess_csi500), 2)
        stats["excess_win_csi500"] = round(len([x for x in excess_csi500 if x > 0]) / len(excess_csi500) * 100, 1)

    return stats


def monthly_breakdown(records: list) -> dict:
    """Compute monthly performance breakdown."""
    closed = [r for r in records if r.get("return_5d") is not None]
    monthly = defaultdict(list)
    for r in closed:
        m = r["signal_date"][:7]
        monthly[m].append(r["return_5d"])

    result = {}
    for m in sorted(monthly.keys()):
        rets = monthly[m]
        result[m] = {
            "n": len(rets),
            "avg_return": round(mean(rets), 2),
            "win_rate": round(len([r for r in rets if r > 0]) / len(rets) * 100, 1),
            "best": round(max(rets), 2),
            "worst": round(min(rets), 2),
        }
    return result


def compute_orthogonality(ml_records: list, p1_records: list) -> dict:
    """Compute overlap/orthogonality between ML and P1 signal picks."""
    ml_active = {r["code"] for r in ml_records if r.get("status") == "active"}
    p1_active = {r["code"] for r in p1_records if r.get("status") == "active"}

    if not ml_active or not p1_active:
        return {"ml_count": len(ml_active), "p1_count": len(p1_active),
                "overlap": 0, "overlap_pct": 0, "ml_only": len(ml_active),
                "p1_only": len(p1_active), "note": "insufficient data for comparison"}

    overlap = ml_active & p1_active
    ml_only = ml_active - p1_active
    p1_only = p1_active - ml_active
    union = ml_active | p1_active

    return {
        "ml_count": len(ml_active),
        "p1_count": len(p1_active),
        "overlap": len(overlap),
        "overlap_pct": round(len(overlap) / len(union) * 100, 1) if union else 0,
        "ml_only": len(ml_only),
        "p1_only": len(p1_only),
        "overlap_symbols": sorted(overlap),
        "ml_only_top5": sorted(ml_only)[:5],
        "p1_only_top5": sorted(p1_only)[:5],
    }


def generate_report(ml_stats: dict, p1_stats: dict, orthogonality: dict,
                    ml_monthly: dict, p1_monthly: dict) -> str:
    """Generate markdown comparison report."""
    p1_has_data = p1_stats.get("total_closed", 0) > 0

    lines = [
        "# v4.3 vs v5.0 Alpha Signal Comparison",
        "",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')} | Updated daily via signal_tracker.py",
        "",
        "## Overview",
        "",
        "| | v4.3 (ML Ensemble) | v5.0 P1 (Factor Consensus) |",
        "|---|---|---|",
        f"| Model | CatBoost + XGBoost + GBDT | 37-factor IC-weighted Z-score |",
        f"| Universe | CSI 500 | CSI 500 |",
        f"| Hold period | ~5 trading days | ~5 trading days |",
        f"| Total tracked | {ml_stats.get('total_tracked', 0)} | {p1_stats.get('total_tracked', 0)} |",
        "",
        "## Performance Comparison",
        "",
        "| Metric | v4.3 ML | v5.0 P1 | Δ (P1 - ML) |",
        "|---|---|---|---|",
    ]

    # Key metrics comparison
    for key in ["total_closed", "avg_return_pct", "win_rate_pct", "sharpe_annual",
                "max_gain_pct", "max_loss_pct"]:
        ml_val = ml_stats.get(key, 0)
        p1_val = p1_stats.get(key, 0) if p1_has_data else "—"
        delta = ""
        if p1_has_data and isinstance(p1_val, (int, float)) and isinstance(ml_val, (int, float)):
            d = p1_val - ml_val
            if key in ("avg_return_pct", "win_rate_pct", "sharpe_annual", "max_gain_pct"):
                direction = "🟢" if d > 0 else "🔴" if d < 0 else "➖"
            elif key == "max_loss_pct":
                direction = "🟢" if d > 0 else "🔴" if d < 0 else "➖"  # less negative = better
            else:
                direction = ""
            delta = f"{direction} {d:+.2f}"
        fmt = lambda v: f"{v:+.2f}%" if isinstance(v, float) and "pct" in key else f"{v}" if isinstance(v, (int, float)) else str(v)
        label = METRICS_LABELS.get(key, key)
        lines.append(f"| {label} | {fmt(ml_val)} | {fmt(p1_val)} | {delta} |")

    # Excess returns
    if ml_stats.get("avg_excess_csi300") is not None:
        ml_ex = ml_stats["avg_excess_csi300"]
        p1_ex = p1_stats.get("avg_excess_csi300", "—") if p1_has_data else "—"
        delta = ""
        if p1_has_data and isinstance(p1_ex, (int, float)):
            d = p1_ex - ml_ex
            delta = f"{'🟢' if d > 0 else '🔴'} {d:+.2f}%"
        lines.append(f"| {METRICS_LABELS['avg_excess_csi300']} | {ml_ex:+.2f}% | {p1_ex} | {delta} |")

    lines.append("")

    # Orthogonality
    if orthogonality:
        lines += [
            "## Signal Orthogonality (Active Picks)",
            "",
            f"| | Count |",
            f"|---|---|",
            f"| v4.3 ML active | {orthogonality['ml_count']} |",
            f"| v5.0 P1 active | {orthogonality['p1_count']} |",
            f"| Overlap | {orthogonality['overlap']} ({orthogonality['overlap_pct']}%) |",
            f"| ML only | {orthogonality['ml_only']} |",
            f"| P1 only | {orthogonality['p1_only']} |",
            "",
        ]
        if orthogonality.get("overlap_symbols"):
            lines.append(f"Overlap stocks: {', '.join(orthogonality['overlap_symbols'])}")
            lines.append("")
        if orthogonality.get("note"):
            lines.append(f"> {orthogonality['note']}")
            lines.append("")

    # Monthly breakdown
    if ml_monthly:
        lines += [
            "## v4.3 ML Monthly Performance",
            "",
            "| Month | N | Avg Return | Win Rate | Best | Worst |",
            "|-------|---|-----------|----------|------|-------|",
        ]
        for m, data in sorted(ml_monthly.items()):
            lines.append(f"| {m} | {data['n']} | {data['avg_return']:+.1f}% | {data['win_rate']:.0f}% | {data['best']:+.1f}% | {data['worst']:+.1f}% |")
        lines.append("")

    if p1_monthly and p1_has_data:
        lines += [
            "## v5.0 P1 Monthly Performance",
            "",
            "| Month | N | Avg Return | Win Rate | Best | Worst |",
            "|-------|---|-----------|----------|------|-------|",
        ]
        for m, data in sorted(p1_monthly.items()):
            lines.append(f"| {m} | {data['n']} | {data['avg_return']:+.1f}% | {data['win_rate']:.0f}% | {data['best']:+.1f}% | {data['worst']:+.1f}% |")
        lines.append("")

    # Verdict
    lines += [
        "## Assessment",
        "",
    ]

    if not p1_has_data:
        lines += [
            f"> ⏳ P1 signals started 2026-11-05. Need 5 trading days for first returns.",
            f"> Currently: {p1_stats.get('total_tracked', 0)} tracked, 0 matured.",
            "",
            "### ML Baseline (Historical)",
            f"- {ml_stats.get('total_closed', 0)} closed signals ({ml_stats.get('total_tracked', 0)} tracked)",
            f"- Avg return: {ml_stats.get('avg_return_pct', 0):+.2f}%",
            f"- Win rate: {ml_stats.get('win_rate_pct', 0)}%",
            f"- Sharpe: {ml_stats.get('sharpe_annual', 0)}",
            "",
            "### Expected P1 Contribution",
            "- P1 factors add 37 factors covering momentum, mean-reversion, and VWAP-based signals",
            "- P1 picks favor Shanghai main board (sh6*) vs ML's ChiNext (sz3*) — low overlap expected",
            "- Combined coverage: 500 stocks × 2 orthogonal alpha sources",
        ]
    else:
        # Future: when P1 has data
        ml_win = ml_stats.get("win_rate_pct", 0)
        p1_win = p1_stats.get("win_rate_pct", 0)
        ml_sharpe = ml_stats.get("sharpe_annual", 0)
        p1_sharpe = p1_stats.get("sharpe_annual", 0)

        verdicts = []
        if p1_win > ml_win:
            verdicts.append(f"✅ P1 win rate higher ({p1_win}% vs {ml_win}%)")
        else:
            verdicts.append(f"❌ P1 win rate lower ({p1_win}% vs {ml_win}%)")

        if p1_sharpe > ml_sharpe:
            verdicts.append(f"✅ P1 Sharpe higher ({p1_sharpe} vs {ml_sharpe})")
        else:
            verdicts.append(f"❌ P1 Sharpe lower ({p1_sharpe} vs {ml_sharpe})")

        for v in verdicts:
            lines.append(f"- {v}")

    lines += [
        "",
        "---",
        "",
        "> Auto-generated by signal_comparison.py — run daily to track P1 vs ML convergence",
    ]

    return "\n".join(lines)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="v4.3 vs v5.0 Alpha Signal Comparison")
    parser.add_argument("--json", action="store_true", help="JSON output only, no markdown")
    args = parser.parse_args()

    print("=" * 60)
    print("v4.3 (ML) vs v5.0 (ML+P1) Alpha Comparison")
    print("=" * 60)

    # Load both trackers
    ml_tracker = load_tracker(ML_TRACKER)
    p1_tracker = load_tracker(P1_TRACKER)

    ml_records = ml_tracker.get("records", [])
    p1_records = p1_tracker.get("records", [])

    # Compute metrics
    ml_stats = compute_metrics(ml_records)
    p1_stats = compute_metrics(p1_records)

    print(f"\n  ML:  {ml_stats['total_closed']} closed / {ml_stats['total_tracked']} tracked")
    print(f"  P1:  {p1_stats['total_closed']} closed / {p1_stats['total_tracked']} tracked")

    # Orthogonality
    ortho = compute_orthogonality(ml_records, p1_records)
    if ortho.get("note"):
        print(f"  Orthogonality: {ortho['note']}")
    else:
        print(f"  Overlap: {ortho['overlap']}/{ortho['ml_count'] + ortho['p1_count']} ({ortho['overlap_pct']}%)")

    # Monthly
    ml_monthly = monthly_breakdown(ml_records)
    p1_monthly = monthly_breakdown(p1_records)

    # Generate report
    report = generate_report(ml_stats, p1_stats, ortho, ml_monthly, p1_monthly)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(COMPARISON_REPORT, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n  Report: {COMPARISON_REPORT}")

    # Save JSON summary
    summary = {
        "generated_at": datetime.now().isoformat(),
        "ml": ml_stats,
        "p1": p1_stats,
        "orthogonality": ortho,
        "ml_monthly": ml_monthly,
        "p1_monthly": p1_monthly,
    }
    with open(COMPARISON_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"  JSON:   {COMPARISON_JSON}")

    # Dual save to data/output/ for morning brief pipeline consumption
    DATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(COMPARISON_JSON_PIPELINE, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"  JSON:   {COMPARISON_JSON_PIPELINE} (pipeline copy)")

    if not args.json:
        print(f"\n{'─' * 50}")
        # Quick summary
        if p1_stats["total_closed"] == 0:
            print("P1 signals pending — first returns expected after 2026-11-12 (5 trading days)")
        else:
            print(f"P1: {p1_stats['avg_return_pct']:+.2f}% avg, {p1_stats['win_rate_pct']}% win")
        print(f"ML baseline: {ml_stats['avg_return_pct']:+.2f}% avg, {ml_stats['win_rate_pct']}% win")
        print(f"{'─' * 50}")


if __name__ == "__main__":
    main()