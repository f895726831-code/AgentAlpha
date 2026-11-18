#!/usr/bin/env python3
"""
El Niño Pipeline Orchestrator — 一站式运行 ENSO 数据→期货→报告的完整管线

用法:
  python scripts/run_el_nino_pipeline.py              # 完整运行
  python scripts/run_el_nino_pipeline.py --skip-futures  # 跳过期货（akshare不可用时）
  python scripts/run_el_nino_pipeline.py --report-only    # 仅生成报告

集成:
  - 峰值期（2026年11月）：每周一+周四 08:00 由 Windows Task Scheduler 触发
  - 峰值过后恢复每周一一次
  - 每日可单独调用 --report-only 生成快照
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Fix GBK encoding on Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DATA_DIR = PROJECT_ROOT / "data" / "output"

# Script paths
FETCH_ENSO = SCRIPTS_DIR / "fetch_enso_data.py"
FETCH_FUTURES = SCRIPTS_DIR / "fetch_el_nino_futures.py"
GEN_REPORT = SCRIPTS_DIR / "generate_el_nino_report.py"

# Output files
ENSO_OUTPUT = DATA_DIR / "enso_data.json"
FUTURES_OUTPUT = DATA_DIR / "el_nino_futures.json"
REPORTS_DIR = PROJECT_ROOT / "reports" / "el-nino"


def run_step(script: Path, args: list[str] = None, description: str = "") -> bool:
    """Run a Python script step. Returns True on success."""
    cmd = [sys.executable, str(script)]
    if args:
        cmd.extend(args)
    print(f"\n{'='*60}")
    print(f"[{description}] Running: {' '.join(cmd)}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=False)
    success = result.returncode == 0
    print(f"[{description}] {'PASSED' if success else 'FAILED'} (exit {result.returncode})")
    return success


def check_data_freshness(path: Path, max_hours: float) -> bool:
    """Check if a data file is fresh enough."""
    if not path.exists():
        return False
    mtime = datetime.fromtimestamp(path.stat().st_mtime)
    age = datetime.now() - mtime
    return age < timedelta(hours=max_hours)


def run_pipeline(skip_futures: bool = False, report_only: bool = False,
                 skip_enso: bool = False, force_enso: bool = False) -> dict:
    """Run the full El Niño pipeline.

    Returns status dict.
    """
    status = {
        "timestamp": datetime.now().isoformat(),
        "enso": "skipped",
        "futures": "skipped",
        "report": "skipped",
        "errors": [],
    }

    if report_only:
        # Only generate report from existing data
        if not ENSO_OUTPUT.exists():
            status["errors"].append("enso_data.json missing — cannot generate report")
            return status
        report_ok = run_step(GEN_REPORT, description="Generate El Niño Report")
        status["report"] = "ok" if report_ok else "failed"
        return status

    # Step 1: Fetch ENSO data (skip if fresh within 6 hours, unless forced)
    if not skip_enso:
        if force_enso or not check_data_freshness(ENSO_OUTPUT, max_hours=6):
            enso_args = ["--force"] if force_enso else []
            enso_ok = run_step(FETCH_ENSO, args=enso_args, description="Fetch ENSO Data")
            status["enso"] = "ok" if enso_ok else "failed"
            if not enso_ok:
                status["errors"].append("ENSO fetch failed")
                return status
        else:
            print(f"[ENSO] Data is fresh ({ENSO_OUTPUT}), skipping fetch")
            status["enso"] = "cached"

    # Step 2: Fetch futures data
    if not skip_futures:
        futures_args = ["--enso-file", str(ENSO_OUTPUT)]
        futures_ok = run_step(FETCH_FUTURES, args=futures_args, description="Fetch Futures Data")
        status["futures"] = "ok" if futures_ok else "failed"
        if not futures_ok:
            status["errors"].append("Futures fetch failed — continuing with report anyway")
    else:
        status["futures"] = "skipped_by_user"

    # Step 3: Generate report
    report_ok = run_step(GEN_REPORT, description="Generate El Niño Report")
    status["report"] = "ok" if report_ok else "failed"

    if report_ok:
        # Find the generated report
        reports = sorted(REPORTS_DIR.glob("*-el-nino-monitor.md"), reverse=True)
        if reports:
            status["report_path"] = str(reports[0])

    # Summary
    print(f"\n{'='*60}")
    print("El Niño Pipeline Summary")
    print(f"{'='*60}")
    for step in ["enso", "futures", "report"]:
        print(f"  {step}: {status[step]}")
    if status["errors"]:
        print(f"  Errors: {status['errors']}")
    print(f"{'='*60}")

    return status


# --- CLI ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="El Niño Pipeline — ENSO数据→期货→报告 一站式运行"
    )
    parser.add_argument("--skip-futures", action="store_true",
                        help="Skip futures data fetch (if akshare unavailable)")
    parser.add_argument("--skip-enso", action="store_true",
                        help="Skip ENSO fetch (use cached data)")
    parser.add_argument("--force-enso", action="store_true",
                        help="Force fresh ENSO fetch")
    parser.add_argument("--report-only", action="store_true",
                        help="Only generate report from existing data")
    parser.add_argument("--json", action="store_true",
                        help="Output status JSON to stdout")

    args = parser.parse_args()

    status = run_pipeline(
        skip_futures=args.skip_futures,
        report_only=args.report_only,
        skip_enso=args.skip_enso,
        force_enso=args.force_enso,
    )

    if args.json:
        print(json.dumps(status, indent=2, ensure_ascii=False))

    # Exit with non-zero if critical errors
    has_critical = any(
        e in status.get("errors", [])
        for e in ["enso_data.json missing", "ENSO fetch failed"]
    )
    sys.exit(1 if has_critical else 0)