"""
P1 TiMi vs LLM 信号重叠率验证。

对比 TiMi 确定性策略选股 与 LLM Agent 实时推理选股的重叠率。
目标: 重叠率 > 70% (代表 TiMi 可替代 LLM 的大部分低频选股决策)。

验证维度:
  1. 信号重叠率 (Jaccard): TiMi ∩ LLM / TiMi ∪ LLM
  2. 方向一致性: 多空方向一致的比例
  3. 置信度相关性: TiMi confidence vs LLM 评分
  4. 策略偏好一致性: 各策略类型的触发频率对比

数据来源:
  - TiMi: timi_daily.py 输出的 timi_signals.json
  - LLM: 历史报告 reports/daily/YYYY-MM-DD-watchlist-scan.md
  - Alpha signals: alpha_signals.json (ML 排名作为 ground truth 参考)

用法:
    python scripts/p1_timi_llm_overlap.py --start 2026-10-01 --end 2026-10-15
    python scripts/p1_timi_llm_overlap.py --live  # 仅比较今天
"""

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Fix Windows GBK encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

# ─── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path("D:/AgentAlpha/my-bot")
REPORTS_DIR = PROJECT_ROOT / "reports" / "daily"
DATA_OUTPUT = PROJECT_ROOT / "data" / "output"


# ─── Data Loaders ─────────────────────────────────────────────────────────────

def load_timi_signals(start: str = None, end: str = None) -> Dict[str, List[Dict]]:
    """Load TiMi signal output by date."""
    timi_dir = DATA_OUTPUT / "timi_signals"
    timi_file = DATA_OUTPUT / "timi_signals.json"
    result = defaultdict(list)

    # Daily files
    if timi_dir.exists():
        for f in sorted(timi_dir.glob("timi_signals_*.json")):
            try:
                date_str = f.stem.replace("timi_signals_", "")
                if start and date_str < start:
                    continue
                if end and date_str > end:
                    continue
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                signals = data if isinstance(data, list) else data.get("signals", [])
                result[date_str] = signals
            except Exception:
                pass

    # Single file fallback
    if timi_file.exists() and not result:
        try:
            with open(timi_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for date_str, signals in data.get("history", {}).items():
                    if start and date_str < start:
                        continue
                    if end and date_str > end:
                        continue
                    result[date_str] = signals
                if not result:
                    date = data.get("date", "")
                    result[date] = data.get("signals", [])
        except Exception:
            pass

    return dict(result)


def extract_llm_signals(start: str = None, end: str = None) -> Dict[str, Dict]:
    """
    Parse LLM watchlist-scan reports to extract stock picks + metadata.
    Returns: {date_str: {symbols: set, directions: dict, strategies: dict}}
    """
    result = {}

    pattern_stock = re.compile(r'\b([036]\d{5})\b')
    pattern_bullish = re.compile(r'(看多|买入|加仓|增持|持有|bullish|long|buy)', re.I)
    pattern_bearish = re.compile(r'(看空|卖出|减仓|减持|空仓|bearish|short|sell)', re.I)
    pattern_strategy = re.compile(r'(T[1-6]|突破|回调|VCP|超跌|金叉|财报|资金|题材|CANSLIM)', re.I)

    for report_file in sorted(REPORTS_DIR.glob("*-watchlist-scan.md")):
        try:
            date_str = report_file.stem.replace("-watchlist-scan", "")
            if start and date_str < start:
                continue
            if end and date_str > end:
                continue

            with open(report_file, "r", encoding="utf-8") as f:
                content = f.read()

            symbols = set(pattern_stock.findall(content))
            if not symbols:
                continue

            directions = {}
            strategies = defaultdict(list)

            # Simple heuristic: stock near "看多/bullish" → long
            for sym in symbols:
                # Find context around this symbol
                idx = content.find(sym)
                if idx < 0:
                    continue
                context = content[max(0, idx - 200): idx + 200]

                is_bullish = bool(pattern_bullish.search(context))
                is_bearish = bool(pattern_bearish.search(context))

                if is_bullish and not is_bearish:
                    directions[sym] = "long"
                elif is_bearish and not is_bullish:
                    directions[sym] = "short"
                else:
                    directions[sym] = "neutral"

                # Strategy tagging
                for m in pattern_strategy.finditer(context):
                    strategies[sym].append(m.group(1))

            result[date_str] = {
                "symbols": symbols,
                "directions": directions,
                "strategies": dict(strategies),
            }

        except Exception:
            pass

    return result


def load_alpha_signals() -> Dict[str, float]:
    """Load latest ML alpha scores as reference."""
    alpha_file = DATA_OUTPUT / "alpha_signals.json"
    scores = {}
    if alpha_file.exists():
        try:
            with open(alpha_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for entry in (data if isinstance(data, list) else data.get("signals", [])):
                sym = entry.get("symbol", "")
                score = entry.get("score", entry.get("alpha_score", 0))
                if sym:
                    scores[sym] = float(score)
        except Exception:
            pass
    return scores


# ─── Overlap Computation ──────────────────────────────────────────────────────

def compute_overlap(
    timi_signals: Dict[str, List[Dict]],
    llm_signals: Dict[str, Dict],
    alpha_scores: Dict[str, float],
) -> Dict:
    """
    Compute TiMi vs LLM overlap metrics.
    """
    common_dates = sorted(set(timi_signals.keys()) & set(llm_signals.keys()))

    if not common_dates:
        return {
            "status": "NO_DATA",
            "message": "No overlapping dates between TiMi and LLM data",
            "n_dates": 0,
        }

    daily_results = []
    all_timi_symbols = set()
    all_llm_symbols = set()
    intersection_all = set()
    direction_agreement = 0
    direction_total = 0
    strategy_freq_timi = Counter()
    strategy_freq_llm = Counter()

    for date_str in common_dates:
        # TiMi symbols
        timi_syms = set()
        timi_dir = {}
        timi_strategies = {}
        for sig in timi_signals.get(date_str, []):
            if isinstance(sig, dict):
                sym = sig.get("symbol", "")
                timi_syms.add(sym)
                timi_dir[sym] = sig.get("direction", "long")
                timi_strategies[sym] = sig.get("strategy_type", "unknown")
            else:
                timi_syms.add(str(sig))

        # LLM symbols
        llm_data = llm_signals.get(date_str, {})
        llm_syms = llm_data.get("symbols", set())
        llm_dirs = llm_data.get("directions", {})

        # Overlap
        overlap = timi_syms & llm_syms
        only_timi = timi_syms - llm_syms
        only_llm = llm_syms - timi_syms

        union = timi_syms | llm_syms
        jaccard = len(overlap) / max(len(union), 1)

        # Direction agreement on overlap
        dir_agree = 0
        dir_count = 0
        for sym in overlap:
            td = timi_dir.get(sym, "long")
            ld = llm_dirs.get(sym, "neutral")
            if ld != "neutral":
                dir_count += 1
                if td == ld:
                    dir_agree += 1

        dir_agree_pct = dir_agree / max(dir_count, 1)

        # Alpha score coverage
        timi_in_alpha = sum(1 for s in timi_syms if s in alpha_scores)
        llm_in_alpha = sum(1 for s in llm_syms if s in alpha_scores)

        daily_results.append({
            "date": date_str,
            "timi_count": len(timi_syms),
            "llm_count": len(llm_syms),
            "overlap_count": len(overlap),
            "only_timi": len(only_timi),
            "only_llm": len(only_llm),
            "jaccard": round(jaccard, 4),
            "direction_agreement": round(dir_agree_pct, 4),
            "timi_alpha_coverage": round(timi_in_alpha / max(len(timi_syms), 1), 4),
            "llm_alpha_coverage": round(llm_in_alpha / max(len(llm_syms), 1), 4),
        })

        # Accumulate
        all_timi_symbols |= timi_syms
        all_llm_symbols |= llm_syms
        intersection_all |= overlap
        direction_agreement += dir_agree
        direction_total += dir_count

        for stype in timi_strategies.values():
            strategy_freq_timi[stype] += 1
        for sym in overlap:
            for st in llm_data.get("strategies", {}).get(sym, []):
                strategy_freq_llm[st] += 1

    # Aggregate metrics
    n_dates = len(common_dates)
    jaccards = [d["jaccard"] for d in daily_results]
    overlap_counts = [d["overlap_count"] for d in daily_results]

    # Pass/Fail criteria
    avg_jaccard = np.mean(jaccards) if jaccards else 0
    overall_dir_agree = direction_agreement / max(direction_total, 1)
    overall_jaccard = len(intersection_all) / max(len(all_timi_symbols | all_llm_symbols), 1)

    # Trend: is overlap improving over time?
    half = n_dates // 2
    first_half_jaccard = np.mean(jaccards[:half]) if half > 0 else 0
    second_half_jaccard = np.mean(jaccards[half:]) if half > 0 else 0
    trend = "improving" if second_half_jaccard > first_half_jaccard + 0.05 else \
            "declining" if second_half_jaccard < first_half_jaccard - 0.05 else "stable"

    # Pass gates
    checks = []
    if avg_jaccard >= 0.70:
        checks.append(("PASS", f"重叠率 {avg_jaccard:.1%} >= 70%"))
    elif avg_jaccard >= 0.50:
        checks.append(("WARN", f"重叠率 {avg_jaccard:.1%} 在 50-70% 之间"))
    else:
        checks.append(("FAIL", f"重叠率 {avg_jaccard:.1%} < 50%"))

    if overall_dir_agree >= 0.80:
        checks.append(("PASS", f"方向一致性 {overall_dir_agree:.1%} >= 80%"))
    elif overall_dir_agree >= 0.60:
        checks.append(("WARN", f"方向一致性 {overall_dir_agree:.1%} 在 60-80% 之间"))
    else:
        checks.append(("FAIL", f"方向一致性 {overall_dir_agree:.1%} < 60%"))

    if len(common_dates) >= 10:
        checks.append(("PASS", f"样本量 {n_dates} >= 10 个交易日"))
    else:
        checks.append(("WARN", f"样本量 {n_dates} < 10 个交易日，统计意义有限"))

    n_pass = sum(1 for c in checks if c[0] == "PASS")
    n_fail = sum(1 for c in checks if c[0] == "FAIL")
    overall = "PASS" if n_fail == 0 else "PARTIAL" if n_pass > 0 else "FAIL"

    return {
        "status": "OK",
        "n_dates": n_dates,
        "date_range": f"{common_dates[0]} → {common_dates[-1]}" if common_dates else "N/A",
        "overall_jaccard": round(overall_jaccard, 4),
        "avg_daily_jaccard": round(avg_jaccard, 4),
        "direction_agreement": round(overall_dir_agree, 4),
        "avg_overlap_per_day": round(np.mean(overlap_counts), 1) if overlap_counts else 0,
        "trend": trend,
        "first_half_jaccard": round(first_half_jaccard, 4),
        "second_half_jaccard": round(second_half_jaccard, 4),
        "timi_only_ratio": round(
            len(all_timi_symbols - all_llm_symbols) / max(len(all_timi_symbols | all_llm_symbols), 1), 4
        ),
        "llm_only_ratio": round(
            len(all_llm_symbols - all_timi_symbols) / max(len(all_timi_symbols | all_llm_symbols), 1), 4
        ),
        "checks": checks,
        "overall": overall,
        "strategy_freq_timi": dict(strategy_freq_timi.most_common(10)),
        "strategy_freq_llm": dict(strategy_freq_llm.most_common(10)),
        "daily_detail": daily_results[-10:],  # last 10 days
    }


# ─── Reporting ────────────────────────────────────────────────────────────────

def print_report(results: Dict):
    """Print formatted overlap report."""
    if results.get("status") == "NO_DATA":
        print(f"\n  ⚠ {results['message']}")
        return

    print(f"\n{'='*60}")
    print(f"  TiMi vs LLM 信号重叠率验证")
    print(f"  区间: {results['date_range']}  ({results['n_dates']} 交易日)")
    print(f"{'='*60}")

    print(f"\n  ── 核心指标 ──")
    print(f"  总体重叠率 (整体Jaccard):  {results['overall_jaccard']:.1%}")
    print(f"  日均重叠率 (平均Jaccard):  {results['avg_daily_jaccard']:.1%}")
    print(f"  方向一致性:                {results['direction_agreement']:.1%}")
    print(f"  日均重叠标的数:            {results['avg_overlap_per_day']:.1f}")
    print(f"  TiMi独有占比:              {results['timi_only_ratio']:.1%}")
    print(f"  LLM独有占比:               {results['llm_only_ratio']:.1%}")
    print(f"  重叠率趋势:                {results['trend']}")
    print(f"    前半段: {results['first_half_jaccard']:.1%}  →  后半段: {results['second_half_jaccard']:.1%}")

    print(f"\n  ── 达标检查 ──")
    for status, msg in results["checks"]:
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}.get(status, "?")
        print(f"  {icon} {msg}")
    print(f"\n  综合评定: {results['overall']}")

    print(f"\n  ── 策略触发频率 (TiMi) ──")
    for stype, count in results.get("strategy_freq_timi", {}).items():
        print(f"    {stype:25s}: {count:4d}")

    if results.get("daily_detail"):
        print(f"\n  ── 最近 5 天详情 ──")
        for d in results["daily_detail"][-5:]:
            bar = "█" * int(d["jaccard"] * 20) + "░" * (20 - int(d["jaccard"] * 20))
            print(f"    {d['date']}: TiMi={d['timi_count']:2d} LLM={d['llm_count']:2d} "
                  f"重叠={d['overlap_count']:2d} |{bar}| {d['jaccard']:.0%}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TiMi vs LLM 信号重叠率验证")
    parser.add_argument("--start", help="起始日期 YYYY-MM-DD")
    parser.add_argument("--end", help="结束日期 YYYY-MM-DD")
    parser.add_argument("--live", action="store_true", help="仅比较今天")
    parser.add_argument("--output", help="输出 JSON 路径")
    args = parser.parse_args()

    if args.live:
        today = datetime.now().strftime("%Y-%m-%d")
        args.start = today
        args.end = today

    print(f"\n  加载 TiMi 信号...")
    timi = load_timi_signals(args.start, args.end)
    print(f"    TiMi: {len(timi)} 个交易日")

    print(f"  解析 LLM 报告...")
    llm = extract_llm_signals(args.start, args.end)
    print(f"    LLM: {len(llm)} 个交易日")

    print(f"  加载 Alpha 评分...")
    alpha = load_alpha_signals()
    print(f"    Alpha: {len(alpha)} 只标的")

    results = compute_overlap(timi, llm, alpha)
    print_report(results)

    # Save
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = PROJECT_ROOT / "reports" / "planning" / "timi_llm_overlap.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n  结果已保存: {output_path}")


if __name__ == "__main__":
    main()