#!/usr/bin/env python3
"""
Evolution Cron Hook — 管线后置处理器

在每日管线完成后运行，处理 pending captures:
  1. 消费 healthcheck 写入的 pending_captures.json
  2. 扫描 Postmortem 报告中的 evolution_signals 块
  3. 检查是否有 stale DEGRADED 状态需要记录

用法:
  python scripts/evolution_cron_hook.py              # 处理今天的所有 pending
  python scripts/evolution_cron_hook.py --date 2026-10-23
  python scripts/evolution_cron_hook.py --dry-run     # 预览但不执行
"""

import argparse
import json
import re
import subprocess
import sys
import io
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_ROOT = Path("D:/AgentAlpha/my-bot")
EVOLUTION_DIR = PROJECT_ROOT / "data/evolution"
PENDING_FILE = EVOLUTION_DIR / "pending_captures.json"
CAPTURE_SCRIPT = PROJECT_ROOT / "scripts/evolution_capture.py"
REPORTS_DIR = PROJECT_ROOT / "reports/daily"


def process_pending_captures(dry_run: bool = False) -> list[dict]:
    """Process pending_captures.json written by healthcheck."""
    if not PENDING_FILE.exists():
        return []

    try:
        pending = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"⚠️ 无法读取 pending_captures.json: {e}")
        return []

    if not pending:
        return []

    results = []
    for item in pending:
        title = item.get("title", "Untitled")
        source = item.get("source", "Unknown")
        severity = item.get("severity", "P2")
        description = item.get("description", "")
        suggested = item.get("suggested_action", "")
        affected = item.get("affected_component", "")

        if dry_run:
            print(f"  [DRY-RUN] 将捕获: [{severity}] {title}")
            results.append({"title": title, "status": "dry_run"})
            continue

        cmd = [
            sys.executable, str(CAPTURE_SCRIPT), "capture",
            "--track", "internal",
            "--source", source,
            "--title", title,
            "--description", description,
            "--severity", severity,
            "--affected", affected,
            "--suggested", suggested,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
            if result.returncode == 0:
                print(f"  ✅ {title[:60]}")
                results.append({"title": title, "status": "captured"})
            else:
                # Check if it was skipped (duplicate)
                if "Duplicate of" in result.stdout:
                    print(f"  ⏭️ {title[:60]} (重复)")
                    results.append({"title": title, "status": "skipped"})
                else:
                    print(f"  ❌ {title[:60]}: {result.stderr.strip()}")
                    results.append({"title": title, "status": "error", "error": result.stderr.strip()})
        except Exception as e:
            print(f"  ❌ {title[:60]}: {e}")
            results.append({"title": title, "status": "error", "error": str(e)})

    # Clear pending file after processing (if not dry run)
    if not dry_run:
        PENDING_FILE.write_text("[]", encoding="utf-8")

    return results


def scan_postmortem_for_signals(target_date: str, dry_run: bool = False) -> list[dict]:
    """Scan postmortem report for evolution_signals blocks."""
    # Find postmortem report — various possible filenames
    report_patterns = [
        f"{target_date}-postmarket-debrief.md",
        f"{target_date}-after-close.md",
        f"{target_date}-daily-recap.md",
    ]

    results = []
    for pattern in report_patterns:
        report_path = REPORTS_DIR / pattern
        if not report_path.exists():
            continue

        try:
            content = report_path.read_text(encoding="utf-8")
        except Exception:
            continue

        # Extract evolution_signals blocks
        # Pattern: ```evolution_signals ... ```
        matches = re.findall(r'```evolution_signals\n(.*?)```', content, re.DOTALL)
        for match in matches:
            # Parse YAML-like entries
            signals = _parse_evolution_signals(match)
            for signal in signals:
                title = signal.get("signal", "")
                if not title:
                    continue

                if dry_run:
                    print(f"  [DRY-RUN] Postmortem信号: [{signal.get('severity', 'P2')}] {title}")
                    results.append({"title": title, "status": "dry_run"})
                    continue

                cmd = [
                    sys.executable, str(CAPTURE_SCRIPT), "capture",
                    "--track", "internal",
                    "--source", f"Postmortem: {target_date}",
                    "--title", title,
                    "--description", signal.get("description", ""),
                    "--severity", signal.get("severity", "P2"),
                    "--affected", signal.get("affected_component", ""),
                    "--suggested", signal.get("suggested_action", ""),
                ]
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
                    if result.returncode == 0:
                        print(f"  ✅ [Postmortem] {title[:60]}")
                        results.append({"title": title, "status": "captured"})
                    else:
                        print(f"  ⏭️ [Postmortem] {title[:60]} (skip/error)")
                except Exception as e:
                    print(f"  ❌ [Postmortem] {title[:60]}: {e}")

    return results


def _parse_evolution_signals(text: str) -> list[dict]:
    """Parse evolution_signals YAML-like block into list of dicts."""
    signals = []
    current = {}

    for line in text.strip().split("\n"):
        line = line.strip()
        if line.startswith("- signal:"):
            if current and "signal" in current:
                signals.append(current)
            current = {"signal": line.split(":", 1)[1].strip()}
        elif line.startswith("  severity:"):
            current["severity"] = line.split(":", 1)[1].strip()
        elif line.startswith("  affected_component:"):
            current["affected_component"] = line.split(":", 1)[1].strip()
        elif line.startswith("  suggested_action:"):
            current["suggested_action"] = line.split(":", 1)[1].strip()
        elif line.startswith("  description:"):
            current["description"] = line.split(":", 1)[1].strip()

    if current and "signal" in current:
        signals.append(current)

    return signals


def main():
    parser = argparse.ArgumentParser(description="Evolution Cron Hook — 管线后置处理器")
    parser.add_argument("--date", help="处理日期 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际写入")
    parser.add_argument("--skip-postmortem", action="store_true", help="跳过 Postmortem 扫描")
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    print(f"Evolution Cron Hook — {target_date}")
    print(f"{'=' * 50}")

    # 1. Process pending captures from healthcheck
    print("\n📋 处理 pending captures...")
    pending_results = process_pending_captures(dry_run=args.dry_run)

    # 2. Scan postmortem for evolution_signals
    if not args.skip_postmortem:
        print("\n📝 扫描 Postmortem evolution_signals...")
        pm_results = scan_postmortem_for_signals(target_date, dry_run=args.dry_run)
    else:
        pm_results = []

    # Summary
    total = len(pending_results) + len(pm_results)
    captured = sum(1 for r in pending_results + pm_results if r.get("status") == "captured")
    skipped = sum(1 for r in pending_results + pm_results if r.get("status") == "skipped")
    dry_runs = sum(1 for r in pending_results + pm_results if r.get("status") == "dry_run")
    errors = sum(1 for r in pending_results + pm_results if r.get("status") == "error")

    print(f"\n{'=' * 50}")
    if args.dry_run:
        print(f"🔍 DRY RUN: 将处理 {dry_runs} 条捕获")
    else:
        print(f"✅ {captured} captured | ⏭️ {skipped} skipped | ❌ {errors} errors")
    print(f"Total: {total} signals processed")


if __name__ == "__main__":
    main()