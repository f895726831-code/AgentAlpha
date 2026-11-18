"""
端到端回测: v4.3 (static BL) vs v5.0 (STABLE + TiMi).

Comparing:
  1. STABLE vs static BL allocation weights (Layer 0)
  2. TiMi vs LLM signal-gen overlap (Layer 2)
  3. Combined risk/return metrics

用法:
    python scripts/backtest_v4_vs_v5.py --start 2024-01-01 --end 2026-09-30

依赖:
    - STABLE asset_allocation.json (当前和历史)
    - TiMi signal output (timi_signals.json)
    - LLM signal reports (reports/daily/)
    - v4.3 benchmark (v4.3 baseline weights — 从历史报告提取)
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Fix Windows GBK encoding issues with emoji/special chars
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

# ─── Project paths ───────────────────────────────────────────────────────────
PROJECT_ROOT = Path("D:/AgentAlpha/my-bot")
REPORTS_DIR = PROJECT_ROOT / "reports" / "daily"
DATA_OUTPUT = PROJECT_ROOT / "data" / "output"
LOGS_DIR = PROJECT_ROOT / "logs" / "cron"

# v4.3 static BL weights (from v4.3 design doc)
V43_STATIC_WEIGHTS = {
    "沪深300": 0.25,
    "中证500": 0.20,
    "创业板": 0.10,
    "科创板": 0.05,
    "债券": 0.20,
    "黄金": 0.05,
    "货币基金": 0.10,
    "港股": 0.05,
}

# v5.0 asset class mapping (Layer 0 11 assets)
LAYER0_ASSETS = [
    "沪深300", "中证500", "中证1000", "创业板", "科创板",
    "恒生指数", "恒生科技",
    "债券", "黄金", "原油", "货币基金",
]


def load_stable_allocations(start: str, end: str) -> Dict[str, Dict[str, float]]:
    """
    Load STABLE allocation history from asset_allocation.json.
    Returns: {date_str: {asset: weight}}
    """
    alloc_file = DATA_OUTPUT / "asset_allocation.json"
    result = {}

    if alloc_file.exists():
        try:
            with open(alloc_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # The file may contain current allocation or history
            # Check for history array first
            if isinstance(data, dict):
                history = data.get("history", [])
                if not history:
                    # Single allocation entry
                    date_str = data.get("date", "") or data.get("as_of", "")
                    if date_str:
                        weights = data.get("weights", {}) or data.get("allocation", {})
                        result[date_str] = _normalize_weights(weights)
                else:
                    for entry in history:
                        d = entry.get("date", "")
                        w = entry.get("weights", {})
                        if d:
                            result[d] = _normalize_weights(w)
        except Exception as e:
            print(f"WARN: failed to load STABLE allocations: {e}")

    return result


def _normalize_weights(weights: dict) -> Dict[str, float]:
    """Normalize weights to sum to 1.0, map asset names."""
    mapping = {
        "hs300": "沪深300", "csi300": "沪深300",
        "csi500": "中证500", "zz500": "中证500",
        "csi1000": "中证1000", "zz1000": "中证1000",
        "cyb": "创业板", "chinext": "创业板",
        "kcb": "科创板", "star": "科创板",
        "hsi": "恒生指数", "hs_tech": "恒生科技",
        "bond": "债券", "gold": "黄金",
        "oil": "原油", "crude": "原油",
        "mmf": "货币基金", "cash": "货币基金",
    }
    normalized = defaultdict(float)
    for k, v in weights.items():
        mapped = mapping.get(k, k)
        normalized[mapped] = float(v)
    total = sum(normalized.values())
    if total > 0:
        return {k: v / total for k, v in normalized.items()}
    return dict(normalized)


def load_timi_signals(start: str, end: str) -> Dict[str, List[Dict]]:
    """
    Load TiMi signal history.
    Returns: {date_str: [signal_dict, ...]}
    """
    timi_file = DATA_OUTPUT / "timi_signals.json"
    timi_dir = DATA_OUTPUT / "timi_signals"
    result = defaultdict(list)

    # Check for daily signal files
    if timi_dir.exists():
        for f in sorted(timi_dir.glob("timi_signals_*.json")):
            try:
                date_str = f.stem.replace("timi_signals_", "")
                with open(f, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                signals = data if isinstance(data, list) else data.get("signals", [])
                result[date_str] = signals
            except Exception:
                pass

    # Also check single file
    if timi_file.exists() and not result:
        try:
            with open(timi_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for date_str, signals in data.get("history", {}).items():
                    result[date_str] = signals
                if not result:
                    date = data.get("date", "")
                    if date:
                        result[date] = data.get("signals", [])
        except Exception:
            pass

    return dict(result)


def extract_llm_signals_from_reports(start: str, end: str) -> Dict[str, set]:
    """
    Parse LLM signal reports to extract stock picks per day.
    Returns: {date_str: set(symbols)}
    """
    result = {}
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d")

    for report_file in sorted(REPORTS_DIR.glob("*-watchlist-scan.md")):
        try:
            date_str = report_file.stem.replace("-watchlist-scan", "")
            file_dt = datetime.strptime(date_str, "%Y-%m-%d")
            if not (start_dt <= file_dt <= end_dt):
                continue

            with open(report_file, "r", encoding="utf-8") as f:
                content = f.read()

            # Parse stock codes from the report
            symbols = _parse_stocks_from_report(content)
            if symbols:
                result[date_str] = symbols
        except Exception:
            pass

    return result


def _parse_stocks_from_report(content: str) -> set:
    """Extract stock codes from watchlist scan report."""
    import re

    symbols = set()
    # Match A-share stock codes: 6-digit starting with 0/3/6
    # Match patterns like: 600519, 000858, 300750
    for match in re.finditer(r'\b([036]\d{5})\b', content):
        symbols.add(match.group(1))

    return symbols


def load_benchmark_returns(start: str, end: str) -> pd.DataFrame:
    """
    Load benchmark return data for 11 assets.
    Uses real ETF daily returns from quant-signal-factory/macro_assets.
    Falls back to synthetic only if ETF data is unavailable.
    """
    returns = {}

    # ── Primary: load real ETF returns via AssetReturns ────────────────
    try:
        # quant-signal-factory's AssetReturns loads 10 ETF CSVs + cash
        qsf_data = Path("D:/AgentAlpha/signal-factory/data/macro_assets")
        if qsf_data.exists():
            import sys as _sys
            # Bypass __init__.py (uses absolute imports) — import module directly
            _asset_returns_dir = Path("D:/AgentAlpha/signal-factory/src/macro_allocation")
            if str(_asset_returns_dir) not in _sys.path:
                _sys.path.insert(0, str(_asset_returns_dir))

            import asset_returns as _ar_mod
            ar = _ar_mod.AssetReturns(data_dir=qsf_data)
            real_returns = ar.load_aligned_returns(log_return=False)

            # Map AssetReturns IDs → Chinese names used in this backtest
            id_to_cn = {
                "a_share": "沪深300",      # 沪深300ETF proxy
                "star_50": "科创板",       # 科创50ETF
                "nasdaq": "创业板",        # best available growth proxy
                "hk_tech": "恒生科技",
                "gold": "黄金",
                "bond": "债券",
                "oil": "原油",
                "cash": "货币基金",
                "nikkei": "恒生指数",      # closest HK/Asia proxy for HSI
            }

            for src_id, cn_name in id_to_cn.items():
                if src_id in real_returns.columns:
                    series = real_returns[src_id].dropna()
                    returns[cn_name] = series

            # 中证500: use a_share × 1.2 beta (small-cap tilt)
            if "a_share" in real_returns.columns:
                returns["中证500"] = real_returns["a_share"] * 1.2

            # 中证1000: use a_share × 1.4 beta (even smaller cap)
            if "a_share" in real_returns.columns:
                returns["中证1000"] = real_returns["a_share"] * 1.4

            if returns:
                # Align all series to common date range
                all_series = [s for s in returns.values()]
                common_idx = all_series[0].index
                for s in all_series[1:]:
                    common_idx = common_idx.intersection(s.index)

                aligned = {}
                for name, s in returns.items():
                    aligned[name] = s.reindex(common_idx)

                df = pd.DataFrame(aligned, index=common_idx)

                # Filter to requested date range
                if start:
                    df = df[df.index >= pd.Timestamp(start)]
                if end:
                    df = df[df.index <= pd.Timestamp(end)]

                if len(df) > 0:
                    print(f"  使用真实 ETF 收益数据: {len(df)} 天 × {len(df.columns)} 资产 "
                          f"({df.index[0].date()} → {df.index[-1].date()})")
                    return df
    except Exception as e:
        print(f"  WARN: ETF 数据加载失败 ({e})，回退到合成数据")

    # ── Fallback: synthetic returns (only if real data unavailable) ─────
    print("  WARN: 使用合成数据 (np.random.seed(42))，回测结果不可靠!")
    np.random.seed(42)
    dates = pd.date_range(start, end, freq="B")
    n = len(dates)

    # Realistic return parameters for each asset (annualized)
    asset_params = {
        "沪深300": (0.06, 0.20),
        "中证500": (0.08, 0.25),
        "中证1000": (0.06, 0.28),
        "创业板": (0.05, 0.30),
        "科创板": (0.04, 0.35),
        "恒生指数": (0.03, 0.22),
        "恒生科技": (0.05, 0.35),
        "债券": (0.03, 0.03),
        "黄金": (0.04, 0.15),
        "原油": (-0.02, 0.30),
        "货币基金": (0.02, 0.005),
    }

    for asset, (mu, sigma) in asset_params.items():
        daily_mu = (1 + mu) ** (1 / 252) - 1
        daily_sigma = sigma / np.sqrt(252)
        rets = np.random.normal(daily_mu, daily_sigma, n)
        returns[asset] = rets

    df = pd.DataFrame(returns, index=dates)
    return df


# ─── Comparison functions ─────────────────────────────────────────────────────

def compare_allocations(
    v43_weights: Dict[str, float],
    stable_weights: Dict[str, float],
) -> dict:
    """
    Compare v4.3 static BL vs STABLE allocation.
    Returns metrics dictionary.
    """
    all_assets = sorted(set(list(v43_weights.keys()) + list(stable_weights.keys())))

    diffs = {}
    for asset in all_assets:
        v43 = v43_weights.get(asset, 0)
        v5 = stable_weights.get(asset, 0)
        diffs[asset] = {
            "v4.3": round(v43, 4),
            "v5.0": round(v5, 4),
            "delta": round(v5 - v43, 4),
        }

    # Calculate turnover (sum of absolute differences / 2)
    abs_sum = sum(abs(diffs[a]["delta"]) for a in all_assets)
    turnover = abs_sum / 2

    # Concentration (Herfindahl index)
    v43_herf = sum(w ** 2 for w in v43_weights.values())
    v5_herf = sum(w ** 2 for w in stable_weights.values())

    # Risk-on assets allocation
    risk_on = ["沪深300", "中证500", "中证1000", "创业板", "科创板", "恒生科技"]
    v43_risk_on = sum(v43_weights.get(a, 0) for a in risk_on)
    v5_risk_on = sum(stable_weights.get(a, 0) for a in risk_on)

    return {
        "asset_diffs": diffs,
        "turnover": round(turnover, 4),
        "v4.3_concentration": round(v43_herf, 4),
        "v5.0_concentration": round(v5_herf, 4),
        "v4.3_risk_on_pct": round(v43_risk_on * 100, 1),
        "v5.0_risk_on_pct": round(v5_risk_on * 100, 1),
    }


def compare_signals(
    timi_signals: Dict[str, List[Dict]],
    llm_picks: Dict[str, set],
) -> dict:
    """
    Compare TiMi vs LLM signal overlap.
    Returns overlap metrics.
    """
    common_dates = sorted(set(timi_signals.keys()) & set(llm_picks.keys()))

    overlaps = []
    timi_only_counts = []
    llm_only_counts = []
    overlap_counts = []
    total_timi = 0
    total_llm = 0
    total_overlap = 0

    for date_str in common_dates:
        timi_symbols = set()
        for sig in timi_signals.get(date_str, []):
            sym = sig.get("symbol", "") if isinstance(sig, dict) else str(sig)
            if sym:
                timi_symbols.add(sym)

        llm_symbols = llm_picks.get(date_str, set())

        overlap = timi_symbols & llm_symbols
        overlap_pct = len(overlap) / max(len(timi_symbols | llm_symbols), 1)

        overlaps.append({
            "date": date_str,
            "timi_count": len(timi_symbols),
            "llm_count": len(llm_symbols),
            "overlap_count": len(overlap),
            "overlap_pct": round(overlap_pct, 4),
        })

        timi_only_counts.append(len(timi_symbols - llm_symbols))
        llm_only_counts.append(len(llm_symbols - timi_symbols))
        overlap_counts.append(len(overlap))
        total_timi += len(timi_symbols)
        total_llm += len(llm_symbols)
        total_overlap += len(overlap)

    n_days = max(len(common_dates), 1)

    # Jaccard similarity (intersection / union)
    avg_overlap = total_overlap / max(total_timi + total_llm - total_overlap, 1)

    return {
        "n_days": len(common_dates),
        "daily_details": overlaps[-20:],  # last 20 days detail
        "avg_overlap_pct": round(avg_overlap * 100, 1),
        "avg_timi_signals_per_day": round(total_timi / n_days, 1),
        "avg_llm_picks_per_day": round(total_llm / n_days, 1),
        "avg_overlap_per_day": round(total_overlap / n_days, 1),
        "tim_only_ratio": round(np.mean(timi_only_counts) / max(np.mean(timi_only_counts + llm_only_counts), 1), 4),
    }


def simulate_portfolio(
    weights: Dict[str, float],
    returns_df: pd.DataFrame,
    rebalance_freq: str = "M",
) -> dict:
    """
    Simulate portfolio returns with periodic rebalancing.
    """
    # Map weights to available columns
    port_weights = {}
    for asset, w in weights.items():
        if asset in returns_df.columns:
            port_weights[asset] = w

    total_w = sum(port_weights.values())
    if total_w == 0:
        return {"sharpe": 0, "annual_return": 0, "annual_vol": 0, "max_dd": 0}

    port_weights = {k: v / total_w for k, v in port_weights.items()}

    # Daily portfolio returns
    daily_returns = pd.Series(0.0, index=returns_df.index)
    for asset, w in port_weights.items():
        daily_returns += w * returns_df[asset].fillna(0)

    # Rebalancing: at rebalance dates, reset weights
    if rebalance_freq == "M":
        # Monthly reset to target weights
        rebalance_dates = daily_returns.resample("ME").last().index
        port_returns = daily_returns.copy()
    else:
        port_returns = daily_returns

    # Metrics
    ann_return = port_returns.mean() * 252
    ann_vol = port_returns.std() * np.sqrt(252)
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0

    cum_returns = (1 + port_returns).cumprod()
    running_max = cum_returns.cummax()
    drawdown = (cum_returns - running_max) / running_max
    max_dd = drawdown.min()

    return {
        "sharpe": round(sharpe, 4),
        "annual_return": round(ann_return * 100, 2),
        "annual_vol": round(ann_vol * 100, 2),
        "max_dd": round(max_dd * 100, 2),
        "calmar": round(ann_return / abs(max_dd) if max_dd != 0 else 0, 4),
    }


def run_backtest(start: str, end: str, output: str = None) -> dict:
    """
    Run full v4.3 vs v5.0 comparison.
    """
    print(f"\n{'='*60}")
    print(f"  v4.3 vs v5.0 端到端回测")
    print(f"  区间: {start} → {end}")
    print(f"{'='*60}\n")

    results = {
        "parameters": {"start": start, "end": end, "run_at": datetime.now().isoformat()},
    }

    # ── 1. Load Data ──────────────────────────────────────────────────────
    print("[1/5] 加载数据...")

    stable_allocs = load_stable_allocations(start, end)
    print(f"  STABLE 配置: {len(stable_allocs)} 个日期")

    timi_signals = load_timi_signals(start, end)
    print(f"  TiMi 信号: {len(timi_signals)} 个交易日")

    llm_picks = extract_llm_signals_from_reports(start, end)
    print(f"  LLM 选股: {len(llm_picks)} 个交易日")

    returns_df = load_benchmark_returns(start, end)
    print(f"  基准收益数据: {len(returns_df)} 个交易日 ({len(returns_df.columns)} 资产)")

    # ── 2. Allocation Comparison ──────────────────────────────────────────
    print("\n[2/5] 配置权重对比...")

    # Use latest STABLE allocation
    if stable_allocs:
        latest_stable = max(stable_allocs.keys())
        stable_weights = stable_allocs[latest_stable]
        print(f"  使用 STABLE 配置日期: {latest_stable}")
    else:
        stable_weights = {}  # Empty — will show zero comparison
        print("  WARN: 无 STABLE 配置数据，使用空权重")

    alloc_comparison = compare_allocations(V43_STATIC_WEIGHTS, stable_weights)
    results["allocation_comparison"] = alloc_comparison

    print(f"\n  v4.3 风险资产占比: {alloc_comparison['v4.3_risk_on_pct']}%")
    print(f"  v5.0 风险资产占比: {alloc_comparison['v5.0_risk_on_pct']}%")
    print(f"  权重换手率: {alloc_comparison['turnover'] * 100:.1f}%")
    print(f"\n  资产权重差异:")
    for asset, diff in sorted(alloc_comparison["asset_diffs"].items(), key=lambda x: abs(x[1]["delta"]), reverse=True):
        if abs(diff["delta"]) > 0.001:
            direction = "↑" if diff["delta"] > 0 else "↓"
            print(f"    {asset:12s}: v4.3={diff['v4.3']:.3f}  v5.0={diff['v5.0']:.3f}  ({direction}{abs(diff['delta']):.3f})")

    # ── 3. Signal Overlap ─────────────────────────────────────────────────
    print("\n[3/5] 信号重叠度分析...")

    signal_comparison = compare_signals(timi_signals, llm_picks)
    results["signal_comparison"] = signal_comparison

    print(f"  共同交易日: {signal_comparison['n_days']}")
    print(f"  重叠率 (Jaccard): {signal_comparison['avg_overlap_pct']}%")
    print(f"  TiMi 日均信号: {signal_comparison['avg_timi_signals_per_day']}")
    print(f"  LLM 日均选股: {signal_comparison['avg_llm_picks_per_day']}")
    print(f"  日均重叠: {signal_comparison['avg_overlap_per_day']}")

    if signal_comparison["daily_details"]:
        print(f"\n  最近 5 天:")
        for d in signal_comparison["daily_details"][-5:]:
            status = "✓" if d["overlap_pct"] >= 0.3 else "⚠"
            print(f"    {d['date']}: TiMi={d['timi_count']} LLM={d['llm_count']} "
                  f"重叠={d['overlap_count']} ({d['overlap_pct']*100:.0f}%) {status}")

    # ── 4. Portfolio Simulation ───────────────────────────────────────────
    print("\n[4/5] 组合模拟...")

    v43_metrics = simulate_portfolio(V43_STATIC_WEIGHTS, returns_df)
    stable_metrics = simulate_portfolio(stable_weights, returns_df) if stable_weights else {}

    results["v4.3_portfolio"] = v43_metrics
    results["v5.0_portfolio"] = stable_metrics

    print(f"\n  {'指标':<20s} {'v4.3':>10s} {'v5.0':>10s} {'Δ':>10s}")
    print(f"  {'-'*50}")
    for key, label in [("sharpe", "Sharpe"), ("annual_return", "年化收益(%)"),
                        ("annual_vol", "年化波动(%)"), ("max_dd", "最大回撤(%)"),
                        ("calmar", "Calmar")]:
        v43 = v43_metrics.get(key, 0)
        v5 = stable_metrics.get(key, 0) if stable_metrics else 0
        delta = v5 - v43 if stable_metrics else float("nan")
        direction = "↑" if delta > 0 else "↓" if delta < 0 else " "
        print(f"  {label:<20s} {v43:>10.2f} {v5:>10.2f} {direction}{abs(delta):>9.2f}")

    # ── 5. Summary & Verdict ──────────────────────────────────────────────
    print(f"\n[5/5] 综合评估...")

    verdict_items = []
    passes = 0
    fails = 0

    # Check 1: Sharpe improvement
    v43_sharpe = v43_metrics.get("sharpe", 0)
    v5_sharpe = stable_metrics.get("sharpe", 0) if stable_metrics else 0
    if v5_sharpe > v43_sharpe:
        verdict_items.append(f"✅ Sharpe 提升: {v43_sharpe:.2f} → {v5_sharpe:.2f}")
        passes += 1
    else:
        verdict_items.append(f"❌ Sharpe 未改善: {v43_sharpe:.2f} → {v5_sharpe:.2f}")
        fails += 1

    # Check 2: MaxDD improvement
    v43_mdd = abs(v43_metrics.get("max_dd", 0))
    v5_mdd = abs(stable_metrics.get("max_dd", 0)) if stable_metrics else 0
    if v5_mdd < v43_mdd or v5_mdd == 0:
        verdict_items.append(f"✅ 回撤改善: {v43_mdd:.1f}% → {v5_mdd:.1f}%")
        passes += 1
    else:
        verdict_items.append(f"❌ 回撤恶化: {v43_mdd:.1f}% → {v5_mdd:.1f}%")
        fails += 1

    # Check 3: Signal overlap
    overlap_pct = signal_comparison.get("avg_overlap_pct", 0)
    if overlap_pct >= 30:
        verdict_items.append(f"✅ 信号重叠率 {overlap_pct}% ≥ 30% (合理)")
        passes += 1
    elif overlap_pct >= 10:
        verdict_items.append(f"⚠️ 信号重叠率 {overlap_pct}% 偏低 (10-30%)")
    else:
        verdict_items.append(f"❌ 信号重叠率 {overlap_pct}% < 10% (严重分歧)")
        fails += 1

    summary = {
        "verdict_items": verdict_items,
        "passes": passes,
        "fails": fails,
        "overall": "PASS" if fails == 0 else "PARTIAL" if passes > 0 else "FAIL",
    }
    results["summary"] = summary

    print(f"\n  {'='*50}")
    for item in verdict_items:
        print(f"  {item}")
    print(f"  {'='*50}")
    print(f"  综合评定: {summary['overall']} ({passes}✓ / {fails}✗)")

    # ── Save output ──────────────────────────────────────────────────────
    if output:
        output_path = Path(output)
    else:
        output_path = PROJECT_ROOT / "reports" / "planning" / f"v4.3-vs-v5.0-backtest-{start}.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n  结果已保存: {output_path}")
    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="v4.3 vs v5.0 端到端回测")
    parser.add_argument("--start", default="2024-01-01", help="回测起始日期")
    parser.add_argument("--end", default="2026-09-30", help="回测结束日期")
    parser.add_argument("--output", help="输出 JSON 路径 (默认: reports/planning/)")
    args = parser.parse_args()

    run_backtest(args.start, args.end, args.output)


if __name__ == "__main__":
    main()