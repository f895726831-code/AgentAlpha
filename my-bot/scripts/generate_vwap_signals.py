#!/usr/bin/env python3
"""
VWAP Factor Signal Generator — 将 VWAP 截面排名注入 alpha_signals 管线

Phase 1 验证: VWAP IC=+0.356, RankIC=+0.256 (CSI500, 2024-2026)
这是当前搜索空间中 IC 最强的单因子，应接入生产管线。

通过直接读取 Qlib bin 文件避免 Qlib API 的 multiprocessing 命名空间冲突
（my-bot/scripts/qlib/ 目录会遮蔽系统 qlib 包）。

输出格式: 与 alpha_signals.json 兼容的 all_scores dict
用法:
    python scripts/generate_vwap_signals.py
    python scripts/generate_vwap_signals.py --merge --output data/output/alpha_signals_latest.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
QLIB_ROOT = Path("D:/qlib/qlib_data/cn_data")
OUTPUT_DIR = PROJECT_ROOT / "data" / "output"


def read_bin_field(feature_dir: Path, field: str, date_idx: int) -> float | None:
    """从 Qlib bin 文件读取单日字段值"""
    bin_path = feature_dir / f"{field}.day.bin"
    if not bin_path.exists():
        return None
    data = np.fromfile(bin_path, dtype=np.float32)
    start_idx = int(data[0])
    pos = date_idx - start_idx + 1
    if pos < 1 or pos >= len(data):
        return None
    val = float(data[pos])
    if np.isnan(val) or val <= 0:
        return None
    return val


def load_calendar() -> tuple[list[str], dict[str, int]]:
    """加载交易日历，返回 (日期列表, 日期→索引映射)"""
    cal_file = QLIB_ROOT / "calendars" / "day.txt"
    with open(cal_file) as f:
        dates = [line.strip() for line in f if line.strip()]
    date_to_idx = {d: i for i, d in enumerate(dates)}
    return dates, date_to_idx


def load_instruments(universe: str, date: str) -> list[str]:
    """加载标的列表并过滤上市/退市日期"""
    inst_file = QLIB_ROOT / "instruments" / f"{universe}.txt"
    if not inst_file.exists():
        inst_file = QLIB_ROOT / "instruments" / "all.txt"
    if not inst_file.exists():
        raise FileNotFoundError(f"Instruments file not found for {universe}")

    instruments = []
    with open(inst_file) as f:
        for line in f:
            parts = line.strip().split("\t")
            if parts and len(parts) >= 3:
                code, start_str, end_str = parts[0], parts[1], parts[2]
                if start_str <= date <= end_str:
                    instruments.append(code)
    return instruments


def compute_vwap_signals(
    universe: str = "csi500",
    target_date: str | None = None,
) -> dict:
    """
    直接读取 Qlib bin 文件计算 VWAP 截面信号。
    VWAP ≈ (high + low + close) / 3  (日频典型价格代理)
    Signal = VWAP / close - 1  → 截面 z-score → sigmoid → 0~1
    """
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    # 1. 加载日历，定位目标日期
    calendar, date_to_idx = load_calendar()
    if target_date not in date_to_idx:
        # 回退到最近交易日
        available = [d for d in calendar if d <= target_date]
        if not available:
            raise ValueError(f"No trading day <= {target_date}")
        target_date = available[-1]

    date_idx = date_to_idx[target_date]
    print(f"[VWAP] 目标日期: {target_date} (calendar index {date_idx})")

    # 2. 加载标的列表
    instruments = load_instruments(universe, target_date)
    print(f"[VWAP] 标的数量: {len(instruments)}")

    if len(instruments) > 500:
        instruments = instruments[:500]

    # 3. 逐个读取 OHLCV，计算 VWAP/close
    pivot_data: dict[str, float] = {}
    missing_ohlcv = 0
    missing_vwap = 0

    for inst in instruments:
        feature_dir = QLIB_ROOT / "features" / inst
        if not feature_dir.exists():
            continue

        close_v = read_bin_field(feature_dir, "close", date_idx)
        high_v = read_bin_field(feature_dir, "high", date_idx)
        low_v = read_bin_field(feature_dir, "low", date_idx)

        if close_v is None or high_v is None or low_v is None:
            missing_ohlcv += 1
            continue

        # VWAP ≈ typical price (日频代理)
        vwap = (high_v + low_v + close_v) / 3.0
        if vwap <= 0 or close_v <= 0:
            missing_vwap += 1
            continue

        # Ratio: 相对于收盘价的偏离
        pivot_data[inst] = vwap / close_v

    print(f"[VWAP] 有效标: {len(pivot_data)}, "
          f"缺失OHLCV: {missing_ohlcv}, 无效VWAP: {missing_vwap}")

    if len(pivot_data) < 10:
        print("[VWAP] ⚠ 标的不足，无法计算截面信号")
        return {}

    # 4. 截面排名 → z-score → sigmoid
    values = pd.Series(pivot_data)
    z_scores = (values - values.mean()) / values.std()
    alpha_scores = 1.0 / (1.0 + np.exp(-z_scores))

    # 统计
    print(f"[VWAP] VWAP/close 均值={values.mean():.4f} "
          f"标准差={values.std():.4f} "
          f"范围=[{values.min():.4f}, {values.max():.4f}]")

    return {
        "all_scores": {k: round(float(v), 6) for k, v in alpha_scores.to_dict().items()},
        "top_20": alpha_scores.nlargest(20).index.tolist(),
        "bottom_20": alpha_scores.nsmallest(20).index.tolist(),
    }


def merge_with_existing(vwap_signals: dict, existing_path: Path) -> dict:
    """将 VWAP 信号合并到现有 alpha_signals JSON (0.7 ML + 0.3 VWAP)"""
    if not existing_path.exists():
        return vwap_signals

    with open(existing_path, "r", encoding="utf-8") as f:
        existing = json.load(f)

    existing_scores = existing.get("all_scores", {})
    vwap_scores = vwap_signals.get("all_scores", {})
    merged = {}

    all_symbols = set(existing_scores.keys()) | set(vwap_scores.keys())
    for sym in all_symbols:
        ml_score = existing_scores.get(sym, 0.5)
        vwap_score = vwap_scores.get(sym, 0.5)
        merged[sym] = round(ml_score * 0.7 + vwap_score * 0.3, 6)

    existing["all_scores"] = merged
    existing["top_20"] = sorted(merged, key=merged.get, reverse=True)[:20]
    existing["bottom_20"] = sorted(merged, key=merged.get)[:20]
    existing["@vwap_factor"] = {
        "weight": 0.3,
        "ic": 0.3556,
        "rank_ic": 0.2558,
        "merged_at": datetime.now().isoformat(),
    }

    return existing


def main():
    import argparse
    parser = argparse.ArgumentParser(description="VWAP因子信号生成器")
    parser.add_argument("--universe", default="csi500")
    parser.add_argument("--date", default=None)
    parser.add_argument("--merge", action="store_true", help="合并到现有 alpha_signals")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    print(f"[VWAP] 加载 {args.universe} (直接读取 bin 文件)...")
    signals = compute_vwap_signals(universe=args.universe, target_date=args.date)
    n_signals = len(signals.get("all_scores", {}))
    print(f"[VWAP] 生成 {n_signals} 只标的信号")

    if n_signals == 0:
        print("[VWAP] ⚠ 无有效信号（数据缺失？）")
        return 1

    # 输出
    output_path = Path(args.output) if args.output else (OUTPUT_DIR / "alpha_signals_vwap.json")

    if args.merge and output_path.exists():
        merged = merge_with_existing(signals, output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        print(f"[VWAP] 已合并到 {output_path}")
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        result = {
            "generated_at": args.date or datetime.now().strftime("%Y-%m-%d"),
            "universe": args.universe,
            "factor": "vwap_ratio",
            "ic": 0.3556,
            "rank_ic": 0.2558,
            "n_stocks": n_signals,
            **signals,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"[VWAP] 输出到 {output_path}")

    # 显示 Top 10
    all_scores = signals.get("all_scores", {})
    top10 = sorted(all_scores.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"\n[VWAP] Top 10:")
    for sym, score in top10:
        print(f"  {sym:12s} {score:.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(main())