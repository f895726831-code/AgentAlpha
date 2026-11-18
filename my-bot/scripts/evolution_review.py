#!/usr/bin/env python3
"""
周度 Evolution Backlog 审查

在每周复盘时运行，输出 backlog 健康报告和优先级排序。
可与 LLM Agent 配合使用——输出结构化数据供 Agent 决策。

用法:
  python scripts/evolution_review.py                    # 完整审查报告
  python scripts/evolution_review.py --summary          # 仅摘要
  python scripts/evolution_review.py --export-json      # 输出 JSON 供 Agent 消费
  python scripts/evolution_review.py --promote P0       # 列出可晋升为路线图任务的高优先级条目
"""

import argparse
import json
import sys
import io
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path("D:/AgentAlpha/my-bot")
EVOLUTION_DIR = PROJECT_ROOT / "data/evolution"
BACKLOG_FILE = EVOLUTION_DIR / "backlog.json"
WATCHLIST_FILE = EVOLUTION_DIR / "watchlist.json"
REVIEW_DIR = EVOLUTION_DIR / "reviews"


def _load_backlog() -> dict:
    if BACKLOG_FILE.exists():
        return json.loads(BACKLOG_FILE.read_text(encoding="utf-8"))
    return {"version": "1.0", "entries": [], "stats": {}}


def _load_watchlist() -> dict:
    if WATCHLIST_FILE.exists():
        return json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    return {"entries": []}


def _age_days(created: str) -> int:
    return (datetime.now() - datetime.strptime(created, "%Y-%m-%d")).days


def _priority_score(entry: dict) -> float:
    """Calculate priority score for sorting."""
    score = 0.0

    if entry["track"] == "internal":
        severity_weights = {"P0": 100, "P1": 60, "P2": 30, "P3": 10}
        score += severity_weights.get(entry.get("severity", "P3"), 10)
    else:
        score += entry.get("composite_score", 2.5) * 15

    # Age penalty: -2 per week, but only for items older than 2 weeks
    age = _age_days(entry["created"])
    if age > 14:
        score -= (age - 14) // 7 * 2

    # Active status bonus
    active_bonus = {"captured": 10, "diagnosed": 5, "designed": 0, "validating": -5}
    score += active_bonus.get(entry.get("status", "captured"), 0)

    return max(score, 0)


def generate_report() -> str:
    """Generate a weekly review report."""
    backlog = _load_backlog()
    watchlist = _load_watchlist()
    entries = backlog["entries"]
    stats = backlog.get("stats", {})
    today = datetime.now().strftime("%Y-%m-%d")

    # Active entries
    active_statuses = {"captured", "diagnosed", "designed", "validating"}
    active = [e for e in entries if e["status"] in active_statuses]
    integrated = [e for e in entries if e["status"] == "integrated"]
    rejected = [e for e in entries if e["status"] == "rejected"]

    # Sort active by priority
    active_sorted = sorted(active, key=_priority_score, reverse=True)

    # Stale items (>4 weeks in captured/diagnosed)
    stale = [e for e in active if _age_days(e["created"]) > 28 and e["status"] in ("captured", "diagnosed")]

    # Recently integrated (this week)
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    recently_integrated = [e for e in integrated if e.get("resolved") and e["resolved"] >= week_ago]

    lines = [
        f"# Evolution Backlog 周度审查 — {today}",
        "",
        f"## 概览",
        f"",
        f"| 指标 | 数值 |",
        f"|---|---|",
        f"| Backlog 总数 | {stats.get('total', len(entries))} |",
        f"| 活跃条目 | {len(active)} |",
        f"| 本周已集成 | {len(recently_integrated)} |",
        f"| 已集成总计 | {len(integrated)} |",
        f"| 已拒绝 | {len(rejected)} |",
        f"| Watchlist | {len(watchlist.get('entries', []))} |",
        f"| Track A (内部) | {stats.get('by_track', {}).get('internal', '?')} |",
        f"| Track B (外部) | {stats.get('by_track', {}).get('external', '?')} |",
        "",
        f"## ⚠️ 过期条目 (>4周未处理)",
        "",
    ]

    if stale:
        for e in stale:
            age = _age_days(e["created"])
            lines.append(f"- **{e['id']}** [{e.get('severity', 'N/A')}] {e['title']} — {age}天前创建, status={e['status']}")
    else:
        lines.append("✅ 无过期条目")
    lines.append("")

    lines.append("## 🔥 优先级排序 (Top 10)")
    lines.append("")
    lines.append("| # | ID | Track | 严重度/评分 | 优先级分 | Age | Status | Title |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, e in enumerate(active_sorted[:10]):
        sev = e.get("severity", f"{e.get('composite_score', '?')}")
        score = _priority_score(e)
        age = _age_days(e["created"])
        lines.append(f"| {i+1} | {e['id']} | {e['track']} | {sev} | {score:.0f} | {age}d | {e['status']} | {e['title'][:50]} |")
    lines.append("")

    lines.append("## 🟢 本周已集成")
    lines.append("")
    if recently_integrated:
        for e in recently_integrated:
            lines.append(f"- **{e['id']}** {e['title']} (resolved {e.get('resolved', '?')})")
    else:
        lines.append("本周无新集成的条目")
    lines.append("")

    lines.append("## 📋 可晋升为路线图任务 (P0 + captured/diagnosed)")
    lines.append("")
    p0_active = [e for e in active_sorted if e.get("severity") == "P0"]
    if p0_active:
        for e in p0_active:
            lines.append(f"- **{e['id']}** {e['title']}")
            if e.get("proposed_solution"):
                lines.append(f"  → {e['proposed_solution'][:100]}")
    else:
        lines.append("无活跃 P0 条目")
    lines.append("")

    lines.append("## 📡 Watchlist 评估")
    lines.append("")
    for w in watchlist.get("entries", []):
        reeval = w.get("reevaluate_after", "?")
        ready = reeval <= today if reeval != "?" else False
        tag = "🔴 待评估" if ready else "⏳"
        lines.append(f"- {tag} **{w.get('id', '?')}** {w.get('title', '?')} (reeval: {reeval})")
    lines.append("")

    lines.append("---")
    lines.append(f"> 自动生成于 evolution_review.py | 下次审查: {(datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')}")

    return "\n".join(lines)


def export_json() -> dict:
    """Export structured data for LLM Agent consumption."""
    backlog = _load_backlog()
    watchlist = _load_watchlist()
    active_statuses = {"captured", "diagnosed", "designed", "validating"}
    active = [e for e in backlog["entries"] if e["status"] in active_statuses]
    active_sorted = sorted(active, key=_priority_score, reverse=True)

    return {
        "review_date": datetime.now().strftime("%Y-%m-%d"),
        "stats": backlog.get("stats", {}),
        "top_priorities": [
            {
                "id": e["id"],
                "track": e["track"],
                "title": e["title"],
                "severity": e.get("severity"),
                "composite_score": e.get("composite_score"),
                "priority_score": _priority_score(e),
                "age_days": _age_days(e["created"]),
                "status": e["status"],
                "proposed_solution": e.get("proposed_solution", ""),
            }
            for e in active_sorted[:10]
        ],
        "stale_items": [
            {"id": e["id"], "title": e["title"], "age_days": _age_days(e["created"]), "status": e["status"]}
            for e in active if _age_days(e["created"]) > 28 and e["status"] in ("captured", "diagnosed")
        ],
        "watchlist_ready": [
            {"id": w.get("id"), "title": w.get("title"), "reevaluate_after": w.get("reevaluate_after")}
            for w in watchlist.get("entries", [])
            if w.get("reevaluate_after", "9999") <= datetime.now().strftime("%Y-%m-%d")
        ],
    }


def promote_candidates(severity: str = "P0") -> list[dict]:
    """List items ready for promotion to roadmap."""
    backlog = _load_backlog()
    active_statuses = {"captured", "diagnosed"}
    candidates = [
        e for e in backlog["entries"]
        if e.get("severity") == severity and e["status"] in active_statuses
    ]
    return sorted(candidates, key=_priority_score, reverse=True)


def main():
    parser = argparse.ArgumentParser(description="周度 Evolution Backlog 审查")
    parser.add_argument("--summary", action="store_true", help="仅输出摘要")
    parser.add_argument("--export-json", action="store_true", help="输出 JSON 供 Agent 消费")
    parser.add_argument("--promote", choices=["P0", "P1", "P2"], help="列出可晋升为路线图任务的条目")
    parser.add_argument("--output", help="输出文件路径")
    args = parser.parse_args()

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")

    if args.promote:
        candidates = promote_candidates(args.promote)
        if candidates:
            print(f"## 可晋升条目 ({args.promote}):")
            for c in candidates:
                print(f"- {c['id']} [{c['status']}] {c['title']}")
                if c.get("proposed_solution"):
                    print(f"  → {c['proposed_solution']}")
        else:
            print(f"无活跃 {args.promote} 条目")
        return

    if args.export_json:
        data = export_json()
        output = json.dumps(data, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(output, encoding="utf-8")
            print(f"→ {args.output}")
        else:
            print(output)
        return

    # Default: full report
    report = generate_report()
    if args.summary:
        # Extract only the overview and top priorities
        lines = report.split("\n")
        in_section = True
        for line in lines:
            if line.startswith("## ") and "概览" not in line and "优先级排序" not in line:
                break
            print(line)
    else:
        print(report)

    # Save report
    report_file = REVIEW_DIR / f"weekly-review-{today}.md"
    report_file.write_text(report, encoding="utf-8")
    print(f"\n📄 审查报告已保存: {report_file}")


if __name__ == "__main__":
    main()