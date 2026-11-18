"""
Layer 1 板块轮动回测 — 多配置对比（Top-5/7/10 + 多样性约束）

对比:
  - TOP_N = 5, 7, 10
  - 多样性约束: max 2 per category (for N<=7), max 3 (for N=10)
  - vs 等权全板块基准

NOTE: 本脚本专注多配置网格搜索。如需 ML vs 动量基准对比（含交易成本），
请使用 backtest_final_compare.py。两个脚本共享 ~100 行工具函数
(load_prices/compute_features/select_diverse_top)，未来可提取到 src/backtest/sector_utils.py。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_DIR = Path("signal-factory/data/sector")
HORIZON = 10
TRAIN_END_RATIO = 0.7

# 板块类别映射
SECTOR_CATEGORY = {
    "801010": "cycle", "801020": "cycle", "801030": "cycle",
    "801040": "cycle", "801050": "cycle", "801080": "growth",
    "801110": "consumer", "801120": "consumer", "801130": "consumer",
    "801140": "consumer", "801150": "defense", "801160": "defense",
    "801170": "cycle", "801180": "cycle", "801200": "consumer",
    "801210": "consumer", "801230": "other", "801710": "cycle",
    "801720": "cycle", "801730": "growth", "801740": "defense",
    "801750": "growth", "801760": "growth", "801770": "growth",
    "801780": "financial", "801790": "financial", "801880": "cycle",
    "801890": "cycle", "801950": "cycle", "801960": "growth",
    "801970": "defense",
}


def load_prices():
    p = pd.read_csv(DATA_DIR / "sector_price_matrix.csv", index_col=0, parse_dates=True)
    p = p.ffill(limit=7)
    p = p[p.index >= "2012-01-01"]
    dead = [c for c in p.columns
            if p[c].last_valid_index() is not None
            and p[c].last_valid_index() < pd.Timestamp("2022-01-01")]
    if dead:
        print(f"  Removing dead sectors: {dead}")
        p = p.drop(columns=dead)
    return p


def compute_features(prices):
    p = prices.ffill()
    r = p.pct_change()
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
    features["ma_align"] = ((ma20 > ma60).astype(float) * 0.5
                            + (ma60 > p.rolling(120, min_periods=120).mean()).astype(float) * 0.5)
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


def select_diverse_top(day_data, top_n, max_per_cat):
    """选择 top_n 板块，每个类别最多 max_per_cat 个"""
    sorted_data = day_data.sort_values("pred", ascending=False)
    selected = []
    cat_counts = {}

    for _, row in sorted_data.iterrows():
        if len(selected) >= top_n:
            break
        sec = row["sector"]
        cat = SECTOR_CATEGORY.get(sec, "other")
        count = cat_counts.get(cat, 0)
        if count >= max_per_cat:
            continue
        selected.append(sec)
        cat_counts[cat] = count + 1

    return selected


def run_backtest(test_df, top_n, max_per_cat, label):
    """运行单次回测"""
    test_dates = sorted(test_df["date"].unique())
    rebalance_dates = test_dates[::HORIZON]
    cumulative = 1.0
    ew_cum = 1.0
    nav = []

    for date in rebalance_dates:
        day_data = test_df[test_df["date"] == date]
        if len(day_data) < top_n:
            continue

        sectors = select_diverse_top(day_data, top_n, max_per_cat)
        if len(sectors) < top_n:
            # 如果多样性约束导致不足，放宽约束
            sectors = day_data.nlargest(top_n, "pred")["sector"].values

        top_ret = day_data[day_data["sector"].isin(sectors)]["raw_ret"].mean()
        ew_ret = day_data["raw_ret"].mean()

        cumulative *= (1 + top_ret)
        ew_cum *= (1 + ew_ret)

        nav.append({"date": date, "cum": cumulative, "ew_cum": ew_cum,
                    "ret": top_ret, "ew_ret": ew_ret,
                    "sectors": ",".join(sectors)})

    nav_df = pd.DataFrame(nav)
    if nav_df.empty:
        return None

    periods = len(nav_df)
    total = nav_df["cum"].iloc[-1]
    years = periods * HORIZON / 252
    ann_ret = total ** (1 / years) - 1 if years > 0 else 0
    period_rets = nav_df["cum"].pct_change().dropna()
    ann_vol = period_rets.std() * np.sqrt(252 / HORIZON) if len(period_rets) > 0 else 0
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    max_dd = ((nav_df["cum"] - nav_df["cum"].cummax()) / nav_df["cum"].cummax()).min()

    ew_total = nav_df["ew_cum"].iloc[-1]
    ew_ann = ew_total ** (1 / years) - 1 if years > 0 else 0
    ew_prets = nav_df["ew_cum"].pct_change().dropna()
    ew_vol = ew_prets.std() * np.sqrt(252 / HORIZON) if len(ew_prets) > 0 else 0
    ew_sharpe = ew_ann / ew_vol if ew_vol > 0 else 0
    ew_dd = ((nav_df["ew_cum"] - nav_df["ew_cum"].cummax()) / nav_df["ew_cum"].cummax()).min()

    # 类别多样性
    all_cats = []
    for _, row in nav_df.iterrows():
        cats = [SECTOR_CATEGORY.get(s, "other") for s in str(row["sectors"]).split(",")]
        all_cats.append(len(set(cats)))
    avg_cats = np.mean(all_cats) if all_cats else 0

    return {
        "label": label, "top_n": top_n, "max_per_cat": max_per_cat,
        "total_ret": total - 1, "ann_ret": ann_ret, "ann_vol": ann_vol,
        "sharpe": sharpe, "max_dd": max_dd,
        "ew_total": ew_total - 1, "ew_ann": ew_ann, "ew_sharpe": ew_sharpe,
        "excess": total - ew_total,
        "avg_categories": avg_cats,
        "nav": nav_df,
    }


def main():
    print("Loading...")
    prices = load_prices()
    print(f"  {prices.shape[0]} days x {prices.shape[1]} sectors")

    print("Computing features/labels...")
    features = compute_features(prices)
    labels = compute_labels(prices)
    raw_fwd = prices.shift(-HORIZON) / prices - 1
    X, y, dates, sectors = prepare_xy(features, labels)
    print(f"  {len(X)} samples, {X.shape[1]} features")

    n = len(X)
    train_end = int(n * TRAIN_END_RATIO)
    X_train, y_train = X[:train_end], y[:train_end]
    X_test, y_test = X[train_end:], y[train_end:]
    dates_test = dates[train_end:]
    sectors_test = sectors[train_end:]
    print(f"  Train: {len(X_train)}, Test: {len(X_test)}")
    print(f"  Test: {dates_test[0].date()} ~ {dates_test[-1].date()}")

    print("Training XGBoost...")
    from xgboost import XGBRegressor
    model = XGBRegressor(
        n_estimators=200, learning_rate=0.03, max_depth=5,
        reg_alpha=0.05, reg_lambda=0.05,
        subsample=0.8, colsample_bytree=0.8, verbosity=0,
    )
    model.fit(X_train, y_train)
    pred_test = model.predict(X_test)

    from scipy.stats import spearmanr, pearsonr
    ic = {"pearson": pearsonr(pred_test, y_test)[0],
          "rank": spearmanr(pred_test, y_test)[0]}
    print(f"  IC: Pearson={ic['pearson']:.4f}, Rank={ic['rank']:.4f}")

    # 构建 test_df
    raw_actual = raw_fwd.stack()
    raw_actual.index.names = ["date", "sector_code"]
    raw_test = raw_actual.loc[dates_test].reset_index()
    raw_test.columns = ["date", "sector", "raw_ret"]
    test_df = pd.DataFrame({
        "date": dates_test, "sector": sectors_test,
        "pred": pred_test, "excess": y_test,
    }).merge(raw_test, on=["date", "sector"], how="left")

    # ── 运行多配置对比 ──
    configs = [
        (5, 2, "Top-5 div"),
        (7, 2, "Top-7 div"),
        (7, 99, "Top-7 raw"),
        (10, 3, "Top-10 div"),
        (10, 99, "Top-10 raw"),
    ]

    print(f"\n{'='*80}")
    print(f"{'Config':<15} {'Total Ret':>9} {'Ann Ret':>8} {'Sharpe':>7} {'MaxDD':>8} {'Excess':>9} {'Categories':>10}")
    print(f"{'-'*80}")

    results = []
    for top_n, max_cat, label in configs:
        r = run_backtest(test_df, top_n, max_cat, label)
        if r:
            results.append(r)
            print(f"{label:<15} {r['total_ret']:>8.1%} {r['ann_ret']:>7.1%} "
                  f"{r['sharpe']:>6.2f} {r['max_dd']:>7.1%} {r['excess']:>8.1%} "
                  f"{r['avg_categories']:>9.1f}")

    # 基准
    ew_r = results[0]["ew_total"]
    ew_sharpe = results[0]["ew_sharpe"]
    print(f"{'EW Benchmark':<15} {ew_r:>8.1%} {'':>7} {ew_sharpe:>6.2f}")

    # 最佳结果详情
    best = max(results, key=lambda x: x["sharpe"])
    print(f"\nBest: {best['label']} (Sharpe={best['sharpe']:.2f}, MaxDD={best['max_dd']:.1%})")

    # 类别多样性详情
    print(f"\nDiversity comparison (avg distinct categories per period):")
    for r in results:
        print(f"  {r['label']:<15}: {r['avg_categories']:.1f} / {r['top_n']} sectors")
        # Top sectors chosen
        all_sectors = []
        for _, row in r["nav"].iterrows():
            all_sectors.extend(str(row["sectors"]).split(","))
        top_picks = pd.Series(all_sectors).value_counts().head(10)
        picks_str = ", ".join(f"{s}({c})" for s, c in top_picks.items())
        print(f"    Favorites: {picks_str}")

    # 保存最佳
    best["nav"].to_csv(DATA_DIR / "backtest_sector_best.csv")
    print(f"\nBest NAV saved: {DATA_DIR / 'backtest_sector_best.csv'}")


if __name__ == "__main__":
    main()