#!/usr/bin/env python3
"""
El Niño Monitoring Report Generator — 每周生成厄尔尼诺监控报告

输入:
  - data/output/enso_data.json (fetch_enso_data.py 生成)
  - data/output/el_nino_futures.json (fetch_el_nino_futures.py 生成)

输出: reports/el-nino/YYYY-MM-DD-el-nino-monitor.md

依赖: fetch_enso_data.py, fetch_el_nino_futures.py 均已运行
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "output"
REPORTS_DIR = PROJECT_ROOT / "reports" / "el-nino"
ENSO_FILE = DATA_DIR / "enso_data.json"
FUTURES_FILE = DATA_DIR / "el_nino_futures.json"


def load_json(path: Path) -> Optional[dict]:
    """Load JSON file, return None if missing."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError) as e:
        print(f"[WARN] Failed to load {path}: {e}")
        return None


def build_enso_section(enso: dict) -> str:
    """Build ENSO status section of report."""
    cls = enso.get("classification", {})
    sig = enso.get("trading_signals", {})
    exits = enso.get("exit_signals", {})

    status_emoji = {
        "super_el_nino": "🔴",
        "strong": "🟠",
        "moderate": "🟡",
        "weak": "🟢",
        "el_nino": "🟠",
        "neutral": "⚪",
        "la_nina": "🔵",
    }.get(cls.get("classification", ""), "⚪")

    lines = [
        "## 一、ENSO 状态",
        "",
        f"| 指标 | 数值 |",
        f"|---|---|",
        f"| 分类 | {status_emoji} **{cls.get('classification', 'N/A').upper()}** |",
        f"| 强度 | {cls.get('strength', 'N/A')} |",
        f"| 最新 ONI | **{cls.get('latest_oni', 'N/A')}°C** ({cls.get('latest_period', '')}) |",
        f"| 历史峰值 ONI | {cls.get('peak_oni', 'N/A')}°C ({cls.get('peak_period', '')}) |",
        f"| 趋势 | {cls.get('trend', 'N/A')} |",
        f"| 连续暖月数 | {cls.get('consecutive_warm_months', 0)} |",
        f"| 交易模式 | **{sig.get('mode', 'N/A').upper()}** |",
        f"| 数据时间 | {enso.get('metadata', {}).get('fetched_at', 'N/A')} |",
        "",
        "### 阈值参考",
        "",
        "| 等级 | ONI 阈值 |",
        "|---|---|",
    ]
    for level, val in enso.get("thresholds", {}).items():
        lines.append(f"| {level} | ≥ {val}°C |")

    lines.extend([
        "",
        "### 活跃预警",
        "",
    ])
    if sig.get("alerts"):
        for alert in sig["alerts"]:
            alert_desc = {
                "super_el_nino_active": "⚠️ 超强厄尔尼诺进行中 — 全部农产品策略执行",
                "still_intensifying": "📈 ONI 仍在上升 — 继续建仓/加仓窗口",
                "at_peak": "⏸️ ONI 到达峰值 — 持有不动，不加仓",
                "past_peak_decaying": "📉 ONI 已过峰值并下降 — 准备美债多头，监控退出信号",
                "oni_above_3.0_extreme": "🚨 ONI 超过 3.0°C 极端值 — 对标2015-16以上强度",
                "prolonged_event_over_12m": "⏰ 事件持续超过12个月",
                "strong_el_nino_active": "🟠 强厄尔尼诺进行中 — 农产品策略半仓执行",
            }.get(alert, alert)
            lines.append(f"- {alert_desc}")
    else:
        lines.append("- 无特殊预警")

    if exits.get("exit_reasons"):
        lines.extend([
            "",
            "### ⚠️ 退出信号",
            "",
        ])
        for reason in exits["exit_reasons"]:
            reason_desc = {
                "oni_below_1.0": "🚪 ONI 低于 1.0°C — 全局退出触发器",
                "oni_below_1.5": "⚠️ ONI 低于 1.5°C — 建议部分退出",
                "rapid_cooling_2m": "❄️ 连续 2 个月快速降温 — 加速退出",
            }.get(reason, reason)
            lines.append(f"- {reason_desc}")

    return "\n".join(lines)


def build_futures_section(futures: dict, signals: dict) -> str:
    """Build futures/commodity section of report."""
    metrics = futures.get("metrics", {})
    summary = futures.get("summary", {})

    lines = [
        "## 二、关键商品期货",
        "",
        "| 品种 | 最新价 | 月初至今 | 3个月 | 6个月 | 年化波动率 | 距52周高 | 信号 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for key, m in metrics.items():
        name = m.get("name_cn", key)
        price = m.get("latest_price", "N/A")
        ytd = f"{m.get('ret_ytd_pct', 'N/A')}%" if m.get("ret_ytd_pct") is not None else "N/A"
        r3m = f"{m.get('ret_3m_pct', 'N/A')}%" if m.get('ret_3m_pct') is not None else "N/A"
        r6m = f"{m.get('ret_6m_pct', 'N/A')}%" if m.get('ret_6m_pct') is not None else "N/A"
        vol = f"{m.get('vol_20d_annual_pct', 'N/A')}%" if m.get('vol_20d_annual_pct') is not None else "N/A"
        pct_high = f"{m.get('pct_from_52w_high', 'N/A')}%" if m.get('pct_from_52w_high') is not None else "N/A"

        sig = signals.get(key, {})
        signal_str = sig.get("signal", "N/A")
        signal_emoji = {
            "buy": "🟢 买入",
            "hold_add_on_dip": "🟢 持有/逢低加仓",
            "hold": "🟡 持有",
            "reduce": "🔴 减仓",
        }.get(signal_str, signal_str)

        lines.append(f"| {name} | {price} | {ytd} | {r3m} | {r6m} | {vol} | {pct_high} | {signal_emoji} |")

    lines.extend([
        "",
        f"**综合信号**: {summary.get('overall_signal', 'N/A').upper()}",
        f"**买入/持有信号数**: {summary.get('buy_signals', 0)} | **减仓信号数**: {summary.get('reduce_signals', 0)}",
        f"**活跃预警数**: {summary.get('active_alerts', 0)}",
        "",
    ])

    # Per-commodity alerts
    for key, sig in signals.items():
        if sig.get("alerts"):
            name = sig.get("name_cn", key)
            lines.append(f"**{name}**: " + " | ".join(sig["alerts"]))
            lines.append(f"  止损: {sig.get('stop_loss_pct', 'N/A')}% | 分批止盈: {sig.get('partial_take_profit_pct', 'N/A')}% / {sig.get('full_take_profit_pct', 'N/A')}%")

    return "\n".join(lines)


def build_strategy_section() -> str:
    """Build strategy status section."""
    return """## 三、交易策略状态

### 策略 A: 热带农产品多头 (置信度: ⭐⭐⭐⭐⭐)

| 参数 | 设定 |
|---|---|
| 品种配置 | 棕榈油 40% + 橡胶 30% + 白糖 30% |
| 仓位 | 总风险资本 20-30% |
| 止损 | -15% |
| 分批止盈 | +25% 平 1/3, +40% 平 1/3, 剩余跟踪止盈(20日均线) |
| 最大持仓期 | 18 个月 |

### 策略 B: A股农业上游轮动 (置信度: ⭐⭐⭐⭐)

| 参数 | 设定 |
|---|---|
| 组合 | 种业 30% / 白糖 25% / 橡胶 25% / 化肥 20% |
| 仓位 | 总组合 10-15% |
| 再平衡 | 月度 |
| 退出条件 | ONI < +1.0 + 北半球春季播种正常 (2027年4-5月) |

### 策略 G: 受损板块规避 (置信度: ⭐⭐⭐⭐)

| 规避板块 | 时间窗口 |
|---|---|
| 饲料（海大集团等） | 2026Q4 - 2027Q2 |
| 生猪养殖（牧原等） | 2026Q4 - 2027Q2 |
| 食品加工 | 2026Q4 - 2027Q2 |

### 其他策略

| 策略 | 置信度 | 状态 |
|---|---|---|
| C. 能源战术（先空后多） | ⭐⭐⭐ | 监控中 |
| D. 新兴市场减配 | ⭐⭐⭐ | 已激活 |
| E. 美债多头（事件中后期） | ⭐⭐⭐ | 监控中 |
| F. 黄金对冲 | ⭐⭐⭐ | 已激活 |
"""


def build_scenario_section(enso: dict) -> str:
    """Build risk scenario projection section."""
    cls = enso.get("classification", {})
    trend = cls.get("trend", "unknown")
    latest_oni = cls.get("latest_oni", 0)
    peak_oni = cls.get("peak_oni", 0)

    # Determine most likely scenario
    if latest_oni >= 3.0 and trend in ("warming_rapidly", "warming"):
        scenario = "**当前判断 → 超预期情景概率上升** (ONI 已超 3.0 且仍在上升)"
    elif latest_oni >= 2.5 and trend in ("stable", "warming"):
        scenario = "**当前判断 → 基准情景** (ONI 在 2.5-3.5 区间)"
    elif trend in ("cooling", "cooling_rapidly") and latest_oni < 2.0:
        scenario = "**当前判断 → 快速衰减情景** (ONI 已开始下降)"
    else:
        scenario = "**当前判断 → 基准情景 (默认)**"

    return f"""## 四、情景分析

{scenario}

| 情景 | 概率 | ONI 峰值 | 衰减时间 | 策略调整 |
|---|---|---|---|---|
| 🟢 基准 | 55% | 3.2-3.5°C | 2027春快速衰减 | 全部策略执行 |
| 🟡 超预期 | 25% | >3.5°C | 持续至2027秋 | 农产品仓位→40%，持仓延至24月 |
| 🟠 快速衰减 | 15% | <3.0°C | 2027Q1回落 | 减半农产品仓位，提前止盈 |
| 🔴 尾部风险 | 5% | 叠加地缘/金融 | 不适用 | 全部平仓→现金+黄金 |

### 退出条件

| 触发条件 | 行动 |
|---|---|
| ONI 连续 2 个月 < +1.0°C | **全局退出 — 平仓所有 El Niño 多头** |
| 东南亚/印度雨季降水恢复正常 | 减仓 50% |
| 2027年9月 | 全面评估，默认逐步退出 |
| 任一策略回撤 > 20% | 该策略止损退出 |
"""


def build_timeline_section() -> str:
    """Build expected impact timeline."""
    return """## 五、宏观影响时间线

| 时段 | 关键特征 | 交易含义 |
|---|---|---|
| **2026年秋冬季** | 厄尔尼诺达峰，通胀开始升温 | 农产品建仓窗口，糖价/菜价上涨 |
| **2027年春季** | 价格延续，经济影响较小 | 持有不动，关注产区播种 |
| **2027年夏季** | **通胀影响达峰**，洪涝冲击水稻 | 美债多头准备，受损板块影响最大 |
| **2027年秋季** | 事件消退，天气正常化 | 逐步退出 El Niño 策略 |
"""


def generate_report(force: bool = False) -> Path:
    """Generate the full monitoring report."""
    # Load data
    enso = load_json(ENSO_FILE)
    futures = load_json(FUTURES_FILE)

    if not enso:
        print("[ERROR] enso_data.json not found. Run fetch_enso_data.py first.")
        sys.exit(1)

    today = datetime.now().strftime("%Y-%m-%d")

    # Build report sections
    sections = [
        f"# 厄尔尼诺监控报告 — {today}",
        "",
        f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 数据来源: NOAA CPC (ONI/RONI) + akshare (期货)",
        f"> 策略参考: [超强厄尔尼诺交易策略](../reports/planning/el-nino-super-trading-strategy-2026-10-23.md)",
        "",
        "---",
        "",
        build_enso_section(enso),
        "",
        "---",
        "",
    ]

    if futures and futures.get("metrics"):
        sections.append(build_futures_section(futures, futures.get("signals", {})))
        sections.extend(["", "---", ""])

    sections.extend([
        build_strategy_section(),
        "",
        "---",
        "",
        build_scenario_section(enso),
        "",
        "---",
        "",
        build_timeline_section(),
        "",
        "---",
        "",
        "## 六、操作检查清单",
        "",
        "- [ ] ENSO 数据已更新（本周）",
        "- [ ] 期货数据已刷新（今日）",
        "- [ ] A股农业上游组合再平衡检查（月度）",
        "- [ ] 产区天气报告已查阅（东南亚/印度/澳洲）",
        "- [ ] 止损/止盈订单已检查",
        "- [ ] 情景概率已更新",
        "",
        "---",
        "",
        f"*本报告由 `scripts/generate_el_nino_report.py` 自动生成*",
    ])

    content = "\n".join(sections)

    # Write
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = REPORTS_DIR / f"{today}-el-nino-monitor.md"
    output_path.write_text(content, encoding="utf-8")
    print(f"[REPORT] Saved to {output_path}")

    return output_path


# --- CLI ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate El Niño monitoring report")
    parser.add_argument("--force", action="store_true", help="Force regeneration")
    parser.add_argument("--stdout", action="store_true", help="Print report to stdout instead of saving")
    args = parser.parse_args()

    if args.stdout:
        # Quick stdout mode — load data and print basic status
        enso = load_json(ENSO_FILE)
        if not enso:
            print("No ENSO data available. Run fetch_enso_data.py first.")
            sys.exit(1)

        cls = enso["classification"]
        sig = enso["trading_signals"]
        print(f"ENSO: {cls['classification']} | ONI: {cls['latest_oni']}°C | Trend: {cls['trend']}")
        print(f"Trading: {sig['mode']} | Active: {sig['active']}")

        futures = load_json(FUTURES_FILE)
        if futures and futures.get("summary"):
            print(f"Commodities: {futures['summary']['overall_signal']} | Buy signals: {futures['summary']['buy_signals']}")
    else:
        generate_report(force=args.force)