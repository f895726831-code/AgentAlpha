"""
Layer 1 板块轮动信号生成器

从 ML 模型生成最新板块信号，输出 sector_signals.json。
供 FM_trade sync_signal_factory.py 的 sync_sector_signals() 消费。

用法:
    python scripts/generate_sector_signals.py               # 最新日期
    python scripts/generate_sector_signals.py --date 2026-09-08  # 指定日期
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Fix Windows GBK encoding for emoji output
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.sector_rotation.sector_data import SW_SECTOR_CODES

DATA_DIR = Path("signal-factory/data/sector")
OUTPUT_DIR = Path("signal-factory/data/output")
HORIZON = 10
TOP_N = 5
MAX_PER_CAT = 2

SECTOR_CATEGORY = {
    "801010": "cycle", "801030": "cycle", "801040": "cycle", "801050": "cycle",
    "801080": "growth", "801110": "consumer", "801120": "consumer",
    "801130": "consumer", "801140": "consumer", "801150": "defense",
    "801160": "defense", "801170": "cycle", "801180": "cycle",
    "801200": "consumer", "801210": "consumer", "801230": "other",
    "801710": "cycle", "801720": "cycle", "801730": "growth",
    "801740": "defense", "801750": "growth", "801760": "growth",
    "801770": "growth", "801780": "financial", "801790": "financial",
    "801880": "cycle", "801890": "cycle", "801950": "cycle",
    "801960": "growth", "801970": "defense",
}


def load_prices():
    p = pd.read_csv(DATA_DIR / "sector_price_matrix.csv", index_col=0, parse_dates=True)
    p = p.ffill(limit=7)
    p = p[p.index >= "2012-01-01"]
    dead = [c for c in p.columns
            if p[c].last_valid_index() is not None
            and p[c].last_valid_index() < pd.Timestamp("2022-01-01")]
    if dead:
        p = p.drop(columns=dead)
    return p


def _validate_price_data(prices: pd.DataFrame) -> bool:
    """Check if latest price data is valid (not all zeros).

    Returns True if data looks valid, False if latest rows are all zeros
    (indicating data fetch failure).
    """
    if prices.empty:
        return False
    latest = prices.iloc[-1]
    n_zero = (latest == 0).sum()
    n_nan = latest.isna().sum()
    n_total = len(latest)
    bad_ratio = (n_zero + n_nan) / n_total
    if bad_ratio > 0.5:
        print(f"  ⚠️ Price data validation FAILED: {n_zero} zeros, {n_nan} NaN "
              f"out of {n_total} sectors ({bad_ratio:.0%})")
        return False
    return True


def compute_features(prices):
    p = prices.ffill(); r = p.pct_change()
    features = {}
    for lb, nm in [(5, "ret_1w"), (21, "ret_1m"), (63, "ret_3m"), (126, "ret_6m")]:
        features[nm] = p / p.shift(lb) - 1
    features["vol_1m"] = r.rolling(21, min_periods=21).std() * np.sqrt(252)
    features["vol_3m"] = r.rolling(63, min_periods=63).std() * np.sqrt(252)
    roll_mean = p.rolling(1260, min_periods=252).mean()
    roll_std = p.rolling(1260, min_periods=252).std()
    features["val_zscore"] = ((p - roll_mean) / roll_std).where(roll_std > 0, 0)
    ret_1m = p / p.shift(21) - 1
    features["rs_rank"] = ret_1m.rank(axis=1, pct=True)
    features["excess_1m"] = ret_1m.sub(ret_1m.mean(axis=1), axis=0)
    ma20 = p.rolling(20, min_periods=20).mean()
    ma60 = p.rolling(60, min_periods=60).mean()
    features["ma20_ratio"] = p / ma20 - 1
    features["ma60_ratio"] = p / ma60 - 1
    dfs = []
    for name, df in features.items():
        s = df.stack(); s.index.names = ["date", "sector_code"]
        dfs.append(s.to_frame(name))
    return pd.concat(dfs, axis=1)


def compute_labels(prices):
    fwd_ret = prices.shift(-HORIZON) / prices - 1
    return fwd_ret.sub(fwd_ret.mean(axis=1), axis=0)


def prepare_xy(features, labels):
    Xl, yl, dl, sl = [], [], [], []
    for date in features.index.get_level_values("date").unique():
        if date not in labels.index:
            continue
        f = features.loc[date]; l = labels.loc[date]
        for sec in f.index.intersection(l.index):
            xv, lv = f.loc[sec].values, l[sec]
            if not np.any(np.isnan(xv)) and not np.isnan(lv):
                Xl.append(xv); yl.append(lv); dl.append(date); sl.append(sec)
    return np.array(Xl), np.array(yl), pd.DatetimeIndex(dl), np.array(sl)


def select_diverse_top(sector_scores, top_n, max_per_cat):
    selected = []; cat_counts = {}
    for sec, _ in sorted(sector_scores.items(), key=lambda x: -x[1]):
        if len(selected) >= top_n:
            break
        cat = SECTOR_CATEGORY.get(sec, "other")
        if cat_counts.get(cat, 0) >= max_per_cat:
            continue
        selected.append(sec)
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    if len(selected) < top_n:
        for sec, _ in sorted(sector_scores.items(), key=lambda x: -x[1]):
            if sec not in selected:
                selected.append(sec)
            if len(selected) >= top_n:
                break
    return selected[:top_n]


def _load_previous_signals():
    """Load the last valid sector_signals.json as fallback."""
    for path in [OUTPUT_DIR / "sector_signals.json", OUTPUT_DIR / "sector_signals_latest.json"]:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                scores = [s["score"] for s in data.get("sector_signals", [])]
                if scores and max(scores) - min(scores) > 1e-8:
                    return data
            except Exception:
                continue
    return None


def generate(target_date: str = None):
    """生成板块信号 JSON"""
    print("Loading data...")
    prices = load_prices()

    # Validate price data before proceeding
    if not _validate_price_data(prices):
        print("  ⚠️ Price data appears corrupted (all zeros). Attempting fallback...")
        fallback = _load_previous_signals()
        if fallback:
            print(f"  ✅ Using previous signals from {fallback['date']}")
            return fallback
        print("  ❌ No previous signals available. Aborting.")
        return None

    if target_date is None:
        target_date = prices.index[-1].strftime("%Y-%m-%d")
    target_dt = pd.Timestamp(target_date)

    # 用截至目标日期的数据
    prices_train = prices[prices.index < target_dt]
    if len(prices_train) < 252:
        print(f"ERROR: Not enough data before {target_date}")
        return None

    print(f"Training on {prices_train.index[0].date()} ~ {prices_train.index[-1].date()} "
          f"({len(prices_train)} days)")

    print("Computing features/labels...")
    features = compute_features(prices_train)
    labels = compute_labels(prices_train)
    X, y, dates, sectors = prepare_xy(features, labels)
    print(f"  {len(X)} samples, {X.shape[1]} features")

    # 训练
    print("Training XGBoost...")
    from xgboost import XGBRegressor
    model = XGBRegressor(
        n_estimators=200, learning_rate=0.03, max_depth=5,
        reg_alpha=0.05, reg_lambda=0.05,
        subsample=0.8, colsample_bytree=0.8,
        verbosity=0,
    )
    model.fit(X, y)

    # OOS IC (用最后 30% 做快速验证)
    n = len(X)
    train_end = int(n * 0.7)
    from scipy.stats import spearmanr, pearsonr
    pred_oos = model.predict(X[train_end:])
    ic_pearson = pearsonr(pred_oos, y[train_end:])[0]
    ic_rank = spearmanr(pred_oos, y[train_end:])[0]
    print(f"  OOS IC: Pearson={ic_pearson:.4f}, Rank={ic_rank:.4f}")

    # 预测最新日期
    features_full = compute_features(prices)
    avail_dates = sorted(features_full.index.get_level_values("date").unique())
    avail_dates = pd.DatetimeIndex([d for d in avail_dates if d <= target_dt])
    if len(avail_dates) == 0:
        print(f"ERROR: No feature data available before {target_date}")
        return None

    latest_date = avail_dates[-1]
    print(f"  Target date: {target_dt.date()}, Feature dates range: "
          f"{avail_dates[0].date()} ~ {avail_dates[-1].date()} ({len(avail_dates)} dates)")
    print(f"  Predicting for date: {latest_date.date()}")
    f = features_full.loc[latest_date]
    print(f"  Feature rows: {len(f)}, NaNs per col: {f.isna().sum().sum()}")
    X_pred = f.values
    valid = ~np.any(np.isnan(X_pred), axis=1)
    if valid.sum() == 0:
        print("ERROR: No valid features for prediction")
        return None

    scores = model.predict(X_pred[valid])
    sector_codes = f.index[valid]

    # Validate scores are not all zero (indicates corrupted data)
    score_range = np.max(scores) - np.min(scores)
    if score_range < 1e-8:
        print(f"  ⚠️ All predicted scores are identical (range={score_range:.2e}). "
              f"Data may be corrupted. Falling back to previous signals...")
        fallback = _load_previous_signals()
        if fallback:
            print(f"  ✅ Using previous signals from {fallback['date']}")
            return fallback
        print("  ❌ No previous signals available. Using current (possibly invalid) signals.")

    # 构建排名
    sector_scores = dict(zip(sector_codes, scores))
    top5 = select_diverse_top(sector_scores, TOP_N, MAX_PER_CAT)
    top5_set = set(top5)

    # 完整排序
    ranked = sorted(sector_scores.items(), key=lambda x: -x[1])
    sector_signals = []
    for rank, (sec, score) in enumerate(ranked, 1):
        info = SW_SECTOR_CODES.get(sec, {})
        sector_signals.append({
            "rank": rank,
            "sector_code": sec,
            "sector_name": info.get("name", sec),
            "windcode": info.get("windcode", f"{sec}.SI"),
            "category": info.get("category", "unknown"),
            "score": round(float(score), 6),
            "selected": sec in top5_set,
        })

    # 类别统计
    cat_counts = {}
    for s in sector_signals:
        cat = s["category"]
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    # 选中的板块详情
    selected_detail = [s for s in sector_signals if s["selected"]]
    selected_cats = list(set(s["category"] for s in selected_detail))

    output = {
        "date": target_date,
        "generated_at": datetime.now().isoformat(),
        "model_name": "xgboost_top5_div",
        "pipeline_mode": "normal",
        "model_config": {
            "top_n": TOP_N,
            "max_per_category": MAX_PER_CAT,
            "features": ["ret_1w", "ret_1m", "ret_3m", "ret_6m",
                         "vol_1m", "vol_3m", "val_zscore",
                         "rs_rank", "excess_1m", "ma20_ratio", "ma60_ratio"],
            "label": "excess_2w",
            "horizon_days": HORIZON,
            "model_type": "XGBRegressor",
            "n_estimators": 200,
        },
        "model_performance": {
            "ic_pearson": round(float(ic_pearson), 4),
            "ic_rank": round(float(ic_rank), 4),
        },
        "sector_signals": sector_signals,
        "top_5_selected": selected_detail,
        "top_5_categories": selected_cats,
        "total_sectors": len(sector_signals),
        "diversity_constraint": {
            "max_per_category": MAX_PER_CAT,
            "category_counts": cat_counts,
        },
    }

    return output


def main():
    import argparse
    parser = argparse.ArgumentParser(description="生成板块轮动信号")
    parser.add_argument("--date", default=None, help="目标日期 (YYYY-MM-DD)")
    args = parser.parse_args()

    output = generate(args.date)
    if output is None:
        sys.exit(1)

    # 保存
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / "sector_signals.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    latest_path = OUTPUT_DIR / "sector_signals_latest.json"
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[OK] Sector signals saved:")
    print(f"   {out_path}")
    print(f"   {latest_path}")

    # 摘要
    print(f"\n{'='*50}")
    print(f"Top-5 Selected Sectors (diverse max{MAX_PER_CAT}/category):")
    for s in output["top_5_selected"]:
        print(f"  #{s['rank']:>2} {s['sector_name']:<6} ({s['sector_code']}) "
              f"score={s['score']:.4f}  [{s['category']}]")
    print(f"\nCategories covered: {output['top_5_categories']}")
    print(f"Model IC: Pearson={output['model_performance']['ic_pearson']:.4f}, "
          f"Rank={output['model_performance']['ic_rank']:.4f}")


if __name__ == "__main__":
    main()