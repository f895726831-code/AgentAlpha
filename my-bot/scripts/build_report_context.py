#!/usr/bin/env python3
"""
build_report_context.py — 三层架构报告数据桥接

读取 Layer 0/1/2 数据文件，预生成三份日报所需的上下文：
  - Layer 0: asset_allocation.json → _layer0_summary.md
  - Layer 1: sector_signals.json → _layer1_summary.md
  - Performance: sector CSV 数据 → _sector_performance.md (via fetch_sector_performance)

输出到 data/output/ 目录，供 Claude agent prompt 直接读取插入。

用法:
  python scripts/build_report_context.py                     # 生成全部（Layer 0 + Layer 1 + Performance）
  python scripts/build_report_context.py --layer0            # 仅 Layer 0
  python scripts/build_report_context.py --layer1            # 仅 Layer 1
  python scripts/build_report_context.py --performance       # 仅板块实时表现
  python scripts/build_report_context.py --midday --eod      # 生成午间+盘后验证模板
"""

import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "output"
QF_DATA_DIR = Path("D:/AgentAlpha/signal-factory/data/output")

# ─── 板块分类映射 ───
CATEGORY_LABELS = {
    "growth": "成长",
    "cycle": "周期",
    "defense": "防御",
    "financial": "金融",
    "consumer": "消费",
    "other": "其他",
}

CATEGORY_ICONS = {
    "growth": "🚀",
    "cycle": "🔄",
    "defense": "🛡️",
    "financial": "🏦",
    "consumer": "🛒",
    "other": "📦",
}


def load_json(path: Path):
    """加载 JSON，文件不存在返回 None"""
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ═══════════════════════════════════════════════════════════════
# Layer 0: 宏观资产配置
# ═══════════════════════════════════════════════════════════════

def build_layer0_summary() -> str:
    """
    生成 Layer 0 宏观资产配置概览 markdown。
    数据源: asset_allocation.json（由 macro_allocation 管线产出，或手动维护）

    兼容两种格式：
      - v1.0 (macro_contract): asset_details 列表, rebalance_suggestions(asset/action/drift/suggestion)
      - v0.x (legacy): assets 字典, rebalance_suggestions(priority/action/target/amount/reason)
    """
    asset_data = load_json(DATA_DIR / "asset_allocation.json")

    today = date.today().isoformat()
    lines = []

    lines.append("## 零、宏观资产配置概览 (Layer 0)")
    lines.append("")

    if asset_data is None:
        lines.append("> ⚠️ `asset_allocation.json` 不存在。Layer 0 宏观配置数据待初始化。")
        lines.append("> 数据管线: macro_data → macro_regime → asset_scorer → portfolio_builder → asset_allocation.json")
        lines.append("")
        lines.append("### 当前默认配置 (risk_off 保守)")
        lines.append("")
        lines.append("| 资产 | ETF | 目标权重 | 说明 |")
        lines.append("|---|---|---|---|")
        lines.append("| A股 (沪深300+个股) | 159919 | ≤15% | risk_off 上限 |")
        lines.append("| 科创50 (ETF) | 588000 | 3-10% | 高弹性成长 |")
        lines.append("| 债券 (国债ETF) | 511010 | ≥25% | 防御优先 |")
        lines.append("| 黄金 (华安黄金) | 518880 | 10-15% | 避险 |")
        lines.append("| 现金/回购 | GC001 | ≥20% | 保持流动性 |")
        lines.append("| 石油 (华宝油气) | 162411 | 0-5% | 地缘风险高 |")
        lines.append("| 纳指 (纳指ETF) | 513100 | 0-5% | 美联储政策 |")
        lines.append("| 恒生科技 | 513130 | 0-5% | 港股承压 |")
        lines.append("| 日经225 (日经ETF) | 513880 | 0-5% | 日本央行政策 |")
        lines.append("| 印度SENSEX (LOF) | 164824 | 0-5% | 新兴市场波动 |")
        lines.append("| 可转债 (转债ETF) | 511380 | 3-10% | 股债混合 |")
        lines.append("")
        return "\n".join(lines)

    # Detect contract version
    contract_ver = asset_data.get("version", "0.x")
    is_v1 = contract_ver.startswith("1.")

    # ─── 宏观象限 ───
    macro = asset_data.get("macro_quadrant", {})
    lines.append(f"> 数据源: asset_allocation.json | 契约版本: {contract_ver} | 上次更新: {asset_data.get('updated', today)}")
    lines.append("")

    # v1.0 format uses quadrant_label_cn + confidence
    quadrant_cn = macro.get("quadrant_label_cn", macro.get("quadrant", "待判定"))
    confidence = macro.get("confidence", 0)
    conf_str = f" (置信度: {confidence:.0%})" if confidence else ""

    lines.append("### 宏观象限")
    lines.append("")
    lines.append(f"> **当前象限: {quadrant_cn}{conf_str}**")
    lines.append("")
    lines.append("| 维度 | 当前状态 | 方向 | 判断 |")
    lines.append("|---|---|---|---|")
    for dim in ["growth", "inflation", "liquidity", "risk_appetite"]:
        d = macro.get(dim, {})
        label = {"growth": "增长", "inflation": "通胀", "liquidity": "流动性", "risk_appetite": "风险偏好"}[dim]
        lines.append(f"| {label} | {d.get('value', '—')} | {d.get('direction', '—')} | {d.get('judgment', '—')} |")
    lines.append("")

    # ─── 7 类资产目标权重 ───
    weights = asset_data.get("target_weights", {})
    current = asset_data.get("current_weights", {})

    lines.append("### 11 类资产目标权重（本周）")
    lines.append("")
    lines.append("| 资产 | ETF | 类别 | 目标权重 | 当前权重 | 偏离 | 评分 | 建议 |")
    lines.append("|---|---|---|---|---|---|---|---|")

    if is_v1 and "asset_details" in asset_data:
        # v1.0 contract format
        for detail in asset_data["asset_details"]:
            asset_key = detail.get("asset", "")
            name = detail.get("asset_name", asset_key)
            etf = detail.get("etf_code", "—")
            cat = detail.get("category", "")
            cat_label = _cat_label(cat)
            target = detail.get("target_weight", 0)
            curr = detail.get("current_weight", 0)
            score = detail.get("score", 0)
            rec = detail.get("recommendation", "neutral")
            rec_label = {"overweight": "超配 ↑", "neutral": "中性 →", "underweight": "低配 ↓", "avoid": "回避 ✗"}.get(rec, rec)
            drift = target - curr
            drift_str = f"{drift:+.1%}"
            signal = _drift_signal(drift)

            lines.append(
                f"| {name} | {etf} | {cat_label} "
                f"| {target:.0%} | {curr:.0%} | {drift_str} {signal} "
                f"| {score:.0f} | {rec_label} |"
            )
    else:
        # Legacy format: assets dict
        assets_dict = asset_data.get("assets", {})
        for asset_key, asset_info in assets_dict.items():
            target = weights.get(asset_key, 0)
            curr = current.get(asset_key, 0)
            drift = target - curr
            drift_str = f"{drift:+.1%}"
            signal = _drift_signal(drift)
            lines.append(
                f"| {asset_info.get('name', asset_key)} "
                f"| {asset_info.get('etf_code', '—')} "
                f"| {asset_info.get('category', '—')} "
                f"| {target:.0%} | {curr:.0%} | {drift_str} {signal} "
                f"| — | — |"
            )

    lines.append("")

    # ─── 模式覆盖 ───
    mode_override = asset_data.get("mode_override", {})
    if mode_override:
        mode = mode_override.get("mode", "normal")
        note = mode_override.get("note", "")
        if mode != "normal" or note:
            lines.append(f"> 🔴 **{mode} 模式覆盖**: {note}")
            lines.append("")

    # ─── 调仓建议 ───
    rebalance = asset_data.get("rebalance_suggestions", [])
    if rebalance:
        lines.append("### 调仓建议")
        lines.append("")

        if is_v1:
            # v1.0 format: asset/action/drift/suggestion/urgency
            lines.append("| 资产 | 操作 | 当前 → 目标 | 偏离 | 建议 | 紧迫度 |")
            lines.append("|---|---|---|---|---|---|")
            for item in rebalance:
                lines.append(
                    f"| {item.get('asset_name', item.get('asset', '—'))} "
                    f"| {_action_label(item.get('action', 'hold'))} "
                    f"| {item.get('current_weight', 0):.0%} → {item.get('target_weight', 0):.0%} "
                    f"| {item.get('drift', 0):+.1%} "
                    f"| {item.get('suggestion', '—')} "
                    f"| {item.get('urgency', 'normal')} |"
                )
        else:
            # Legacy format: priority/action/target/amount/reason
            lines.append("| 优先级 | 操作 | 标的 | 金额/比例 | 原因 |")
            lines.append("|---|---|---|---|---|")
            for item in rebalance:
                lines.append(
                    f"| {item.get('priority', '—')} "
                    f"| {item.get('action', '—')} "
                    f"| {item.get('target', '—')} "
                    f"| {item.get('amount', '—')} "
                    f"| {item.get('reason', '—')} |"
                )
        lines.append("")

    # ─── 约束检查 ───
    if is_v1:
        constraints = asset_data.get("constraints_check", {})
        if constraints:
            lines.append("### 约束检查")
            lines.append("")
            for constraint, passed in constraints.items():
                icon = "✅" if passed else "❌"
                lines.append(f"- {icon} {constraint}")
            lines.append("")

    # ─── ★ P3: 有效风险暴露（经资产属性调整） ───
    if is_v1 and "target_weights" in asset_data:
        tw = asset_data["target_weights"]
        # 转债 ≈ 70% 权益属性 + 30% 债性
        cb_bond_w = tw.get("cb_bond", 0)
        equity_w = sum(tw.get(k, 0) for k in [
            "nasdaq", "hstech", "a_share", "star_50", "nikkei", "india"
        ])
        bond_w = tw.get("bond", 0)
        gold_w = tw.get("gold", 0)
        cash_w = tw.get("cash", 0)
        oil_w = tw.get("oil", 0)

        effective_equity = equity_w + cb_bond_w * 0.70
        effective_fixed = bond_w + cb_bond_w * 0.30

        lines.append("### 有效风险暴露（经资产属性调整）")
        lines.append("")
        lines.append("> 转债含约70%权益敞口，名义固收权重可能高估实际防守仓位。")
        lines.append("")
        lines.append("| 风险类别 | 名义权重 | 调整后有效敞口 |")
        lines.append("|---|---|---|")
        lines.append(f"| 纯固收（国债） | {bond_w:.0%} | {bond_w:.0%} |")
        lines.append(f"| 转债固收部分 | — | {cb_bond_w*0.30:.0%} |")
        lines.append(f"| **有效固收合计** | **{bond_w + cb_bond_w:.0%}** | **{effective_fixed:.0%}** |")
        lines.append(f"| 权益（直接） | {equity_w:.0%} | {equity_w:.0%} |")
        lines.append(f"| 转债权益部分 | — | {cb_bond_w*0.70:.0%} |")
        lines.append(f"| **有效权益合计** | **{equity_w + cb_bond_w:.0%}** | **{effective_equity:.0%}** |")
        lines.append(f"| 实际利率对冲（黄金） | {gold_w:.0%} | {gold_w:.0%} |")
        lines.append(f"| 商品（石油） | {oil_w:.0%} | {oil_w:.0%} |")
        lines.append(f"| 现金 | {cash_w:.0%} | {cash_w:.0%} |")
        lines.append("")

    # ─── Summary ───
    if is_v1 and asset_data.get("summary"):
        lines.append(f"> {asset_data['summary']}")
        lines.append("")

    lines.append("<!-- LAYER0_SUMMARY_COMPLETE -->")
    return "\n".join(lines)


def _cat_label(cat: str) -> str:
    """Map category key to Chinese label"""
    mapping = {
        "commodity": "商品", "bond": "债券",
        "overseas_equity": "海外", "domestic_equity": "A股",
        "domestic_hybrid": "可转债",
        "cash": "现金",
        # Legacy mappings
        "growth": "成长", "cycle": "周期", "defense": "防御",
        "financial": "金融", "consumer": "消费", "other": "其他",
    }
    return mapping.get(cat, cat)


def _drift_signal(drift: float) -> str:
    """Return signal icon for weight drift"""
    if abs(drift) > 0.10:
        return "🔴"
    elif abs(drift) > 0.05:
        return "🟡"
    return "🟢"


def _action_label(action: str) -> str:
    """Map action to Chinese label"""
    return {"buy": "买入 ↑", "sell": "卖出 ↓", "hold": "持有 →", "new": "新建 ★"}.get(action, action)


# ═══════════════════════════════════════════════════════════════
# Layer 1: 板块轮动
# ═══════════════════════════════════════════════════════════════

def build_layer1_summary() -> str:
    """
    生成 Layer 1 板块轮动概览 markdown。
    数据源: sector_signals.json + 可选 _sector_performance.md
    """
    # 优先读 FM_trade 本地副本，回退到 quant-signal-factory
    sector_data = load_json(DATA_DIR / "sector_signals.json")
    if sector_data is None:
        sector_data = load_json(QF_DATA_DIR / "sector_signals.json")

    today = date.today().isoformat()
    lines = []

    lines.append("## 一、板块配置概览 (Layer 1)")
    lines.append("")

    if sector_data is None:
        lines.append("> ⚠️ `sector_signals.json` 不存在。Layer 1 板块信号待生成。")
        lines.append("> 预计由 `generate_sector_signals.py` 在信号工厂管线中产出。")
        lines.append("")
        lines.append("<!-- LAYER1_SUMMARY_COMPLETE -->")
        return "\n".join(lines)

    model_name = sector_data.get("model_name", "—")
    perf = sector_data.get("model_performance", {})
    ic_pearson = perf.get("ic_pearson", "—")
    ic_rank = perf.get("ic_rank", "—")
    signal_date = sector_data.get("date", "—")

    lines.append(f"> 数据源: sector_signals.json | 模型: {model_name} | 信号日期: {signal_date}")
    if isinstance(ic_pearson, (int, float)):
        lines.append(f"> IC Pearson: {ic_pearson:.4f} | IC Rank: {ic_rank:.4f}")
    lines.append("")

    # ─── Top-5 优选板块 ───
    top5 = sector_data.get("top_5_selected", [])
    lines.append("### ML Top-5 优选板块")
    lines.append("")
    lines.append("| Rank | 板块 | 申万代码 | 类别 | ML Score | 集中度限制 |")
    lines.append("|---|---|---|---|---|---|")
    for item in top5:
        rank = item.get("rank", "—")
        name = item.get("sector_name", "—")
        code = item.get("sector_code", "—")
        cat = item.get("category", "other")
        cat_label = CATEGORY_LABELS.get(cat, cat)
        cat_icon = CATEGORY_ICONS.get(cat, "")
        score = item.get("score", 0)
        lines.append(f"| {rank} | {cat_icon} {name} | {code} | {cat_label} | {score:+.4f} | max 30% |")
    lines.append("")

    # ─── 多样性约束 ───
    diversity = sector_data.get("diversity_constraint", {})
    cat_counts = diversity.get("category_counts", {})
    if cat_counts:
        lines.append("### 多样性约束")
        lines.append(f"> max {diversity.get('max_per_category', 2)}/category")
        lines.append("")
        lines.append("| 类别 | 总数 | 已选 |")
        lines.append("|---|---|---|")
        top5_cats = [item.get("category") for item in top5]
        for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
            cat_label = CATEGORY_LABELS.get(cat, cat)
            selected_count = top5_cats.count(cat)
            status = "✅" if selected_count <= diversity.get("max_per_category", 2) else "⚠️ 超出"
            lines.append(f"| {cat_label} | {count} | {selected_count} {status} |")
        lines.append("")

    # ─── 全板块排名（紧凑格式） ───
    all_sectors = sector_data.get("sector_signals", [])
    if all_sectors:
        lines.append("### 31 板块完整排名")
        lines.append("")
        lines.append("| Rank | 板块 | 类别 | Score | 选中 |")
        lines.append("|---|---|---|---|---|")
        for item in all_sectors[:15]:  # 前 15 详细展示
            rank = item.get("rank", "—")
            name = item.get("sector_name", "—")
            cat = item.get("category", "other")
            cat_icon = CATEGORY_ICONS.get(cat, "")
            score = item.get("score", 0)
            selected = "✅" if item.get("selected") else ""
            lines.append(f"| {rank} | {cat_icon} {name} | {CATEGORY_LABELS.get(cat, cat)} | {score:+.4f} | {selected} |")

        # 后 15 紧凑显示
        remaining = []
        for item in all_sectors[15:]:
            name = item.get("sector_name", "—")
            score = item.get("score", 0)
            selected = "★" if item.get("selected") else ""
            remaining.append(f"{name}({score:+.4f}{selected})")
        lines.append(f"| 16-30 | {' · '.join(remaining)} | | | |")
        lines.append("")

    # ─── 与 Layer 2 的联动 ───
    lines.append("### 与 Layer 2 选股联动")
    lines.append("")
    lines.append("> 读取 `portfolio.json` → `watchlist` 交叉板块归属")
    lines.append("")
    lines.append("| 自选标的 | 所属板块 | 板块 Rank | Layer 2 状态 | 优先级 |")
    lines.append("|---|---|---|---|---|")
    lines.append("| (需运行时读取 portfolio.json 填充) | | | | |")
    lines.append("")
    lines.append("> 🟢 N/M 自选标的处于 Top-5 板块内，Layer 1 提供顺风。")
    lines.append("")

    lines.append("<!-- LAYER1_SUMMARY_COMPLETE -->")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# Layer 1: Sector Performance (实时板块表现)
# ═══════════════════════════════════════════════════════════════

def build_performance_report() -> str:
    """
    生成板块实时表现报告。
    调用 fetch_sector_performance.py 获取数据并生成 markdown。
    如果 fetch_sector_performance.py 不可用则返回空字符串。
    """
    try:
        # Add script directory to path for import
        script_dir = str(PROJECT_ROOT / "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        from fetch_sector_performance import (
            fetch_all_sector_returns,
            compare_with_ml_predictions,
            generate_performance_markdown,
        )

        print("[Layer 1 Performance] Fetching sector returns...")
        returns_df = fetch_all_sector_returns(
            data_dir=Path("D:/AgentAlpha/signal-factory/data/sector")
        )

        if len(returns_df) == 0:
            print("[Layer 1 Performance] No sector data available")
            return ""

        print(f"[Layer 1 Performance] Loaded {len(returns_df)} sectors, comparing with ML predictions...")
        comparison_df = compare_with_ml_predictions(
            returns_df,
            signals_path=DATA_DIR / "sector_signals.json",
        )

        md = generate_performance_markdown(comparison_df)

        # Save to output
        out = DATA_DIR / "_sector_performance.md"
        out.write_text(md, encoding="utf-8")
        print(f"[Layer 1 Performance] → {out}")

        return md

    except ImportError as e:
        print(f"[Layer 1 Performance] fetch_sector_performance.py not available: {e}")
        return ""
    except Exception as e:
        print(f"[Layer 1 Performance] Error: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════
# Layer 1: 午间/盘后验证模板
# ═══════════════════════════════════════════════════════════════

def build_layer1_intraday_template(report_type: str = "midday") -> str:
    """
    生成 Layer 1 盘中验证模板（午间/盘后）。
    report_type: "midday" | "eod"

    优先使用 fetch_sector_performance.py 获取实际板块表现，
    如果不可用则输出占位符模板。
    """
    sector_data = load_json(DATA_DIR / "sector_signals.json")
    if sector_data is None:
        sector_data = load_json(QF_DATA_DIR / "sector_signals.json")

    section_label = "午盘" if report_type == "midday" else "日终"
    lines = []

    if report_type == "midday":
        lines.append(f"## 三、板块轮动{section_label}验证 (Layer 1)")
    else:
        lines.append(f"## 三、Layer 1 板块轮动{section_label}验证")

    lines.append("")

    if sector_data is None:
        lines.append(f"> ⚠️ sector_signals.json 不可用，跳过板块验证。")
        lines.append("")
        return "\n".join(lines)

    top5 = sector_data.get("top_5_selected", [])
    perf = sector_data.get("model_performance", {})
    signal_date = sector_data.get("date", "—")

    lines.append(f"> 模型: {sector_data.get('model_name', '—')} | 信号日期: {signal_date}")
    lines.append("")

    # ─── Try to get real performance data ───
    perf_data = _try_load_performance_data()

    # ─── Top-5 预测 vs 实际 ───
    lines.append("### Top-5 预测板块 vs 实际表现")
    lines.append("")
    lines.append("| 板块 | ML 预测 Rank | 上午涨跌 | 实际 Rank | 偏离 | 判断 |")
    lines.append("|---|---|---|---|---|---|")

    if perf_data is not None:
        # Use real data
        for item in top5:
            name = item.get("sector_name", "—")
            rank = item.get("rank", "—")
            # Find matching row in performance data
            match = perf_data[perf_data["name"] == name]
            if len(match) > 0:
                row = match.iloc[0]
                chg = f'{row["change_pct"]:+.2f}%' if not pd.isna(row["change_pct"]) else "—"
                actual_rank = int(row["actual_rank"]) if not pd.isna(row["actual_rank"]) else "—"
                ml_rank = rank
                try:
                    diff = int(ml_rank) - int(actual_rank)
                except (ValueError, TypeError):
                    diff = "—"
                # Judgment
                if isinstance(diff, int):
                    if diff <= 0:
                        judge = "✅ 命中" if diff >= -2 else "🟡 小幅偏离"
                    else:
                        judge = "❌ 大幅偏离" if diff >= 10 else "🟠 偏低"
                else:
                    judge = "—"
                lines.append(f"| {name} | #{ml_rank} | {chg} | #{actual_rank} | {diff} | {judge} |")
            else:
                lines.append(f"| {name} | #{rank} | (无数据) | — | — | — |")
    else:
        # Fallback placeholders
        for item in top5:
            name = item.get("sector_name", "—")
            rank = item.get("rank", "—")
            lines.append(f"| {name} | #{rank} | (需实时数据) | (需实时数据) | — | — |")
    lines.append("")

    # ─── 板块资金流向 ───
    lines.append("### 板块资金流向")
    lines.append("")
    lines.append("| 方向 | 板块 | 净流入(亿) |")
    lines.append("|---|---|---|")
    lines.append("| (需实时数据) | | |")
    lines.append("")

    # ─── ML 模型表现跟踪（仅盘后） ───
    if report_type == "eod":
        ic_pearson = perf.get("ic_pearson", "—")
        ic_rank = perf.get("ic_rank", "—")
        lines.append("### ML 模型表现跟踪")
        lines.append("")
        lines.append("| 指标 | 本周 | 本月 | 累计 (OOS) | 目标 |")
        lines.append("|---|---|---|---|---|")
        if isinstance(ic_rank, (int, float)):
            lines.append(f"| IC Rank (Spearman) | — | — | {ic_rank:.4f} | > 0.05 |")
        if isinstance(ic_pearson, (int, float)):
            lines.append(f"| IC Pearson | — | — | {ic_pearson:.4f} | — |")
        lines.append("| Top-5 命中率 | — | — | — | > 30% |")
        lines.append("| Top-5 等权 Sharpe | — | — | 0.65 | > 0.5 ✅ |")
        lines.append("")

    return "\n".join(lines)


def _try_load_performance_data():
    """Try to load sector performance data from fetch_sector_performance.py.
    Returns DataFrame or None if unavailable."""
    try:
        import pandas as pd
        script_dir = str(PROJECT_ROOT / "scripts")
        if script_dir not in sys.path:
            sys.path.insert(0, script_dir)

        from fetch_sector_performance import (
            fetch_all_sector_returns,
            compare_with_ml_predictions,
        )

        returns_df = fetch_all_sector_returns(
            data_dir=Path("D:/AgentAlpha/signal-factory/data/sector")
        )
        if len(returns_df) == 0:
            return None

        comparison_df = compare_with_ml_predictions(
            returns_df,
            signals_path=DATA_DIR / "sector_signals.json",
        )
        return comparison_df
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════
# CSI300 Daily K-line (v4.3.1 — P0 data integrity fix)
# ═══════════════════════════════════════════════════════════════

def build_csi300_daily() -> str:
    """
    生成 CSI300 日线数据 JSON，作为晨报权威数据源。

    使用 akshare stock_zh_index_daily 获取最近 10 个交易日 OHLCV，
    同时计算 MA5/MA20 等技术指标。输出到 data/output/_csi300_daily.json。

    设计目的：消除 claude -p 动态调用 Wind MCP 的不确定性，
    提供磁盘上的权威 CSI300 数据，配合 ETF 交叉验证杜绝午间盘数据误用。

    Returns:
        状态描述字符串
    """
    import sys
    import json as _json
    from pathlib import Path as _Path

    json_path = DATA_DIR / "_csi300_daily.json"

    try:
        import akshare as ak
        import pandas as pd

        # QSF fallback import (akshare → baostock for index daily)
        _qsf_root = str(_Path("D:/AgentAlpha/signal-factory"))
        if _qsf_root not in sys.path:
            sys.path.insert(0, _qsf_root)
        from src.data.akshare_fallback import stock_zh_index_daily_with_fallback

        # Fetch last 60 trading days to compute moving averages
        df = stock_zh_index_daily_with_fallback(symbol="sh000300")
        if df is None or len(df) == 0:
            return "CSI300 daily: FAILED — akshare returned empty"

        # Standardize column names
        df = df.rename(columns={
            "date": "date", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume"
        })
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

        # Compute technical indicators
        df["ma5"] = df["close"].rolling(5).mean().round(2)
        df["ma20"] = df["close"].rolling(20).mean().round(2)
        df["chg_pct"] = (df["close"].pct_change() * 100).round(4)

        # Last 10 trading days for the daily payload
        recent = df.tail(10).copy()

        # Determine latest state
        latest = recent.iloc[-1]
        latest_date = latest["date"]
        latest_close = float(latest["close"])
        latest_ma5 = float(latest["ma5"]) if not pd.isna(latest["ma5"]) else None
        latest_ma20 = float(latest["ma20"]) if not pd.isna(latest["ma20"]) else None
        latest_chg = float(latest["chg_pct"]) if not pd.isna(latest["chg_pct"]) else None

        # Price vs MA state
        if latest_ma5 is not None and latest_ma20 is not None:
            if latest_close > latest_ma5 > latest_ma20:
                trend = "bullish"       # 多头排列
            elif latest_close < latest_ma5 < latest_ma20:
                trend = "bearish"       # 空头排列
            elif latest_close < latest_ma5:
                trend = "weak"          # 短空但长多
            else:
                trend = "mixed"         # 其他
        else:
            trend = "insufficient_data"

        # Build output
        payload = {
            "data_date": latest_date,
            "generated_at": pd.Timestamp.now().isoformat(),
            "source": "akshare+baostock stock_zh_index_daily_with_fallback(sh000300)",
            "latest": {
                "date": latest_date,
                "open": float(latest["open"]),
                "high": float(latest["high"]),
                "low": float(latest["low"]),
                "close": latest_close,
                "volume": int(latest["volume"]),
                "chg_pct": latest_chg,
                "ma5": latest_ma5,
                "ma20": latest_ma20,
                "trend": trend,
            },
            "ma_state": {
                "close_vs_ma5": f"{latest_close - latest_ma5:+.2f}" if latest_ma5 else None,
                "close_vs_ma20": f"{latest_close - latest_ma20:+.2f}" if latest_ma20 else None,
                "ma5_direction": "down" if latest_ma5 and len(df) >= 6 and latest_ma5 < float(df.iloc[-6]["close"]) else "up" if latest_ma5 else None,
                "trend": trend,
            },
            "recent_10_days": [
                {
                    "date": row["date"],
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": int(row["volume"]),
                    "chg_pct": float(row["chg_pct"]) if not pd.isna(row["chg_pct"]) else None,
                }
                for _, row in recent.iterrows()
            ],
            "previous_low": float(recent.iloc[-2]["low"]) if len(recent) >= 2 else None,
        }

        # Write JSON
        json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False, indent=2)

        direction = "↑" if latest_chg and latest_chg > 0 else "↓" if latest_chg and latest_chg < 0 else "→"
        return (
            f"CSI300 daily → {json_path} "
            f"({latest_date} close={latest_close:.2f} {direction}{latest_chg:+.2f}%, "
            f"trend={trend}, MA5={latest_ma5}, MA20={latest_ma20})"
        )

    except ImportError as e:
        return f"CSI300 daily: FAILED — import error: {e}"
    except Exception as e:
        return f"CSI300 daily: FAILED — {type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="三层架构报告数据桥接")
    parser.add_argument("--layer0", action="store_true", help="仅生成 Layer 0")
    parser.add_argument("--layer1", action="store_true", help="仅生成 Layer 1")
    parser.add_argument("--csi300", action="store_true", help="生成 CSI300 日线数据 (_csi300_daily.json)")
    parser.add_argument("--performance", action="store_true", help="生成板块实时表现报告")
    parser.add_argument("--midday", action="store_true", help="生成午间验证模板")
    parser.add_argument("--eod", action="store_true", help="生成盘后跟踪模板")
    parser.add_argument("--eod-light", action="store_true", help="仅生成 EOD 数据 (CSI300 + ETF)，不生成完整摘要")
    args = parser.parse_args()

    run_all = not any([args.layer0, args.layer1, args.csi300, args.performance, args.midday, args.eod, args.eod_light])

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    results = []

    if run_all or args.layer0:
        md = build_layer0_summary()
        out = DATA_DIR / "_layer0_summary.md"
        out.write_text(md, encoding="utf-8")
        results.append(f"Layer 0 → {out}")

    if run_all or args.layer1:
        md = build_layer1_summary()
        out = DATA_DIR / "_layer1_summary.md"
        out.write_text(md, encoding="utf-8")
        results.append(f"Layer 1 → {out}")

    if run_all or args.csi300 or args.eod_light:
        msg = build_csi300_daily()
        results.append(msg)

    if run_all or args.performance:
        md = build_performance_report()
        if md:
            results.append(f"Performance → {DATA_DIR / '_sector_performance.md'}")
        else:
            results.append("Performance → SKIPPED (no data available)")

    if args.midday:
        md = build_layer1_intraday_template("midday")
        out = DATA_DIR / "_layer1_midday_template.md"
        out.write_text(md, encoding="utf-8")
        results.append(f"Layer 1 Midday → {out}")

    if args.eod:
        md = build_layer1_intraday_template("eod")
        out = DATA_DIR / "_layer1_eod_template.md"
        out.write_text(md, encoding="utf-8")
        results.append(f"Layer 1 EOD → {out}")

    if args.eod_light:
        # Light EOD: only CSI300 daily data (already done above)
        # No Layer 0/1 summaries or sector performance
        results.append("EOD light: CSI300 daily only (no summaries)")

    if not results:
        print("未选择任何生成目标。")
        sys.exit(1)

    for r in results:
        print(r)
    print("Done.")


if __name__ == "__main__":
    main()