#!/usr/bin/env python3
"""
fetch_sector_performance.py — 申万31行业实时表现与ML预测对比

从 quant-signal-factory 缓存的 sector_*.csv 读取日线数据，
计算每个行业的最新涨跌幅，与 sector_signals.json 的ML预测对比，
输出 markdown 报告。

用法:
  python scripts/fetch_sector_performance.py                     # 输出到 stdout + 写入 _sector_performance.md
  python scripts/fetch_sector_performance.py --date 2026-09-10   # 指定分析日期
  python scripts/fetch_sector_performance.py --no-output         # 仅打印 summary，不写文件

被 build_report_context.py 导入使用:
  from scripts.fetch_sector_performance import (
      fetch_all_sector_returns,
      compare_with_ml_predictions,
      generate_performance_markdown,
  )
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import pandas as pd

# ── 路径常亮 ────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR_OUTPUT = PROJECT_ROOT / "data" / "output"
SECTOR_CACHE_DIR = Path("D:/AgentAlpha/signal-factory/data/sector")

# ── 申万31行业代码 → 名称+类别映射 ──────────────────────────────

SW_SECTOR_CODES: Dict[str, Dict[str, str]] = {
    "801010": {"name": "农林牧渔", "category": "cycle"},
    "801020": {"name": "采掘", "category": "cycle"},
    "801030": {"name": "化工", "category": "cycle"},
    "801040": {"name": "钢铁", "category": "cycle"},
    "801050": {"name": "有色金属", "category": "cycle"},
    "801080": {"name": "电子", "category": "growth"},
    "801110": {"name": "家用电器", "category": "consumer"},
    "801120": {"name": "食品饮料", "category": "consumer"},
    "801130": {"name": "纺织服装", "category": "consumer"},
    "801140": {"name": "轻工制造", "category": "consumer"},
    "801150": {"name": "医药生物", "category": "defense"},
    "801160": {"name": "公用事业", "category": "defense"},
    "801170": {"name": "交通运输", "category": "cycle"},
    "801180": {"name": "房地产", "category": "cycle"},
    "801200": {"name": "商业贸易", "category": "consumer"},
    "801210": {"name": "休闲服务", "category": "consumer"},
    "801230": {"name": "综合", "category": "other"},
    "801710": {"name": "建筑材料", "category": "cycle"},
    "801720": {"name": "建筑装饰", "category": "cycle"},
    "801730": {"name": "电气设备", "category": "growth"},
    "801740": {"name": "国防军工", "category": "defense"},
    "801750": {"name": "计算机", "category": "growth"},
    "801760": {"name": "传媒", "category": "growth"},
    "801770": {"name": "通信", "category": "growth"},
    "801780": {"name": "银行", "category": "financial"},
    "801790": {"name": "非银金融", "category": "financial"},
    "801880": {"name": "汽车", "category": "cycle"},
    "801890": {"name": "机械设备", "category": "cycle"},
    "801950": {"name": "石油石化", "category": "cycle"},
    "801960": {"name": "电力设备", "category": "growth"},
    "801970": {"name": "环保", "category": "defense"},
}

CATEGORY_LABELS = {
    "growth": "成长",
    "cycle": "周期",
    "defense": "防御",
    "financial": "金融",
    "consumer": "消费",
    "other": "其他",
}


# ═══════════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════════


def _find_latest_trading_date(df: pd.DataFrame, as_of: Optional[str] = None) -> pd.Timestamp:
    """找到 CSV 中不晚于 as_of 的最新交易日期。"""
    dates = pd.to_datetime(df["date"]).sort_values()
    if as_of:
        cutoff = pd.to_datetime(as_of)
        dates = dates[dates <= cutoff]
    if dates.empty:
        raise ValueError("No trading dates found in data")
    return dates.iloc[-1]


def _compute_returns_for_sector(df: pd.DataFrame, latest_date: pd.Timestamp) -> Dict:
    """计算单个行业的三个周期收益率。"""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)

    # 定位 latest_date 在 df 中的位置
    idx = df[df["date"] == latest_date].index
    if len(idx) == 0:
        return None
    idx = idx[0]

    latest_close = df.loc[idx, "close"]

    # 上一日收盘价（确保是前一个交易日的 close）
    if idx >= 1:
        prev_close = df.loc[idx - 1, "close"]
        chg_pct = (latest_close / prev_close - 1) * 100
    else:
        chg_pct = None

    # 5日和20日收益率
    def _pct_change(lookback: int):
        ref_idx = idx - lookback
        if ref_idx >= 0:
            return (latest_close / df.loc[ref_idx, "close"] - 1) * 100
        return None

    return {
        "sector_code": str(df.loc[idx, "sw_code"]),
        "name": str(df.loc[idx, "sector_name"]),
        "category": str(df.loc[idx, "category"]),
        "latest_date": latest_date,
        "close": latest_close,
        "change_pct": chg_pct,
        "change_5d": _pct_change(5),
        "change_20d": _pct_change(20),
    }


def fetch_all_sector_returns(
    data_dir: Optional[Path] = None,
    latest_date: Optional[str] = None,
) -> pd.DataFrame:
    """从缓存 CSV 读取所有行业收益率。

    Args:
        data_dir: sector CSV 目录，默认 SECTOR_CACHE_DIR
        latest_date: 分析基准日 (YYYY-MM-DD)，默认每个 CSV 各自的最新日

    Returns:
        DataFrame with columns: sector_code, name, category, latest_date,
        close, change_pct, change_5d, change_20d
    """
    data_dir = Path(data_dir) if data_dir else SECTOR_CACHE_DIR
    results = []
    missing = []

    for code, info in SW_SECTOR_CODES.items():
        csv_path = data_dir / f"sector_{code}.csv"
        if not csv_path.exists():
            missing.append(f"{code} {info['name']} (file not found)")
            continue

        try:
            df = pd.read_csv(csv_path)
        except Exception as e:
            missing.append(f"{code} {info['name']} (read error: {e})")
            continue

        if "close" not in df.columns or "date" not in df.columns:
            missing.append(f"{code} {info['name']} (missing columns)")
            continue

        try:
            target_date = _find_latest_trading_date(df, latest_date)
            row = _compute_returns_for_sector(df, target_date)
            if row:
                results.append(row)
        except Exception as e:
            missing.append(f"{code} {info['name']} (compute error: {e})")

    if missing:
        print(f"[WARN] {len(missing)} sectors skipped:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)

    if not results:
        return pd.DataFrame(
            columns=["sector_code", "name", "category", "latest_date",
                     "close", "change_pct", "change_5d", "change_20d"]
        )

    ret_df = pd.DataFrame(results)
    # 按 change_pct 降序排名
    ret_df = ret_df.sort_values("change_pct", ascending=False, na_position="last").reset_index(drop=True)
    ret_df["actual_rank"] = range(1, len(ret_df) + 1)
    return ret_df


def compare_with_ml_predictions(
    returns_df: pd.DataFrame,
    signals_path: Optional[Path] = None,
) -> pd.DataFrame:
    """将实际收益率与 ML 预测对比。

    Args:
        returns_df: fetch_all_sector_returns() 的输出
        signals_path: sector_signals.json 路径

    Returns:
        DataFrame with additional columns: ml_rank, ml_score, ml_selected,
        hit_top5, hit_top10
    """
    df = returns_df.copy()

    # 默认值
    df["ml_rank"] = None
    df["ml_score"] = None
    df["ml_selected"] = False
    df["hit_top5"] = False
    df["hit_top10"] = False

    signals_path = Path(signals_path) if signals_path else (DATA_DIR_OUTPUT / "sector_signals.json")
    if not signals_path.exists():
        print(f"[INFO] sector_signals.json not found at {signals_path}, skipping ML comparison")
        return df

    with open(signals_path, "r", encoding="utf-8") as f:
        signals = json.load(f)

    sector_signals = signals.get("sector_signals", [])
    if not sector_signals:
        print("[INFO] sector_signals is empty, skipping ML comparison")
        return df

    # 构建 sector_code → ml_info 映射
    ml_map = {}
    for s in sector_signals:
        ml_map[s["sector_code"]] = {
            "ml_rank": s.get("rank"),
            "ml_score": s.get("score"),
            "ml_selected": s.get("selected", False),
        }

    # 获取 ML top-5 和 top-10 的 sector_code 集合
    ml_top5 = {s["sector_code"] for s in sector_signals if s.get("rank") and s["rank"] <= 5}
    ml_top10 = {s["sector_code"] for s in sector_signals if s.get("rank") and s["rank"] <= 10}

    # 实际 top-5 和 top-10
    actual_top5 = set(df.head(5)["sector_code"].values)
    actual_top10 = set(df.head(10)["sector_code"].values)

    for i, row in df.iterrows():
        code = row["sector_code"]
        ml = ml_map.get(code)
        if ml:
            df.at[i, "ml_rank"] = ml["ml_rank"]
            df.at[i, "ml_score"] = ml["ml_score"]
            df.at[i, "ml_selected"] = ml["ml_selected"]

        # 命中判断：ML预测top-N 实际也是 top-N
        if code in ml_top5 and code in actual_top5:
            df.at[i, "hit_top5"] = True
        if code in ml_top10 and code in actual_top10:
            df.at[i, "hit_top10"] = True

    # 附上元信息
    df.attrs["ml_model_name"] = signals.get("model_name", "—")
    df.attrs["ml_signal_date"] = signals.get("date", "—")
    df.attrs["ml_ic_pearson"] = signals.get("model_performance", {}).get("ic_pearson")
    df.attrs["ml_ic_rank"] = signals.get("model_performance", {}).get("ic_rank")
    df.attrs["ml_top5_codes"] = ml_top5
    df.attrs["ml_top10_codes"] = ml_top10
    df.attrs["actual_top5_codes"] = actual_top5
    df.attrs["actual_top10_codes"] = actual_top10

    return df


def generate_performance_markdown(comparison_df: pd.DataFrame) -> str:
    """生成板块表现 markdown 报告。

    Args:
        comparison_df: compare_with_ml_predictions() 的输出

    Returns:
        markdown 字符串
    """
    df = comparison_df
    has_ml = df["ml_rank"].notna().any()
    today_str = date.today().isoformat()

    lines = []
    lines.append(f"# 申万一级行业板块表现报告")
    lines.append(f"")
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"> 数据日期: {today_str}")

    # 统计有效数据日期
    data_dates = df["latest_date"].dropna()
    if len(data_dates) > 0:
        latest_data_date = data_dates.max()
        lines.append(f"> 最新行情日期: {latest_data_date.date()}")

    if has_ml:
        lines.append(f"> ML 模型: {df.attrs.get('ml_model_name', '—')}")
        lines.append(f"> ML 信号日期: {df.attrs.get('ml_signal_date', '—')}")
        ic_p = df.attrs.get("ml_ic_pearson")
        ic_r = df.attrs.get("ml_ic_rank")
        if ic_p is not None:
            lines.append(f"> IC Pearson: {ic_p:.4f} | IC Rank: {ic_r:.4f}")
    lines.append("")

    # ── 0. 全板块排名总表 ──
    lines.append("## 全板块排名（按今日涨跌幅降序）")
    lines.append("")

    if has_ml:
        header = "| 排名 | 板块 | 类别 | 涨跌幅% | 5日% | 20日% | ML预测排名 | ML Score | 命中 |"
        sep = "|---|---|---|---|---|---|---|---|---|"
    else:
        header = "| 排名 | 板块 | 类别 | 涨跌幅% | 5日% | 20日% |"
        sep = "|---|---|---|---|---|---|---|"
    lines.append(header)
    lines.append(sep)

    for _, row in df.iterrows():
        code = row["sector_code"]
        name = row["name"]
        cat_raw = row["category"]
        cat_label = CATEGORY_LABELS.get(cat_raw, cat_raw)
        chg = f'{row["change_pct"]:+.2f}' if row["change_pct"] is not None and not pd.isna(row["change_pct"]) else "—"
        chg5 = f'{row["change_5d"]:+.2f}' if row["change_5d"] is not None and not pd.isna(row["change_5d"]) else "—"
        chg20 = f'{row["change_20d"]:+.2f}' if row["change_20d"] is not None and not pd.isna(row["change_20d"]) else "—"

        if has_ml:
            ml_rank = int(row["ml_rank"]) if row["ml_rank"] is not None and not pd.isna(row["ml_rank"]) else "—"
            ml_score = f'{row["ml_score"]:+.4f}' if row["ml_score"] is not None and not pd.isna(row["ml_score"]) else "—"
            hit = "✅" if row["hit_top5"] else ("🟡" if row["hit_top10"] else "")
            lines.append(f"| {row['actual_rank']} | {name} | {cat_label} | {chg} | {chg5} | {chg20} | {ml_rank} | {ml_score} | {hit} |")
        else:
            lines.append(f"| {row['actual_rank']} | {name} | {cat_label} | {chg} | {chg5} | {chg20} |")

    lines.append("")

    # ── 1. Top-5 预测准确度总结 ──
    if has_ml:
        ml_top5_codes = df.attrs.get("ml_top5_codes", set())
        ml_top10_codes = df.attrs.get("ml_top10_codes", set())
        actual_top5_codes = df.attrs.get("actual_top5_codes", set())
        actual_top10_codes = df.attrs.get("actual_top10_codes", set())

        top5_hits = len(ml_top5_codes & actual_top5_codes)
        top10_hits = len(ml_top5_codes & actual_top10_codes)

        lines.append("## ML Top-5 预测准确度")
        lines.append("")
        lines.append(f"| 指标 | 值 |")
        lines.append(f"|---|---|")
        lines.append(f"| ML Top-5 命中实际 Top-5 数量 | {top5_hits}/5 |")
        lines.append(f"| ML Top-5 命中实际 Top-10 数量 | {top10_hits}/5 |")
        lines.append("")

        # 逐项清单
        lines.append("### ML Top-5 逐项表现")
        lines.append("")
        lines.append("| ML预测排名 | 板块 | ML Score | 实际排名 | 实际涨跌幅 | 命中 |")
        lines.append("|---|---|---|---|---|---|")

        for _, row in df.iterrows():
            ml_r = row["ml_rank"]
            if ml_r is None or pd.isna(ml_r) or int(ml_r) > 5:
                continue
            name = row["name"]
            ml_score = f'{row["ml_score"]:+.4f}' if row["ml_score"] is not None and not pd.isna(row["ml_score"]) else "—"
            actual_r = row["actual_rank"]
            chg = f'{row["change_pct"]:+.2f}%' if row["change_pct"] is not None and not pd.isna(row["change_pct"]) else "—"
            hit = "✅ Top-5" if row["hit_top5"] else ("🟡 Top-10" if row["hit_top10"] else "❌")
            lines.append(f"| {int(ml_r)} | {name} | {ml_score} | {actual_r} | {chg} | {hit} |")
        lines.append("")

        # ── 2. 最大惊喜/失望 ──
        lines.append("## 最大偏差分析")
        lines.append("")

        # 过滤有 ML 排名的行
        ml_rows = df[df["ml_rank"].notna() & (df["ml_rank"] > 0)].copy()
        if len(ml_rows) > 0:
            ml_rows["rank_diff"] = ml_rows["ml_rank"].astype(int) - ml_rows["actual_rank"].astype(int)

            # ML高估：ML排名靠前但实际排名靠后（rank_diff 负数最大）
            overestimated = ml_rows.nsmallest(5, "rank_diff")
            # ML低估：ML排名靠后但实际排名靠前（rank_diff 正数最大）
            underestimated = ml_rows.nlargest(5, "rank_diff")

            lines.append("### ML 预测过于乐观（ML排名高，实际表现差）")
            lines.append("")
            lines.append("| ML排名 | 板块 | ML Score | 实际排名 | 涨跌幅% | 偏差 |")
            lines.append("|---|---|---|---|---|---|")
            for _, row in overestimated.iterrows():
                name = row["name"]
                ml_r = int(row["ml_rank"])
                actual_r = int(row["actual_rank"])
                chg = f'{row["change_pct"]:+.2f}%' if row["change_pct"] is not None and not pd.isna(row["change_pct"]) else "—"
                diff = int(row["rank_diff"])
                lines.append(f"| {ml_r} | {name} | {row['ml_score']:+.4f} | {actual_r} | {chg} | {diff:+d} |")
            lines.append("")

            lines.append("### ML 预测过于悲观（ML排名低，实际表现好）")
            lines.append("")
            lines.append("| ML排名 | 板块 | ML Score | 实际排名 | 涨跌幅% | 偏差 |")
            lines.append("|---|---|---|---|---|---|")
            for _, row in underestimated.iterrows():
                name = row["name"]
                ml_r = int(row["ml_rank"])
                actual_r = int(row["actual_rank"])
                chg = f'{row["change_pct"]:+.2f}%' if row["change_pct"] is not None and not pd.isna(row["change_pct"]) else "—"
                diff = int(row["rank_diff"])
                lines.append(f"| {ml_r} | {name} | {row['ml_score']:+.4f} | {actual_r} | {chg} | {diff:+d} |")
            lines.append("")
        else:
            lines.append("> 无 ML 预测数据，跳过偏差分析。")
            lines.append("")

    # ── 3. 类别统计 ──
    lines.append("## 类别平均表现")
    lines.append("")
    lines.append("| 类别 | 板块数 | 平均涨跌幅% | 平均5日% | 平均20日% |")
    lines.append("|---|---|---|---|---|")

    for cat_raw in ["growth", "cycle", "defense", "financial", "consumer", "other"]:
        cat_df = df[df["category"] == cat_raw]
        if len(cat_df) == 0:
            continue
        cat_label = CATEGORY_LABELS.get(cat_raw, cat_raw)
        avg_chg = cat_df["change_pct"].mean()
        avg_5d = cat_df["change_5d"].mean()
        avg_20d = cat_df["change_20d"].mean()
        n = len(cat_df)
        lines.append(
            f"| {cat_label} | {n} | {avg_chg:+.2f} | {avg_5d:+.2f} | {avg_20d:+.2f} |"
        )
    lines.append("")

    # 整体统计
    n_total = len(df)
    n_up = len(df[df["change_pct"] > 0]) if df["change_pct"].notna().any() else 0
    n_down = len(df[df["change_pct"] < 0]) if df["change_pct"].notna().any() else 0
    avg_all = df["change_pct"].mean() if df["change_pct"].notna().any() else 0.0

    lines.append("## 整体统计")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|---|---|")
    lines.append(f"| 有效板块数 | {n_total} |")
    lines.append(f"| 上涨板块 | {n_up} |")
    lines.append(f"| 下跌板块 | {n_down} |")
    lines.append(f"| 平均涨跌幅 | {avg_all:+.2f}% |")
    lines.append("")

    lines.append("---")
    lines.append(f"*报告由 fetch_sector_performance.py 自动生成*")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# CLI / Main
# ═══════════════════════════════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="申万31行业实时表现与ML预测对比"
    )
    parser.add_argument(
        "--date", type=str, default=None,
        help="分析基准日 YYYY-MM-DD (默认: 每个CSV各自最新日)"
    )
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help="sector CSV 目录 (默认: D:/AgentAlpha/signal-factory/data/sector)"
    )
    parser.add_argument(
        "--signals", type=str, default=None,
        help="sector_signals.json 路径 (默认: data/output/sector_signals.json)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="输出 markdown 路径 (默认: data/output/_sector_performance.md)"
    )
    parser.add_argument(
        "--no-output", action="store_true",
        help="不写入文件，仅打印摘要到 stdout"
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else SECTOR_CACHE_DIR
    signals_path = Path(args.signals) if args.signals else (DATA_DIR_OUTPUT / "sector_signals.json")
    output_path = Path(args.output) if args.output else (DATA_DIR_OUTPUT / "_sector_performance.md")

    # ── Step 1: 读取收益率 ──
    print("=" * 60)
    print(" 申万31行业板块表现分析")
    print("=" * 60)
    print(f"  数据目录: {data_dir}")
    print(f"  分析日期: {args.date or '(各板块最新交易日)'}")
    print()

    returns_df = fetch_all_sector_returns(data_dir=data_dir, latest_date=args.date)

    if len(returns_df) == 0:
        print("[ERROR] No sector data loaded. Aborting.")
        sys.exit(1)

    # ── Step 2: ML 对比 ──
    comparison_df = compare_with_ml_predictions(returns_df, signals_path=signals_path)

    # ── Step 3: 生成 markdown ──
    md = generate_performance_markdown(comparison_df)

    if not args.no_output:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(md, encoding="utf-8")
        print(f"[OK] Report written to {output_path}")
        print()

    # ── Step 4: 打印摘要 ──
    has_ml = comparison_df["ml_rank"].notna().any()

    # 全板块排名摘要 (top 8 + bottom 5)
    print("─" * 60)
    print(" Top 8 板块 (按今日涨跌幅):")
    print(f" {'排名':<5} {'板块':<10} {'类别':<6} {'涨跌幅%':>8} {'5日%':>8} {'20日%':>8}")
    print(" " + "-" * 55)
    for _, row in comparison_df.head(8).iterrows():
        chg = f'{row["change_pct"]:+.2f}' if row["change_pct"] is not None and not pd.isna(row["change_pct"]) else "   —"
        chg5 = f'{row["change_5d"]:+.2f}' if row["change_5d"] is not None and not pd.isna(row["change_5d"]) else "   —"
        chg20 = f'{row["change_20d"]:+.2f}' if row["change_20d"] is not None and not pd.isna(row["change_20d"]) else "   —"
        print(f" {row['actual_rank']:<5} {row['name']:<10} {CATEGORY_LABELS.get(row['category'], row['category']):<6} {chg:>8} {chg5:>8} {chg20:>8}")

    print()
    print(" Bottom 5 板块:")
    print(f" {'排名':<5} {'板块':<10} {'类别':<6} {'涨跌幅%':>8} {'5日%':>8} {'20日%':>8}")
    print(" " + "-" * 55)
    for _, row in comparison_df.tail(5).iterrows():
        chg = f'{row["change_pct"]:+.2f}' if row["change_pct"] is not None and not pd.isna(row["change_pct"]) else "   —"
        chg5 = f'{row["change_5d"]:+.2f}' if row["change_5d"] is not None and not pd.isna(row["change_5d"]) else "   —"
        chg20 = f'{row["change_20d"]:+.2f}' if row["change_20d"] is not None and not pd.isna(row["change_20d"]) else "   —"
        print(f" {row['actual_rank']:<5} {row['name']:<10} {CATEGORY_LABELS.get(row['category'], row['category']):<6} {chg:>8} {chg5:>8} {chg20:>8}")

    print()
    n_total = len(comparison_df)
    n_up = len(comparison_df[comparison_df["change_pct"] > 0]) if comparison_df["change_pct"].notna().any() else 0
    n_down = len(comparison_df[comparison_df["change_pct"] < 0]) if comparison_df["change_pct"].notna().any() else 0
    avg_all = comparison_df["change_pct"].mean() if comparison_df["change_pct"].notna().any() else 0.0
    print(f" 总计: {n_total} 板块 | 上涨: {n_up} | 下跌: {n_down} | 平均: {avg_all:+.2f}%")

    # ML 精度
    if has_ml:
        ml_top5_codes = comparison_df.attrs.get("ml_top5_codes", set())
        ml_top10_codes = comparison_df.attrs.get("ml_top10_codes", set())
        actual_top5_codes = comparison_df.attrs.get("actual_top5_codes", set())
        actual_top10_codes = comparison_df.attrs.get("actual_top10_codes", set())

        top5_hits = len(ml_top5_codes & actual_top5_codes)
        top10_hits = len(ml_top5_codes & actual_top10_codes)
        print()
        print(f" ML Top-5 预测准确度 (模型: {comparison_df.attrs.get('ml_model_name', '—')}):")
        print(f"   命中实际 Top-5:  {top5_hits}/5")
        print(f"   命中实际 Top-10: {top10_hits}/5")

        # 最大偏差
        ml_rows = comparison_df[comparison_df["ml_rank"].notna() & (comparison_df["ml_rank"] > 0)].copy()
        if len(ml_rows) > 0:
            ml_rows = ml_rows.copy()
            ml_rows["rank_diff"] = ml_rows["ml_rank"].astype(int) - ml_rows["actual_rank"].astype(int)
            worst = ml_rows.nsmallest(3, "rank_diff")
            best = ml_rows.nlargest(3, "rank_diff")
            print()
            print(" 最大高估 (ML看好但实际差):")
            for _, r in worst.iterrows():
                print(f"   {r['name']}: ML#{int(r['ml_rank'])} → 实际#{int(r['actual_rank'])} ({r['change_pct']:+.2f}%)")
            print(" 最大低估 (ML看空但实际好):")
            for _, r in best.iterrows():
                print(f"   {r['name']}: ML#{int(r['ml_rank'])} → 实际#{int(r['actual_rank'])} ({r['change_pct']:+.2f}%)")

    print()
    print("=" * 60)
    print(" Done.")


if __name__ == "__main__":
    main()