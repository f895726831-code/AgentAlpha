#!/usr/bin/env python3
"""
进化捕获工具 — Track A 内部问题自动记录

可被多个触点调用：
  - Backtester: Abandon/Refine 判決后
  - Healthcheck: CRITICAL/WARNING 告警后
  - Factor Lab: 新因子 IC < 0.01
  - Postmortem: evolution_signals 字段
  - 手动: python scripts/evolution_capture.py --source "..." --title "..."

用法:
  python scripts/evolution_capture.py \
    --track internal \
    --source "Backtester: 300454.SZ breakout_60 Abandon" \
    --title " breakout_60 策略回测 Abandon" \
    --description "..." \
    --severity P1 \
    --affected pipeline.Gate1 \
    --suggested "回退参数，增加止盈条件后重新回测"

  # 从 stdin 读取（兼容 postmortem evolution_signals 输出）
  echo '{"signal":"...","severity":"P1","...}' | python scripts/evolution_capture.py --stdin

  # 更新已有条目状态
  python scripts/evolution_capture.py --update EVO-2026-001 --status integrated --resolved 2026-10-23
"""

import argparse
import json
import os
import sys
import io
import re
import tempfile
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path("D:/AgentAlpha/my-bot")
EVOLUTION_DIR = PROJECT_ROOT / "data/evolution"
CAPTURES_DIR = EVOLUTION_DIR / "internal_captures"
BACKLOG_FILE = EVOLUTION_DIR / "backlog.json"


def _load_backlog() -> dict:
    if BACKLOG_FILE.exists():
        return json.loads(BACKLOG_FILE.read_text(encoding="utf-8"))
    return {"version": "1.0", "updated": datetime.now().strftime("%Y-%m-%d"), "entries": [], "stats": {}}


def _atomic_write(filepath: Path, content: str):
    """Write content to file atomically: write to temp, then rename.

    Prevents data loss from truncated writes (e.g., Unicode errors mid-write
    after file has already been opened in 'w' mode).
    """
    fd, tmp = tempfile.mkstemp(dir=filepath.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, filepath)  # atomic on Windows (same volume)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _save_backlog(backlog: dict):
    EVOLUTION_DIR.mkdir(parents=True, exist_ok=True)
    # Update stats
    entries = backlog["entries"]
    backlog["stats"] = {
        "total": len(entries),
        "by_track": {
            "internal": sum(1 for e in entries if e["track"] == "internal"),
            "external": sum(1 for e in entries if e["track"] == "external"),
        },
        "by_status": {},
    }
    for e in entries:
        s = e["status"]
        backlog["stats"]["by_status"][s] = backlog["stats"]["by_status"].get(s, 0) + 1
    if entries:
        backlog["stats"]["by_severity_internal"] = {}
        for e in entries:
            if e["track"] == "internal" and "severity" in e:
                sev = e["severity"]
                backlog["stats"]["by_severity_internal"][sev] = backlog["stats"]["by_severity_internal"].get(sev, 0) + 1
        external_scores = [e.get("composite_score", 0) for e in entries if e["track"] == "external"]
        if external_scores:
            backlog["stats"]["external_avg_score"] = round(sum(external_scores) / len(external_scores), 2)

    backlog["updated"] = datetime.now().strftime("%Y-%m-%d")
    _atomic_write(BACKLOG_FILE, json.dumps(backlog, ensure_ascii=False, indent=2))


def _next_id(backlog: dict) -> str:
    existing = [int(e["id"].split("-")[-1]) for e in backlog["entries"] if e["id"].startswith("EVO-")]
    next_num = max(existing) + 1 if existing else 1
    return f"EVO-{datetime.now().year}-{next_num:03d}"


def _slugify(title: str) -> str:
    """Generate a filesystem-safe slug from a title."""
    slug = re.sub(r'[^\w\s-]', '', title.lower())
    slug = re.sub(r'[-\s]+', '-', slug)
    return slug[:60].strip('-')


def capture(args) -> dict:
    """Create a new evolution capture."""
    backlog = _load_backlog()

    # Check for duplicates (same title in last 30 days)
    for e in backlog["entries"]:
        if e.get("title") == args.title and e.get("status") != "rejected":
            recent = (datetime.now() - datetime.strptime(e["created"], "%Y-%m-%d")).days
            if recent < 30:
                return {
                    "status": "skipped",
                    "reason": f"Duplicate of {e['id']} (created {e['created']}, {recent}d ago)",
                    "existing_id": e["id"],
                }

    entry_id = _next_id(backlog)
    slug = _slugify(args.title)
    today = datetime.now().strftime("%Y-%m-%d")

    entry = {
        "id": entry_id,
        "track": args.track or "internal",
        "source": args.source,
        "title": args.title,
        "description": args.description or "",
        "severity": args.severity,
        "proposed_solution": args.suggested or "",
        "expected_impact": args.impact or "",
        "validation_method": args.validation or "",
        "status": "captured",
        "dependencies": [],
        "created": today,
        "resolved": None,
    }

    # Link to memory file for deterministic bridge matching
    if getattr(args, "memory_ref", None):
        entry["memory_ref"] = args.memory_ref

    backlog["entries"].append(entry)
    _save_backlog(backlog)

    # Write detailed capture file
    CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
    capture_file = CAPTURES_DIR / f"{today}-{slug}.md"
    capture_content = f"""# {args.title}

> 捕获时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | ID: {entry_id} | Track: {entry['track']}
> 来源: {args.source}
> 严重度: {args.severity or 'N/A'}

## 描述

{args.description or '(无详细描述)'}

## 受影响组件

{args.affected or '(未指定)'}

## 建议方案

{args.suggested or '(待诊断)'}

## 预期影响

{args.impact or '(待评估)'}

## 验证方法

{args.validation or '(待确定)'}

---
> 自动生成于 evolution_capture.py | Phase 2 自动捕获
"""
    capture_file.write_text(capture_content, encoding="utf-8")

    return {
        "status": "captured",
        "id": entry_id,
        "file": str(capture_file),
    }


def update_entry(args) -> dict:
    """Update an existing backlog entry's status."""
    backlog = _load_backlog()

    for e in backlog["entries"]:
        if e["id"] == args.update:
            if args.status:
                e["status"] = args.status
            if args.resolved:
                e["resolved"] = args.resolved
            if args.diagnosis:
                e["description"] = (e.get("description", "") + f"\n\n诊断结论 ({datetime.now().strftime('%Y-%m-%d')}): {args.diagnosis}").strip()
            _save_backlog(backlog)
            return {"status": "updated", "id": args.update, "new_status": args.status}
    return {"status": "error", "reason": f"Entry {args.update} not found"}


def stdin_capture() -> dict:
    """Capture from stdin JSON (for postmortem evolution_signals pipeline)."""
    raw = sys.stdin.read().strip()
    if not raw:
        return {"status": "skipped", "reason": "Empty stdin"}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try parsing as a list of YAML-like entries
        return {"status": "error", "reason": f"Invalid JSON from stdin: {raw[:100]}"}

    if isinstance(data, dict):
        data = [data]

    results = []
    for item in data:
        signal = item.get("signal", item.get("title", ""))
        if not signal:
            continue

        result = capture(argparse.Namespace(
            track="internal",
            source=item.get("source", "Postmortem evolution_signals"),
            title=signal,
            description=item.get("description", ""),
            severity=item.get("severity", "P2"),
            suggested=item.get("suggested_action", ""),
            impact=item.get("impact", ""),
            validation=item.get("validation", ""),
            affected=item.get("affected_component", ""),
        ))
        results.append(result)

    return {"status": "ok", "captured": len(results), "results": results}


def list_backlog(args) -> dict:
    """List backlog entries with optional filters."""
    backlog = _load_backlog()

    entries = backlog["entries"]
    if args.status_filter:
        entries = [e for e in entries if e["status"] == args.status_filter]
    if args.track_filter:
        entries = [e for e in entries if e["track"] == args.track_filter]
    if args.severity_filter:
        entries = [e for e in entries if e.get("severity") == args.severity_filter]

    # Sort: active first, then by creation date
    active_statuses = {"captured", "diagnosed", "designed", "validating"}
    entries = sorted(entries, key=lambda e: (
        0 if e["status"] in active_statuses else 1,
        e["created"],
    ))

    for e in entries:
        sev_tag = f"[{e.get('severity', 'N/A')}]" if e["track"] == "internal" else f"[score={e.get('composite_score', '?')}]"
        print(f"  {e['id']} {sev_tag} [{e['status']}] {e['title']}")

    active = [e for e in entries if e["status"] in active_statuses]
    print(f"\n{len(active)} active / {len(entries)} total")

    return {"status": "ok", "count": len(entries)}


def main():
    parser = argparse.ArgumentParser(description="进化捕获工具 — Track A/B 自动记录")
    sub = parser.add_subparsers(dest="command", help="命令")

    # capture
    cap = sub.add_parser("capture", help="创建新的进化捕获")
    cap.add_argument("--track", default="internal", choices=["internal", "external"])
    cap.add_argument("--source", required=True, help="触发来源")
    cap.add_argument("--title", required=True, help="一句话标题")
    cap.add_argument("--description", help="详细描述")
    cap.add_argument("--severity", choices=["P0", "P1", "P2", "P3"], help="严重度 (Track A)")
    cap.add_argument("--composite-score", type=float, help="四维综合分 (Track B)")
    cap.add_argument("--affected", help="受影响组件")
    cap.add_argument("--suggested", help="建议方案")
    cap.add_argument("--impact", help="预期影响")
    cap.add_argument("--validation", help="验证方法")
    cap.add_argument("--memory-ref", help="关联的 memory 文件名 (不含路径/扩展名)，用于 memory→evolution 桥接")

    # update
    upd = sub.add_parser("update", help="更新已有条目")
    upd.add_argument("--update", required=True, help="条目 ID")
    upd.add_argument("--status", choices=["captured", "diagnosed", "designed", "validating", "integrated", "rejected"])
    upd.add_argument("--resolved", help="解决日期 YYYY-MM-DD")
    upd.add_argument("--diagnosis", help="根因诊断结论")

    # stdin
    stdin_p = sub.add_parser("stdin", help="从 stdin JSON 批量捕获")

    # list
    list_p = sub.add_parser("list", help="列出 backlog")
    list_p.add_argument("--status", dest="status_filter")
    list_p.add_argument("--track", dest="track_filter")
    list_p.add_argument("--severity", dest="severity_filter")

    args = parser.parse_args()

    if args.command == "capture":
        result = capture(args)
    elif args.command == "update":
        result = update_entry(args)
    elif args.command == "stdin":
        result = stdin_capture()
    elif args.command == "list":
        result = list_backlog(args)
    else:
        parser.print_help()
        sys.exit(1)

    if result.get("status") == "error":
        print(f"Error: {result.get('reason')}", file=sys.stderr)
        sys.exit(1)

    # Print summary
    if args.command == "capture" and result.get("status") == "captured":
        print(f"✅ {result['id']} → {result['file']}")
    elif args.command == "update":
        print(f"✅ {result['id']} → {result.get('new_status', 'updated')}")


if __name__ == "__main__":
    main()