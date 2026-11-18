"""
信号工厂每日自动化流水线

盘前自动运行，为新一天交易提供 ML alpha 信号参考。

流程:
  1. 增量数据更新（下载昨日收盘数据 + 转 bin）
  1.5 板块数据采集（Layer 1，31 申万行业日线）
  1.6 板块轮动信号生成（Layer 1，在 alpha 信号前独立运行）
  2. 论文因子挖掘（factor-lab API，可选）
  3. Alpha 信号生成（ensemble 多模型预测）
  4. 同步到 AgentAlpha（JSON 桥接）

用法:
    python scripts/daily_pipeline.py                    # 默认：更新数据 + 生成信号 + 同步
    python scripts/daily_pipeline.py --skip-update      # 跳过数据更新
    python scripts/daily_pipeline.py --skip-sync        # 跳过 AgentAlpha 同步
    python scripts/daily_pipeline.py --paper-mining     # 启用论文因子挖掘
    python scripts/daily_pipeline.py --date 2026-08-14  # 指定日期
    python scripts/daily_pipeline.py --dry-run          # 仅检查，不执行
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# Fix Windows GBK encoding for subprocess output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_QSF_ROOT))

# ─── 路径配置 ───

PYTHON_EXE = sys.executable
SCRIPTS_DIR = _QSF_ROOT / "scripts"
UPDATE_SCRIPT = SCRIPTS_DIR / "update_daily.py"
ALPHA_MODULE = "src.factors.alpha_signals"
SYNC_SCRIPT = Path("D:/AgentAlpha/my-bot/scripts/sync_signal_factory.py")
P1_SIGNALS_SCRIPT = Path("D:/AgentAlpha/my-bot/scripts/wsl2/scripts/generate_p1_signals.py")
P1_SIGNALS_CWD = Path("D:/AgentAlpha/my-bot/scripts/wsl2")
P1_INCREMENT_SCRIPT = Path("D:/AgentAlpha/my-bot/scripts/wsl2/scripts/increment_p1_factors.py")

# 输出文件
SIGNALS_OUTPUT = _QSF_ROOT / "data/output/alpha_signals.json"
SIGNALS_CSI300_OUTPUT = _QSF_ROOT / "data/output/alpha_signals_csi300.json"
SIGNALS_LATEST_OUTPUT = _QSF_ROOT / "data/output/alpha_signals_latest.json"
SIGNALS_LATEST_CSI300_OUTPUT = _QSF_ROOT / "data/output/alpha_signals_latest_csi300.json"
COLLECT_SECTOR_SCRIPT = SCRIPTS_DIR / "collect_sector_data.py"
SECTOR_SIGNALS_SCRIPT = SCRIPTS_DIR / "generate_sector_signals.py"
PIPELINE_LOG = _QSF_ROOT / "log/pipeline.log"


def get_default_date() -> str:
    """获取默认预测日期（今天）"""
    return datetime.now().strftime("%Y-%m-%d")


def run_step(name: str, cmd: list, timeout: int = 600, cwd: str = None) -> Dict:
    """执行一个步骤，返回结果"""
    print(f"\n{'─' * 50}")
    print(f"[{name}] 开始...")
    print(f"  命令: {' '.join(cmd)}")
    t0 = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
        )
        elapsed = round(time.time() - t0, 1)
        success = result.returncode == 0

        # 打印输出（截断过长内容）
        stdout = result.stdout
        if len(stdout) > 2000:
            stdout = stdout[:1000] + "\n... (截断) ...\n" + stdout[-1000:]

        if success:
            print(f"[{name}] 完成 ({elapsed}s)")
        else:
            print(f"[{name}] 失败 (exit={result.returncode}, {elapsed}s)")
            if result.stderr:
                stderr_short = result.stderr[:500]
                print(f"  stderr: {stderr_short}")

        return {
            "step": name,
            "success": success,
            "exit_code": result.returncode,
            "elapsed": elapsed,
            "stdout": stdout,
            "stderr": result.stderr[:500] if result.stderr else "",
        }

    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 1)
        print(f"[{name}] 超时 ({timeout}s)")
        return {"step": name, "success": False, "exit_code": -1, "elapsed": elapsed, "stdout": "", "stderr": f"超时 ({timeout}s)"}
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        print(f"[{name}] 异常: {e}")
        return {"step": name, "success": False, "exit_code": -1, "elapsed": elapsed, "stdout": "", "stderr": str(e)}


def step_update_data(date: str, dry_run: bool = False) -> Dict:
    """Step 1: 增量数据更新"""
    if dry_run:
        print("  [DRY-RUN] 跳过数据下载")
        return {"step": "数据更新", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    cmd = [PYTHON_EXE, str(UPDATE_SCRIPT), "--end", date]
    return run_step("数据更新", cmd, timeout=1200)


def step_generate_signals(date: str, instruments: str = "csi500", output: Optional[Path] = None, neutralize_industry: bool = False, dry_run: bool = False) -> Dict:
    """Step 2: Alpha 信号生成（ensemble 模式，label 约束）"""
    out = output or SIGNALS_OUTPUT
    cmd = [
        PYTHON_EXE, "-m", ALPHA_MODULE,
        "--ensemble",
        "--models", "catboost", "xgboost", "gbdt",
        "--instruments", instruments,
        "--date", date,
        "--no-train",
        "--top-n", "50",
        "--output", str(out),
    ]
    if neutralize_industry:
        cmd.append("--neutralize-industry")
    if dry_run:
        label = "CSI 300" if instruments == "csi300" else "CSI 500"
        print(f"  [DRY-RUN] 跳过 {label} 信号生成")
        return {"step": f"{label} 信号生成", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    return run_step(f"{'CSI 300' if instruments == 'csi300' else 'CSI 500'} 信号生成", cmd, timeout=1200)


def step_generate_infer_signals(date: str, instruments: str = "csi500", output: Optional[Path] = None, neutralize_industry: bool = False, dry_run: bool = False) -> Dict:
    """Step 2.5: 纯推断信号生成（跳过 label 约束，使用最新特征）"""
    out = output or (SIGNALS_LATEST_OUTPUT if instruments == "csi500" else SIGNALS_LATEST_CSI300_OUTPUT)
    cmd = [
        PYTHON_EXE, "-m", ALPHA_MODULE,
        "--ensemble",
        "--models", "catboost", "xgboost", "gbdt",
        "--instruments", instruments,
        "--date", date,
        "--no-train",
        "--infer-only",
        "--top-n", "50",
        "--output", str(out),
    ]
    if neutralize_industry:
        cmd.append("--neutralize-industry")
    if dry_run:
        label = "CSI 300" if instruments == "csi300" else "CSI 500"
        print(f"  [DRY-RUN] 跳过 {label} 推断信号生成")
        return {"step": f"{label} 推断信号生成", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    return run_step(f"{'CSI 300' if instruments == 'csi300' else 'CSI 500'} 推断信号生成", cmd, timeout=1200)


def step_generate_sector_signals(date: str, dry_run: bool = False) -> Dict:
    """Step 3.6: Layer 1 板块轮动信号生成"""
    cmd = [PYTHON_EXE, str(SECTOR_SIGNALS_SCRIPT), "--date", date]
    if dry_run:
        print("  [DRY-RUN] 跳过板块信号生成")
        return {"step": "板块轮动信号", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    return run_step("板块轮动信号", cmd, timeout=300)


def step_collect_sector_data(date: str, dry_run: bool = False) -> Dict:
    """Step 1.5: Layer 1 板块数据采集（在 alpha 信号之前运行）"""
    cmd = [PYTHON_EXE, str(COLLECT_SECTOR_SCRIPT)]
    if dry_run:
        print("  [DRY-RUN] 跳过板块数据采集")
        return {"step": "板块数据采集", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    return run_step("板块数据采集", cmd, timeout=600)


def step_generate_p1_signals(date: str, dry_run: bool = False) -> Dict:
    """Step 2.6: Phase 1 因子共识信号生成"""
    cmd = [PYTHON_EXE, str(P1_SIGNALS_SCRIPT), "--date", date]
    if dry_run:
        print("  [DRY-RUN] 跳过 Phase 1 因子共识信号生成")
        return {"step": "Phase 1 因子共识", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    return run_step("Phase 1 因子共识", cmd, timeout=600, cwd=str(P1_SIGNALS_CWD))


def step_increment_p1_factors(date: str, dry_run: bool = False) -> Dict:
    """Step 2.55: Phase 1 因子增量更新 — 扩展 bin 文件到最新日期"""
    if not P1_INCREMENT_SCRIPT.exists():
        print(f"  [WARN] 增量更新脚本不存在: {P1_INCREMENT_SCRIPT}")
        return {"step": "P1 因子增量更新", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "skipped (script not found)", "stderr": ""}
    cmd = [PYTHON_EXE, str(P1_INCREMENT_SCRIPT)]
    if dry_run:
        print("  [DRY-RUN] 跳过 P1 因子增量更新")
        return {"step": "P1 因子增量更新", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    return run_step("P1 因子增量更新", cmd, timeout=600, cwd=str(P1_SIGNALS_CWD))


def step_sync_to_fm_trade(date: str, dry_run: bool = False) -> Dict:
    """Step 3: 同步到 AgentAlpha"""
    if not SYNC_SCRIPT.exists():
        print(f"  [WARN] 同步脚本不存在: {SYNC_SCRIPT}")
        return {"step": "AgentAlpha 同步", "success": False, "exit_code": -1, "elapsed": 0, "stdout": "", "stderr": str(SYNC_SCRIPT) + " 不存在"}

    cmd = [PYTHON_EXE, str(SYNC_SCRIPT)]
    if dry_run:
        print("  [DRY-RUN] 跳过同步")
        return {"step": "AgentAlpha 同步", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}
    return run_step("AgentAlpha 同步", cmd, timeout=60)


def step_factor_lab_papers(date: str, dry_run: bool = False, max_papers: int = 3) -> Dict:
    """Step 2.5: 论文因子挖掘（factor-lab API）"""
    print(f"\n{'─' * 50}")
    print(f"[论文因子挖掘] 开始...")
    t0 = time.time()

    if dry_run:
        print("  [DRY-RUN] 跳过论文因子挖掘")
        return {"step": "论文因子挖掘", "success": True, "exit_code": 0, "elapsed": 0, "stdout": "(dry-run)", "stderr": ""}

    try:
        from src.factor_lab_client import FactorLabClient

        client = FactorLabClient()

        # 健康检查
        health = client.health_check()
        if not health.get("status") == "ok":
            print(f"  [WARN] factor-lab 不可用: {health}")
            print("  请确认 WSL2 factor-lab 已启动")
            return {"step": "论文因子挖掘", "success": False, "exit_code": -1, "elapsed": round(time.time() - t0, 1), "stdout": "", "stderr": "factor-lab 不可用"}

        print(f"  ✅ factor-lab 已连接 (factors={health.get('factors_count', 0)})")

        # 触发论文因子挖掘
        task = client.mine_papers(max_papers=max_papers, date=date)
        print(f"  任务已提交: {task['task_id']}")

        # 轮询等待
        result = client.wait_for_task(
            task["task_id"],
            timeout=600,
            on_progress=lambda p, s: print(f"  进度: {p:.0%} ({s})") if p > 0 else None,
        )

        summary = result.get("summary", {})
        elapsed = round(time.time() - t0, 1)
        print(f"[论文因子挖掘] 完成 ({elapsed}s)")
        print(f"  论文: {summary.get('papers_processed', 0)} 篇 → "
              f"因子: {summary.get('factors_extracted', 0)} → "
              f"入库: {summary.get('stored', 0)}")

        return {
            "step": "论文因子挖掘",
            "success": True,
            "exit_code": 0,
            "elapsed": elapsed,
            "stdout": json.dumps(summary, ensure_ascii=False),
            "stderr": "",
        }

    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        print(f"[论文因子挖掘] 异常: {e}")
        return {"step": "论文因子挖掘", "success": False, "exit_code": -1, "elapsed": elapsed, "stdout": "", "stderr": str(e)}


def verify_output() -> Dict:
    """验证输出文件"""
    result = {"signals_exists": False, "stock_count": 0, "date": None, "model": None,
              "sector_signals_exists": False, "sector_top_5": []}

    if SIGNALS_OUTPUT.exists():
        result["signals_exists"] = True
        try:
            with open(SIGNALS_OUTPUT, "r", encoding="utf-8") as f:
                data = json.load(f)
            result["stock_count"] = data.get("total_stocks", 0)
            result["date"] = data.get("date")
            result["model"] = data.get("model")
        except Exception:
            pass

    sector_out = _QSF_ROOT / "data/output/sector_signals.json"
    if sector_out.exists():
        result["sector_signals_exists"] = True
        try:
            with open(sector_out, "r", encoding="utf-8") as f:
                sector_data = json.load(f)
            result["sector_top_5"] = [s["sector_name"] for s in sector_data.get("top_5_selected", [])]
        except Exception:
            pass

    return result


def healthcheck_bin_coverage(sample_size: int = 20):
    """检查 bin 文件覆盖率：抽检 CSI500 样本，检测最新日期的 close 是否有有效数据。

    返回 (pass_rate, issues_summary) — 若 pass_rate < 0.5 打印告警。
    """
    import numpy as np

    cal_file = Path("D:/qlib/qlib_data/cn_data/calendars/day.txt")
    inst_file = Path("D:/qlib/qlib_data/cn_data/instruments/csi500.txt")
    bin_dir = Path("D:/qlib/qlib_data/cn_data/features")

    if not cal_file.exists() or not inst_file.exists() or not bin_dir.exists():
        return None, "health data not available"

    cal_dates = [line.strip() for line in open(cal_file, encoding="utf-8") if line.strip() and line.strip() != "1970-01-01"]
    if not cal_dates:
        return None, "empty calendar"

    latest_cal = cal_dates[-1]
    latest_idx = len(cal_dates) - 1

    with open(inst_file, encoding="utf-8") as f:
        stocks = [line.strip().split("\t")[0] for line in f if line.strip()]

    # Sample evenly
    step = max(1, len(stocks) // sample_size)
    sample = stocks[::step][:sample_size]

    valid = 0
    missing = 0
    nan_count = 0
    for stock in sample:
        fp = bin_dir / stock / "close.day.bin"
        if not fp.exists():
            missing += 1
            continue
        data = np.fromfile(fp, dtype="<f")
        if len(data) <= 1:
            missing += 1
            continue
        sidx = int(data[0])
        rel_idx = latest_idx - sidx
        if rel_idx < 0 or rel_idx >= len(data) - 1:
            missing += 1
        elif np.isnan(data[1 + rel_idx]):
            nan_count += 1
        else:
            valid += 1

    pass_rate = valid / len(sample) if sample else 0
    issues = f"latest={latest_cal} valid={valid}/{len(sample)} missing={missing} nan={nan_count}"

    if pass_rate < 0.5:
        print(f"\n  🚨 BIN COVERAGE ALERT: only {valid}/{len(sample)} ({pass_rate:.0%}) have valid close for {latest_cal}!")
        print(f"     Missing: {missing}, NaN: {nan_count}")
        print(f"     Check: update_daily.py akshare/baostock data source status")

    return pass_rate, issues


def healthcheck_p1_coverage():
    """检查 P1 因子覆盖度趋势，与上次对比，>10% 下降则告警。

    从 akshare_health.json 读取历史，更新当前覆盖度。
    """
    p1_signal_file = Path("D:/AgentAlpha/my-bot/data/output/alpha_signals_p1.json")
    health_file = Path("D:/AgentAlpha/my-bot/data/output/akshare_health.json")

    if not p1_signal_file.exists():
        return None

    try:
        with open(p1_signal_file, "r", encoding="utf-8") as f:
            p1_data = json.load(f)
    except Exception:
        return None

    factors_used = p1_data.get("factors_used", 0)
    factors_loaded = p1_data.get("factors_loaded", 0)
    coverage_note = p1_data.get("coverage_note", "")
    p1_date = p1_data.get("date", "unknown")

    # Parse coverage from note: "37/37 factors had >= 30 valid stocks"
    import re
    m = re.search(r"(\d+)/(\d+)\s*factors", coverage_note)
    factors_valid = int(m.group(1)) if m else factors_used
    factors_total = int(m.group(2)) if m else factors_loaded
    coverage_pct = round(factors_valid / factors_total * 100, 1) if factors_total > 0 else 0

    # Load history
    history = {}
    if health_file.exists():
        try:
            with open(health_file, "r", encoding="utf-8") as f:
                health_data = json.load(f)
            history = health_data.get("p1_coverage_history", {})
        except Exception:
            pass

    # Get previous entry
    prev_dates = sorted(history.keys())
    prev_entry = history[prev_dates[-1]] if prev_dates else None

    current = {
        "date": p1_date,
        "factors_valid": factors_valid,
        "factors_total": factors_total,
        "coverage_pct": coverage_pct,
        "coverage_note": coverage_note,
    }

    # Compare
    alert = ""
    if prev_entry:
        prev_pct = prev_entry.get("coverage_pct", 0)
        drop = prev_pct - coverage_pct
        if drop > 10:
            alert = (f"⚠️  P1 COVERAGE DROP: {prev_pct:.1f}% → {coverage_pct:.1f}% (-{drop:.1f}pp)\n"
                     f"     factors: {prev_entry.get('factors_valid')}/{prev_entry.get('factors_total')} → "
                     f"{factors_valid}/{factors_total}")
            print(f"\n  {alert}")

    # Update history
    history[p1_date] = current
    # Keep last 30 entries
    if len(history) > 30:
        oldest = sorted(history.keys())[0]
        del history[oldest]

    health_file.parent.mkdir(parents=True, exist_ok=True)
    with open(health_file, "w", encoding="utf-8") as f:
        json.dump({"p1_coverage_history": history, "last_updated": datetime.now().isoformat()},
                  f, indent=2, ensure_ascii=False)

    print(f"  📊 P1 Coverage: {factors_valid}/{factors_total} ({coverage_pct:.1f}%) on {p1_date}"
          + (f" (prev: {prev_pct:.1f}%)" if prev_entry else ""))

    return {"current": current, "previous": prev_entry, "alert": alert}


def log_pipeline(summary: Dict):
    """记录流水线执行日志"""
    PIPELINE_LOG.parent.mkdir(parents=True, exist_ok=True)

    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "date": summary.get("date"),
        "status": summary.get("status"),
        "duration": summary.get("total_elapsed"),
        "steps": summary.get("steps", []),
        "output": summary.get("output", {}),
    }

    # 追加到日志文件
    with open(PIPELINE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="信号工厂每日自动化流水线")
    parser.add_argument("--date", help="目标日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--skip-update", action="store_true", help="跳过数据更新")
    parser.add_argument("--skip-sync", action="store_true", help="跳过 AgentAlpha 同步")
    parser.add_argument("--skip-infer", action="store_true", help="跳过纯推断信号生成")
    parser.add_argument("--skip-sector", action="store_true", help="跳过板块轮动信号生成")
    parser.add_argument("--dry-run", action="store_true", help="仅检查，不执行")
    parser.add_argument("--paper-mining", action="store_true", help="启用论文因子挖掘（factor-lab）")
    parser.add_argument("--paper-max", type=int, default=3, help="论文挖掘最多处理论文数")
    args = parser.parse_args()

    date = args.date or get_default_date()

    print("=" * 60)
    print(f"信号工厂每日自动化流水线")
    print(f"日期: {date}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if args.dry_run:
        print("模式: DRY-RUN（仅检查）")
    print("=" * 60)

    total_start = time.time()
    steps = []

    # Step 1: 数据更新
    if not args.skip_update:
        result = step_update_data(date, dry_run=args.dry_run)
        steps.append(result)
        if not result["success"]:
            print("\n[WARN] 数据更新失败，但继续执行后续步骤...")
        else:
            healthcheck_bin_coverage()
    else:
        print("\n[跳过] 数据更新")

    # Step 1.5: 板块数据采集 (Layer 1) — 在 alpha 信号之前运行，独立不阻塞
    if not args.skip_sector:
        result = step_collect_sector_data(date, dry_run=args.dry_run)
        steps.append(result)
        if not result["success"]:
            print("\n[WARN] 板块数据采集失败，但不阻塞流水线")
    else:
        print("\n[跳过] 板块数据采集")

    # Step 1.6: 板块轮动信号生成 (Layer 1) — 在 alpha 信号之前运行，独立不阻塞
    if not args.skip_sector:
        result = step_generate_sector_signals(date, dry_run=args.dry_run)
        steps.append(result)
        if not result["success"]:
            print("\n[WARN] 板块轮动信号生成失败，但不阻塞流水线")
    else:
        print("\n[跳过] 板块轮动信号生成")

    # Step 2: 论文因子挖掘（factor-lab，可选）
    if args.paper_mining:
        result = step_factor_lab_papers(date, dry_run=args.dry_run, max_papers=args.paper_max)
        steps.append(result)
        # 论文因子挖掘失败不阻塞流水线
    elif not args.dry_run:
        print("\n[跳过] 论文因子挖掘（使用 --paper-mining 启用）")

    # Step 3: 信号生成 (CSI 500)
    result = step_generate_signals(date, instruments="csi500", dry_run=args.dry_run)
    steps.append(result)
    if not result["success"]:
        print("\n[FAIL] CSI 500 信号生成失败，流水线中止")
        summary = {
            "date": date,
            "status": "failed",
            "total_elapsed": round(time.time() - total_start, 1),
            "steps": steps,
            "output": verify_output(),
        }
        log_pipeline(summary)
        _print_summary(summary)
        sys.exit(1)

    # Step 3b: 信号生成 (CSI 300)
    result = step_generate_signals(date, instruments="csi300", output=SIGNALS_CSI300_OUTPUT, neutralize_industry=True, dry_run=args.dry_run)
    steps.append(result)
    if not result["success"]:
        print("\n[FAIL] CSI 300 信号生成失败，流水线中止")
        summary = {
            "date": date,
            "status": "failed",
            "total_elapsed": round(time.time() - total_start, 1),
            "steps": steps,
            "output": verify_output(),
        }
        log_pipeline(summary)
        _print_summary(summary)
        sys.exit(1)

    # Step 3.5: 纯推断信号生成 (CSI 500)
    if not args.skip_infer:
        result = step_generate_infer_signals(date, instruments="csi500", dry_run=args.dry_run)
        steps.append(result)
        if not result["success"]:
            print("\n[WARN] CSI 500 推断信号生成失败，但不阻塞流水线")
    else:
        print("\n[跳过] CSI 500 推断信号生成")

    # Step 3.5b: 纯推断信号生成 (CSI 300)
    if not args.skip_infer:
        result = step_generate_infer_signals(date, instruments="csi300", output=SIGNALS_LATEST_CSI300_OUTPUT, neutralize_industry=True, dry_run=args.dry_run)
        steps.append(result)
        if not result["success"]:
            print("\n[WARN] CSI 300 推断信号生成失败，但不阻塞流水线")
    else:
        print("\n[跳过] CSI 300 推断信号生成")

    # Step 2.55: Phase 1 因子增量更新 (扩展 bin 文件到最新日期)
    result = step_increment_p1_factors(date, dry_run=args.dry_run)
    steps.append(result)
    if not result["success"]:
        print("\n[WARN] P1 因子增量更新失败，但不阻塞流水线")

    # Step 2.6: Phase 1 因子共识信号 (v5.0 FactorFactory Bridge)
    result = step_generate_p1_signals(date, dry_run=args.dry_run)
    steps.append(result)
    if not result["success"]:
        print("\n[WARN] Phase 1 因子共识信号生成失败，但不阻塞流水线")
    else:
        healthcheck_p1_coverage()

    # Step 4: 同步到 AgentAlpha
    if not args.skip_sync:
        result = step_sync_to_fm_trade(date, dry_run=args.dry_run)
        steps.append(result)
    else:
        print("\n[跳过] AgentAlpha 同步")

    # 验证输出
    output = verify_output()

    total_elapsed = round(time.time() - total_start, 1)
    all_success = all(s["success"] for s in steps)

    summary = {
        "date": date,
        "status": "ok" if all_success else "partial",
        "total_elapsed": total_elapsed,
        "steps": steps,
        "output": output,
    }

    log_pipeline(summary)
    _print_summary(summary)

    sys.exit(0 if all_success else 1)


def _print_summary(summary: Dict):
    """打印执行汇总"""
    print(f"\n{'=' * 60}")
    print(f"流水线执行完成")
    print(f"{'=' * 60}")
    print(f"  状态: {summary['status']}")
    print(f"  总耗时: {summary['total_elapsed']}s")
    for s in summary.get("steps", []):
        status = "[OK]" if s["success"] else "[FAIL]"
        print(f"  {status} {s['step']}: {s['elapsed']}s")
    output = summary.get("output", {})
    if output.get("signals_exists"):
        print(f"\n  信号输出: {output['stock_count']} 只标的")
        print(f"  模型: {output['model']}")
        print(f"  预测日期: {output['date']}")
    if output.get("sector_signals_exists"):
        top5 = output.get("sector_top_5", [])
        if top5:
            print(f"  板块 Top-5: {', '.join(top5)}")


if __name__ == "__main__":
    main()