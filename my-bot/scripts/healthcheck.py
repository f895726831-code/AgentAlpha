"""
自动化健康检查 — 心跳检测 + 端到端验证

检查项:
  Layer 1 (心跳): startup-*.txt 是否有当日 STARTUP 条目
  Layer 2 (端到端): 报告文件是否存在 + 完整性标记 + 信号文件新鲜度

输出:
  alerts/daily/{today}.md — 告警报告（空=一切正常）
  alerts/healthcheck.log — 历史日志

用法:
  python scripts/healthcheck.py                    # 检查今天
  python scripts/healthcheck.py --date 2026-09-03  # 检查指定日期
  python scripts/healthcheck.py --quiet            # 仅输出告警，正常时不输出
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── 配置 ────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path("D:/AgentAlpha/my-bot")
LOG_DIR = PROJECT_ROOT / "logs/cron"
REPORT_DIR = PROJECT_ROOT / "reports/daily"
SIGNAL_FILE = PROJECT_ROOT / "data/output/alpha_signals.json"
ALERT_DIR = PROJECT_ROOT / "alerts"
HEALTH_LOG = ALERT_DIR / "healthcheck.log"

# 任务定义：(名称, startup文件, 预期触发时间, 报告文件模板, 完成标记)
TASKS = [
    {
        "name": "MorningBrief",
        "startup_file": "startup-morning.txt",
        "scheduled_hour": 8,
        "scheduled_minute": 35,
        "expected_hour": 8,
        "report_template": "{date}-morning-brief.md",
        "completion_marker": "MORNING_BRIEF_COMPLETE",
        "max_duration_min": 15,
    },
    {
        "name": "MiddayReview",
        "startup_file": "startup-midday.txt",
        "scheduled_hour": 11,
        "scheduled_minute": 30,
        "expected_hour": 11,
        "report_template": "{date}-midday.md",
        "completion_marker": "MIDDAY_COMPLETE",
        "max_duration_min": 15,
    },
    {
        "name": "SignalFactory",
        "startup_file": "signal-factory-pipeline.log",
        "scheduled_hour": 16,
        "scheduled_minute": 0,
        "expected_hour": 16,
        "report_template": None,  # SignalFactory outputs alpha_signals.json, not a report
        "completion_marker": None,
        "max_duration_min": 120,
    },
    {
        "name": "DailyScan",
        "startup_file": "startup-daily.txt",
        "scheduled_hour": 17,
        "scheduled_minute": 30,
        "expected_hour": 17,
        "report_template": "{date}-watchlist-scan.md",
        "completion_marker": "SCAN_COMPLETE",
        "max_duration_min": 60,
    },
]

# TiMi 相关文件路径
TIMI_STRATEGY_DIR = PROJECT_ROOT / "data/strategies"
TIMI_SIGNALS_FILE = PROJECT_ROOT / "data/output/timi_signals.json"
TIMI_MIDDAY_FILE = PROJECT_ROOT / "data/output/timi_signals_midday.json"

# OOS 权重状态文件 (P2-12)
OOS_STATE_DIR = Path("D:/AgentAlpha/signal-factory/data/output")
OOS_STATE_TEMPLATE = "oos_weight_state_{instruments}.json"
OOS_FLOOR_ALERT_WEIGHT = 0.24  # 低于此值告警（等权0.333 × floor 0.3 = 0.10 太极端，用0.24 -> 被降权超过28%）
OOS_CSI300_GRACE_DAYS = 14  # CSI300 宽限期（P1-8 部署后14天内不告警缺失）


# ─── TiMi 健康检查 ──────────────────────────────────────────────────────────

def check_timi_strategy(target_date: str) -> dict:
    """检查 TiMi 策略文件是否存在且新鲜"""
    strategy_path = TIMI_STRATEGY_DIR / f"{target_date}.json"

    if not strategy_path.exists():
        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        yesterday_path = TIMI_STRATEGY_DIR / f"{yesterday}.json"
        if yesterday_path.exists():
            y_mtime = datetime.fromtimestamp(yesterday_path.stat().st_mtime)
            return {
                "ok": True,
                "severity": "warning",
                "detail": f"今日策略未编译，使用昨日 ({yesterday}) 策略 ({y_mtime.strftime('%H:%M')})",
            }
        return {
            "ok": False,
            "severity": "critical",
            "detail": f"策略文件不存在 ({target_date})，且昨日也缺失",
        }

    try:
        with open(strategy_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        mtime = datetime.fromtimestamp(strategy_path.stat().st_mtime)
        mtime_str = mtime.strftime("%H:%M")
        source = data.get("meta", {}).get("compiled_by", "unknown")
        regime = data.get("regime", "?")
        active_count = sum(1 for s in data.get("strategies", {}).values() if s.get("active"))

        return {
            "ok": True,
            "severity": "ok",
            "detail": f"已编译 ({mtime_str}, source={source}, regime={regime}, active={active_count})",
        }
    except Exception as e:
        return {
            "ok": False,
            "severity": "warning",
            "detail": f"策略文件存在但无法解析: {e}",
        }


def check_timi_signals(signals_path: Path, label: str = "TiMi") -> dict:
    """检查 TiMi 信号文件是否存在且新鲜"""
    if not signals_path.exists():
        return {
            "ok": True,
            "severity": "ok",
            "detail": f"无{label}信号文件（今日未触发或 TiMi 未运行）",
        }

    try:
        with open(signals_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        signal_count = len(data.get("signals", []))
        position_count = len(data.get("positions", []))
        source = data.get("source", "unknown")
        success = data.get("success", True)

        mtime = datetime.fromtimestamp(signals_path.stat().st_mtime)
        mtime_str = mtime.strftime("%H:%M")

        if not success:
            return {
                "ok": True,
                "severity": "warning",
                "detail": f"{label} 运行未成功 (source={source}, {mtime_str})",
            }

        if signal_count == 0 and source == "timi":
            return {
                "ok": True,
                "severity": "ok",
                "detail": f"{label} 无信号产出 (正常: risk_off 或 无触发, {mtime_str})",
            }

        return {
            "ok": True,
            "severity": "ok",
            "detail": f"{label}: {signal_count} 信号, {position_count} 仓位 ({mtime_str})",
        }
    except Exception as e:
        return {
            "ok": False,
            "severity": "warning",
            "detail": f"{label} 信号文件无法解析: {e}",
        }


# ─── 工具函数 ──────────────────────────────────────────────────────────────

def check_oos_weights(target_date: str) -> dict:
    """检查 OOS 权重状态 (P2-12)

    检查项:
      1. CSI500 状态文件存在且有有效数据
      2. CSI300 状态文件存在（宽限期内不告警）
      3. 是否有模型被降至接近 floor（持续低 IC 告警）

    使用 OOSWeightGuard 获取准确归一化权重，不做近似计算。
    """
    issues = []
    details = []

    # 尝试导入 OOSWeightGuard（需要 QSF 在 sys.path）
    try:
        qsf_root = str(Path("D:/AgentAlpha/signal-factory"))
        if qsf_root not in sys.path:
            sys.path.insert(0, qsf_root)
        from src.factors.oos_weight_guard import get_guard
        guard = get_guard()
    except ImportError as e:
        return {
            "ok": True,
            "severity": "ok",
            "detail": f"无法导入 OOSWeightGuard ({e})，跳过 OOS 权重检查",
        }

    for instruments in ["csi500", "csi300"]:
        state_path = OOS_STATE_DIR / OOS_STATE_TEMPLATE.format(instruments=instruments)

        if not state_path.exists():
            if instruments == "csi300":
                deploy_date = datetime(2026, 10, 30)
                grace_end = deploy_date + timedelta(days=OOS_CSI300_GRACE_DAYS)
                target_dt = datetime.strptime(target_date, "%Y-%m-%d")
                if target_dt < grace_end:
                    days_left = (grace_end - target_dt).days
                    details.append(f"csi300: 状态文件缺失（宽限期内，剩余 {days_left} 天）")
                    continue
            issues.append(f"csi300: OOS 状态文件缺失（已过宽限期）")
            continue

        summary = guard.get_summary(instruments)
        models = summary.get("models", {})
        if not models:
            details.append(f"{instruments}: 无模型 IC 记录（等待下次重训）")
            continue

        equal_w = 1.0 / len(models) if models else 0.333
        floor_w = equal_w * 0.30

        for name, info in models.items():
            ic = info.get("rolling_ic")
            weight = info.get("weight", equal_w)
            records = info.get("records", 0)

            if ic is None:
                details.append(f"{instruments}/{name}: 无 IC 数据（{records} 条记录）")
                continue

            # 告警条件：归一化权重低于等权的 70%（即被降权超过 30%）
            # 等权=0.333，70%=0.233。CatBoost 当前 0.229 刚好触发。
            if weight < equal_w * 0.70:
                issues.append(
                    f"{instruments}/{name}: IC={ic:.3f}, "
                    f"权重={weight:.3f} (降权 {(1 - weight / equal_w) * 100:.0f}%, "
                    f"{records} 条记录)"
                )
            else:
                details.append(
                    f"{instruments}/{name}: IC={ic:.3f}, "
                    f"权重={weight:.3f} ({records} 条记录)"
                )

    if issues:
        return {
            "ok": False,
            "severity": "warning",
            "detail": "; ".join(issues),
        }
    elif details:
        return {
            "ok": True,
            "severity": "ok",
            "detail": "; ".join(details),
        }
    return {
        "ok": True,
        "severity": "ok",
        "detail": "OOS 权重状态正常（CSI500+CSI300 均有记录）",
    }


def tz_offset() -> str:
    """获取本地时区偏移 (如 +08:00)"""
    now = datetime.now()
    utc_offset = now.astimezone().utcoffset()
    if utc_offset is None:
        return "+08:00"
    hours = utc_offset.total_seconds() / 3600
    return f"{'+' if hours >= 0 else ''}{int(hours):02d}:00"


def parse_startup_time(entry: str) -> datetime | None:
    """从 STARTUP 行提取时间戳"""
    m = re.search(r"STARTUP\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)", entry)
    if m:
        ts = m.group(1)
        try:
            return datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return None


def check_startup_file(
    file_path: Path, target_date: str, task_name: str,
    expected_hour: int | None = None,
) -> dict:
    """检查 startup 文件是否有当日条目"""
    if not file_path.exists():
        return {
            "ok": False,
            "severity": "critical",
            "detail": f"{file_path.name} 不存在",
        }

    try:
        content = file_path.read_text(encoding="utf-8-sig")
    except Exception:
        content = file_path.read_text(encoding="utf-8", errors="replace")

    # 查找当日 STARTUP 条目
    target_prefix = f"STARTUP {target_date}T"
    today_entries = [line for line in content.split("\n") if line.startswith(target_prefix)]

    if not today_entries:
        return {
            "ok": False,
            "severity": "critical",
            "detail": f"当日无 STARTUP 条目 ({target_date})",
        }

    # 如果指定了预期小时，过滤该小时附近的条目
    if expected_hour is not None:
        nearby = []
        for entry in today_entries:
            t = parse_startup_time(entry)
            if t and abs(t.hour - expected_hour) <= 1:
                nearby.append(entry)
        if not nearby:
            # 有当日条目但不在预期时间范围内 → 可能是其他任务触发的
            latest_time = None
            for entry in today_entries:
                t = parse_startup_time(entry)
                if t and (latest_time is None or t > latest_time):
                    latest_time = t
            return {
                "ok": False,
                "severity": "warning",
                "detail": f"当日有 {len(today_entries)} 个 STARTUP 但不在预期时段 (预期 ~{expected_hour}:00, 最晚 {latest_time.strftime('%H:%M') if latest_time else '?'})",
            }
        today_entries = nearby

    # 提取最晚的启动时间
    latest_time = None
    for entry in today_entries:
        t = parse_startup_time(entry)
        if t and (latest_time is None or t > latest_time):
            latest_time = t

    return {
        "ok": True,
        "severity": "ok",
        "detail": f"已触发 ({len(today_entries)} 次, 最晚 {latest_time.strftime('%H:%M:%S') if latest_time else '?'})",
    }


def check_report_file(
    file_path: Path, completion_marker: str | None, task_name: str
) -> dict:
    """检查报告文件是否存在且完整"""
    if not file_path.exists():
        return {
            "ok": False,
            "severity": "critical",
            "detail": f"报告文件不存在: {file_path.name}",
        }

    if completion_marker:
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if completion_marker in content:
                return {
                    "ok": True,
                    "severity": "ok",
                    "detail": f"报告已生成 (含 {completion_marker})",
                }
            else:
                return {
                    "ok": False,
                    "severity": "warning",
                    "detail": f"报告已生成但缺少完成标记 ({completion_marker})",
                }
        except Exception as e:
            return {
                "ok": False,
                "severity": "warning",
                "detail": f"无法读取报告: {e}",
            }

    return {"ok": True, "severity": "ok", "detail": "报告已生成"}


def check_signal_file(
    file_path: Path, target_date: str, task_name: str
) -> dict:
    """检查信号文件是否新鲜（当日生成）"""
    if not file_path.exists():
        return {
            "ok": False,
            "severity": "critical",
            "detail": "alpha_signals.json 不存在",
        }

    mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
    mtime_str = mtime.strftime("%Y-%m-%d %H:%M:%S")

    # 检查文件日期是否匹配
    if mtime.strftime("%Y-%m-%d") != target_date:
        return {
            "ok": False,
            "severity": "warning",
            "detail": f"alpha_signals.json 最后更新于 {mtime_str} (非当日)",
        }

    # 检查内容完整性
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        stock_count = data.get("total_stocks", 0)
        if stock_count == 0:
            return {
                "ok": False,
                "severity": "warning",
                "detail": f"alpha_signals.json 包含 0 只标的",
            }
        return {
            "ok": True,
            "severity": "ok",
            "detail": f"信号已更新 ({stock_count} 只标的, {mtime_str})",
        }
    except Exception as e:
        return {
            "ok": False,
            "severity": "warning",
            "detail": f"无法解析 alpha_signals.json: {e}",
        }


def check_scheduled_time(task: dict, target_date: str) -> dict:
    """检查是否已过预定触发时间"""
    scheduled = datetime.strptime(
        f"{target_date}T{task['scheduled_hour']:02d}:{task['scheduled_minute']:02d}:00",
        "%Y-%m-%dT%H:%M:%S",
    )
    now = datetime.now()

    if now < scheduled:
        return {
            "ok": True,
            "severity": "pending",
            "detail": f"尚未到触发时间 ({task['scheduled_hour']:02d}:{task['scheduled_minute']:02d})",
        }

    # 已过预定时间 + 最大耗时 → 应该已完成
    deadline = scheduled + timedelta(minutes=task["max_duration_min"])
    if now > deadline:
        return {
            "ok": True,
            "severity": "overdue",
            "detail": f"已过截止时间 (预定 {task['scheduled_hour']:02d}:{task['scheduled_minute']:02d}, 截止 {deadline.strftime('%H:%M')})",
        }

    return {
        "ok": True,
        "severity": "ok",
        "detail": f"运行中 (预定 {task['scheduled_hour']:02d}:{task['scheduled_minute']:02d})",
    }


# ─── 主逻辑 ────────────────────────────────────────────────────────────────

def run_healthcheck(target_date: str, quiet: bool = False) -> list[dict]:
    """运行所有健康检查，返回告警列表"""
    alerts = []
    now = datetime.now()

    for task in TASKS:
        task_alerts = []

        # 1. 时间检查 — 如果还没到预定时间，跳过后续检查
        time_check = check_scheduled_time(task, target_date)
        if time_check["severity"] == "pending":
            if not quiet:
                print(f"  ⏳ {task['name']}: {time_check['detail']}")
            continue

        # 2. 心跳检查
        startup_path = LOG_DIR / task["startup_file"]
        heartbeat = check_startup_file(
            startup_path, target_date, task["name"],
            expected_hour=task.get("expected_hour"),
        )

        # 3. 端到端检查
        e2e = None
        signal_check = None
        if task["report_template"]:
            report_path = REPORT_DIR / task["report_template"].format(date=target_date)
            e2e = check_report_file(report_path, task["completion_marker"], task["name"])
        elif task["name"] == "SignalFactory":
            signal_check = check_signal_file(SIGNAL_FILE, target_date, task["name"])

        # 汇总
        checks = [heartbeat]
        if e2e:
            checks.append(e2e)
        if signal_check:
            checks.append(signal_check)

        failed = [c for c in checks if not c["ok"]]
        has_critical = any(c["severity"] == "critical" for c in failed)

        if has_critical:
            level = "CRITICAL"
        elif failed:
            level = "WARNING"
        else:
            level = "OK"

        if not quiet or level != "OK":
            task_alerts.append({
                "task": task["name"],
                "level": level,
                "time_check": time_check,
                "heartbeat": heartbeat,
                "e2e": e2e,
                "signal_check": signal_check,
            })
            alerts.extend(task_alerts)

    # ── TiMi 健康检查 ──
    timi_strategy = check_timi_strategy(target_date)
    timi_daily_signals = check_timi_signals(TIMI_SIGNALS_FILE, "TiMi Daily")
    timi_midday_signals = check_timi_signals(TIMI_MIDDAY_FILE, "TiMi Midday")

    timi_checks = [timi_strategy, timi_daily_signals, timi_midday_signals]
    timi_failed = [c for c in timi_checks if not c["ok"]]

    if timi_failed:
        level = "WARNING"
    else:
        level = "OK"

    if not quiet or level != "OK":
        alerts.append({
            "task": "TiMi",
            "level": level,
            "timi_strategy": timi_strategy,
            "timi_daily_signals": timi_daily_signals,
            "timi_midday_signals": timi_midday_signals,
        })

    # ── OOS 权重检查 (P2-12) ──
    oos_check = check_oos_weights(target_date)
    if oos_check["severity"] != "ok":
        alerts.append({
            "task": "OOS-Weight",
            "level": "WARNING",
            "oos_check": oos_check,
        })
    elif not quiet:
        print(f"  ✅ OOS-Weight: {oos_check['detail']}")

    return alerts


def _alert_detail(a: dict) -> str:
    """从告警 dict 提取摘要详情，兼容 TiMi 和非 TiMi 告警结构。"""
    if a.get("task") == "TiMi":
        # TiMi 告警有 timi_strategy / timi_daily_signals / timi_midday_signals 字段
        parts = []
        for key, label in [
            ("timi_strategy", "策略"),
            ("timi_daily_signals", "日线"),
            ("timi_midday_signals", "午间"),
        ]:
            item = a.get(key, {})
            if item:
                parts.append(f"{label}: {item.get('detail', '?')}")
        return "; ".join(parts) if parts else "未知"
    if a.get("task") == "OOS-Weight":
        oos = a.get("oos_check", {})
        return oos.get("detail", "OOS权重检查失败")
    # 常规告警
    return a.get("heartbeat", {}).get("detail",
           a.get("e2e", {}).get("detail", "-"))


def format_report(target_date: str, alerts: list[dict]) -> str:
    """格式化告警报告为 Markdown"""
    if not alerts:
        return ""

    lines = [
        f"# 健康检查告警 — {target_date}",
        f"",
        f"检查时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"",
        f"## 告警摘要",
        f"",
    ]

    critical = [a for a in alerts if a["level"] == "CRITICAL"]
    warnings = [a for a in alerts if a["level"] == "WARNING"]

    if critical:
        lines.append(f"### 🔴 Critical ({len(critical)} 项)")
        for a in critical:
            detail = _alert_detail(a)
            lines.append(f"- **{a['task']}**: {detail}")
        lines.append("")

    if warnings:
        lines.append(f"### 🟡 Warning ({len(warnings)} 项)")
        for a in warnings:
            detail = _alert_detail(a)
            lines.append(f"- **{a['task']}**: {detail}")
        lines.append("")

    lines.append("## 详细检查")
    lines.append("")
    lines.append("| 任务 | 级别 | 心跳 | 端到端 |")
    lines.append("|---|---|---|---|")
    for a in alerts:
        if a["task"] == "TiMi":
            # TiMi 告警有不同结构
            ts = a.get("timi_strategy", {})
            td = a.get("timi_daily_signals", {})
            tm = a.get("timi_midday_signals", {})
            lines.append(f"| {a['task']} | {a['level']} | 策略: {ts.get('detail','-')} | "
                        f"日线: {td.get('detail','-')} / 午间: {tm.get('detail','-')} |")
        elif a["task"] == "OOS-Weight":
            oos = a.get("oos_check", {})
            lines.append(f"| {a['task']} | {a['level']} | {oos.get('detail','-')} | — |")
        else:
            hb = a["heartbeat"]["detail"]
            e2e_detail = a.get("e2e", {}).get("detail", "") if a.get("e2e") else ""
            sig_detail = a.get("signal_check", {}).get("detail", "") if a.get("signal_check") else ""
            e2e_str = e2e_detail or sig_detail or "-"
            lines.append(f"| {a['task']} | {a['level']} | {hb} | {e2e_str} |")

    lines.append("")
    lines.append("---")
    lines.append("> 自动化健康检查 | Phase 1.2 | 每日 17:45 触发")

    return "\n".join(lines)


def log_to_history(target_date: str, alerts: list[dict]):
    """追加历史日志"""
    HEALTH_LOG.parent.mkdir(parents=True, exist_ok=True)

    critical_count = sum(1 for a in alerts if a["level"] == "CRITICAL")
    warning_count = sum(1 for a in alerts if a["level"] == "WARNING")

    entry = {
        "timestamp": datetime.now().isoformat(),
        "date": target_date,
        "status": "CRITICAL" if critical_count > 0 else ("WARNING" if warning_count > 0 else "OK"),
        "critical": critical_count,
        "warning": warning_count,
        "total_alerts": len(alerts),
    }

    with open(HEALTH_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _check_evolution_signal(alerts: list[dict], target_date: str):
    """当健康检查发现 CRITICAL/WARNING 时，自动写入 evolution capture。

    不直接调用 evolution_capture.py（避免循环依赖），而是写入一个
    待处理文件，由下一次 cron 后置钩子处理。
    """
    critical_tasks = [a for a in alerts if a.get("level") == "CRITICAL"]
    warning_tasks = [a for a in alerts if a.get("level") == "WARNING"]

    if not critical_tasks and not warning_tasks:
        return

    evolution_dir = PROJECT_ROOT / "data/evolution"
    pending_file = evolution_dir / "pending_captures.json"

    # Load existing pending
    pending = []
    if pending_file.exists():
        try:
            pending = json.loads(pending_file.read_text(encoding="utf-8"))
        except Exception:
            pending = []

    for a in critical_tasks:
        detail = _alert_detail(a)
        pending.append({
            "track": "internal",
            "source": f"Healthcheck: {a['task']} CRITICAL",
            "title": f"管线 CRITICAL: {a['task']} — {detail[:80]}",
            "description": f"健康检查 {target_date} 发现 {a['task']} 处于 CRITICAL 状态: {detail}",
            "severity": "P0",
            "affected_component": f"pipeline.{a['task']}",
            "suggested_action": "立即排查 CRITICAL 告警根因",
            "captured_at": datetime.now().isoformat(),
        })

    for a in warning_tasks:
        detail = _alert_detail(a)
        pending.append({
            "track": "internal",
            "source": f"Healthcheck: {a['task']} WARNING",
            "title": f"管线 DEGRADED: {a['task']} — {detail[:80]}",
            "description": f"健康检查 {target_date} 发现 {a['task']} 处于 WARNING 状态: {detail}",
            "severity": "P2",
            "affected_component": f"pipeline.{a['task']}",
            "suggested_action": "评估是否需要修复或降级处理",
            "captured_at": datetime.now().isoformat(),
        })

    evolution_dir.mkdir(parents=True, exist_ok=True)
    pending_file.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="自动化健康检查")
    parser.add_argument("--date", help="检查日期 (YYYY-MM-DD)，默认今天")
    parser.add_argument("--quiet", action="store_true", help="仅输出告警")
    args = parser.parse_args()

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    if not args.quiet:
        print(f"健康检查: {target_date} @ {datetime.now().strftime('%H:%M:%S')}")
        print(f"{'=' * 50}")

    alerts = run_healthcheck(target_date, quiet=args.quiet)

    # ★ Evolution hook: CRITICAL/WARNING 自动写入 pending_captures
    _check_evolution_signal(alerts, target_date)

    # 输出告警到控制台
    if alerts:
        for a in alerts:
            icon = "🔴" if a["level"] == "CRITICAL" else ("🟡" if a["level"] == "WARNING" else "✅")
            detail = _alert_detail(a)
            print(f"{icon} {a['task']}: {detail}")

    # 写入告警报告
    report = format_report(target_date, alerts)
    alert_file = ALERT_DIR / "daily" / f"{target_date}.md"

    if report:
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        alert_file.write_text(report, encoding="utf-8")
        if not args.quiet:
            print(f"\n告警报告: {alert_file}")
    else:
        # 没有告警时也创建一个空标记文件（方便检测"健康检查已运行"）
        alert_file.parent.mkdir(parents=True, exist_ok=True)
        alert_file.write_text(
            f"# 健康检查 — {target_date}\n\n✅ 所有任务正常\n\n检查时间: {datetime.now().strftime('%H:%M:%S')}\n",
            encoding="utf-8",
        )
        if not args.quiet:
            print("✅ 所有任务正常")

    # 历史日志
    log_to_history(target_date, alerts)

    # 返回退出码
    critical_count = sum(1 for a in alerts if a["level"] == "CRITICAL")
    sys.exit(1 if critical_count > 0 else 0)


if __name__ == "__main__":
    main()