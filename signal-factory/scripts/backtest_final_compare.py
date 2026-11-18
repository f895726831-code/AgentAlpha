"""
Layer 1 板块轮动完整对比回测
- ML Top-5 div vs 简单动量基准 (1M/3M/6M)
- 交易成本: 0.01% (万一), 单笔最低5元
- 所有策略: 多样性约束 max2/category

NOTE: 本脚本专注 ML vs 动量基准对比。如需多配置网格搜索
(Top-5/7/10 + 多样性约束变化)，请使用 backtest_sector_compare.py。
两个脚本共享 ~100 行工具函数，未来可提取到 src/backtest/sector_utils.py。
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_DIR = Path("signal-factory/data/sector")
HORIZON = 10
TRAIN_END_RATIO = 0.7
TOP_N = 5
MAX_PER_CAT = 2
PORTFOLIO_VALUE = 500_000  # 50万模拟组合
FEE_RATE = 0.00025  # 万2.5 佣金 (行业ETF无印花税)
MIN_FEE = 5.0  # 最低5元

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


def select_diverse_top(sector_scores, top_n, max_per_cat):
    """按分数排序，满足多样性约束选择 top_n 板块"""
    selected = []; cat_counts = {}
    for sec, score in sorted(sector_scores.items(), key=lambda x: -x[1]):
        if len(selected) >= top_n:
            break
        cat = SECTOR_CATEGORY.get(sec, "other")
        if cat_counts.get(cat, 0) >= max_per_cat:
            continue
        selected.append(sec)
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    # 约束太严导致不足时放宽
    if len(selected) < top_n:
        for sec, _ in sorted(sector_scores.items(), key=lambda x: -x[1]):
            if sec not in selected:
                selected.append(sec)
            if len(selected) >= top_n:
                break
    return selected[:top_n]


def calc_transaction_cost(old_sectors, new_sectors):
    """计算调仓交易成本"""
    sells = set(old_sectors) - set(new_sectors)
    buys = set(new_sectors) - set(old_sectors)
    n_trades = len(sells) + len(buys)
    if n_trades == 0:
        return 0.0
    trade_value = PORTFOLIO_VALUE / TOP_N
    fee_per_trade = max(trade_value * FEE_RATE, MIN_FEE)
    return n_trades * fee_per_trade / PORTFOLIO_VALUE  # 占组合比例


def run_backtest(test_df, strategy_name, score_func, test_dates, prices_full):
    """运行回测

    Args:
        score_func: function(date, prices_slice) → {sector_code: score}
    """
    rebalance_dates = sorted(test_dates[::HORIZON])
    cumulative = 1.0
    cum_ew = 1.0
    prev_sectors = []
    nav = []

    for date in rebalance_dates:
        # 用截至该日期的价格数据计算分数
        prices_slice = prices_full.loc[:date]
        sector_scores = score_func(date, prices_slice)
        if not sector_scores or len(sector_scores) < TOP_N:
            continue

        # 获取当日有 raw_ret 数据的板块
        day_data = test_df[test_df["date"] == date]
        avail_sectors = set(day_data["sector"].values)
        sector_scores = {s: v for s, v in sector_scores.items() if s in avail_sectors}
        if len(sector_scores) < TOP_N:
            continue

        sectors = select_diverse_top(sector_scores, TOP_N, MAX_PER_CAT)

        top_ret = day_data[day_data["sector"].isin(sectors)]["raw_ret"].mean()
        ew_ret = day_data["raw_ret"].mean()

        # 交易成本
        txn_cost = calc_transaction_cost(prev_sectors, sectors)
        net_ret = top_ret - txn_cost

        cumulative *= (1 + net_ret)
        cum_ew *= (1 + ew_ret)
        prev_sectors = sectors

        nav.append({"date": date, "cum": cumulative, "ew_cum": cum_ew,
                    "ret": net_ret, "gross_ret": top_ret, "txn_cost": txn_cost,
                    "ew_ret": ew_ret, "sectors": ",".join(sectors)})

    return pd.DataFrame(nav)


def compute_metrics(nav_df):
    if nav_df.empty:
        return None
    p = len(nav_df); total = nav_df["cum"].iloc[-1]
    years = p * HORIZON / 252
    ann_ret = total ** (1 / years) - 1 if years > 0 else 0
    prets = nav_df["cum"].pct_change().dropna()
    ann_vol = prets.std() * np.sqrt(252 / HORIZON) if len(prets) > 0 else 0
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    max_dd = ((nav_df["cum"] - nav_df["cum"].cummax()) / nav_df["cum"].cummax()).min()
    avg_txn = nav_df["txn_cost"].mean() * 10000  # bp
    return {"total_ret": total - 1, "ann_ret": ann_ret, "ann_vol": ann_vol,
            "sharpe": sharpe, "max_dd": max_dd, "avg_txn_bp": avg_txn, "nav": nav_df}


def main():
    print("Loading data...")
    prices = load_prices()
    print(f"  {prices.shape[0]} days x {prices.shape[1]} sectors")

    # ML model
    print("Training XGBoost...")
    features = compute_features(prices)
    labels = (prices.shift(-HORIZON) / prices - 1).sub(
        (prices.shift(-HORIZON) / prices - 1).mean(axis=1), axis=0)
    raw_fwd = prices.shift(-HORIZON) / prices - 1

    Xl, yl, dl, sl = [], [], [], []
    for date in features.index.get_level_values("date").unique():
        if date not in labels.index: continue
        f = features.loc[date]; l = labels.loc[date]
        for sec in f.index.intersection(l.index):
            xv, lv = f.loc[sec].values, l[sec]
            if not np.any(np.isnan(xv)) and not np.isnan(lv):
                Xl.append(xv); yl.append(lv); dl.append(date); sl.append(sec)
    X, y, dates_arr, sectors_arr = np.array(Xl), np.array(yl), pd.DatetimeIndex(dl), np.array(sl)

    n = len(X); train_end = int(n * TRAIN_END_RATIO)
    X_train, y_train = X[:train_end], y[:train_end]
    X_test, y_test = X[train_end:], y[train_end:]
    dates_test_raw = dates_arr[train_end:]
    sectors_test = sectors_arr[train_end:]

    from xgboost import XGBRegressor
    model = XGBRegressor(n_estimators=200, learning_rate=0.03, max_depth=5,
                         reg_alpha=0.05, reg_lambda=0.05,
                         subsample=0.8, colsample_bytree=0.8, verbosity=0)
    model.fit(X_train, y_train)
    pred_test = model.predict(X_test)

    from scipy.stats import spearmanr, pearsonr
    print(f"  IC: Pearson={pearsonr(pred_test, y_test)[0]:.4f}, Rank={spearmanr(pred_test, y_test)[0]:.4f}")

    # 构建 test_df
    raw_stacked = raw_fwd.stack(); raw_stacked.index.names = ["date", "sector_code"]
    valid_dates = set(raw_stacked.index.get_level_values("date").unique())
    mask = pd.Series(dates_test_raw).isin(valid_dates).values
    dates_test_raw = dates_test_raw[mask]
    sectors_test = sectors_test[mask]
    pred_test = pred_test[mask]
    y_test = y_test[mask]
    raw_test = raw_stacked.loc[dates_test_raw].reset_index()
    raw_test.columns = ["date", "sector", "raw_ret"]
    test_df = pd.DataFrame({
        "date": dates_test_raw, "sector": sectors_test, "pred": pred_test, "excess": y_test,
    }).merge(raw_test, on=["date", "sector"], how="left")

    test_dates = sorted(test_df["date"].unique())
    print(f"  Test: {test_dates[0].date()} ~ {test_dates[-1].date()} ({len(test_dates)} days)")

    prices_full = prices.ffill(limit=7)

    # ── 策略定义 ──
    strategies = {}

    # ML
    def ml_scores(date, ps):
        if date not in features.index.get_level_values("date"):
            avail = features.index.get_level_values("date")
            avail = avail[avail <= date]
            if len(avail) == 0: return {}
            date = avail[-1]
        f = features.loc[date]
        X_pred = f.values
        valid = ~np.any(np.isnan(X_pred), axis=1)
        if valid.sum() == 0: return {}
        scores = model.predict(X_pred[valid])
        return dict(zip(f.index[valid], scores))
    strategies["ML Top-5 div"] = ml_scores

    # 动量基准
    for lookback, name in [(21, "MOM-1M"), (63, "MOM-3M"), (126, "MOM-6M")]:
        def make_mom(lb):
            def mom_scores(date, ps):
                ret = ps.iloc[-1] / ps.iloc[-lb-1] - 1 if len(ps) > lb else pd.Series(dtype=float)
                return ret.dropna().to_dict()
            return mom_scores
        strategies[name] = make_mom(lookback)

    # ── 运行 ──
    print(f"\n{'='*90}")
    print(f"{'Strategy':<15} {'Total':>8} {'Ann Ret':>7} {'Sharpe':>7} {'MaxDD':>8} "
          f"{'Excess':>8} {'Txn(bp)':>8} {'Pass?':>6}")
    print(f"{'-'*90}")

    all_results = []
    for name, score_func in strategies.items():
        nav_df = run_backtest(test_df, name, score_func, test_dates, prices_full)
        if nav_df.empty:
            print(f"{name:<15} {'NO DATA':>60}")
            continue
        m = compute_metrics(nav_df)
        m["name"] = name
        all_results.append(m)

    # 等权基准
    ew_nav = all_results[0]["nav"]["ew_cum"]
    ew_total = ew_nav.iloc[-1] - 1
    years = len(ew_nav.pct_change().dropna()) * HORIZON / 252
    ew_ann = (ew_nav.iloc[-1]) ** (1 / years) - 1
    ew_vol = ew_nav.pct_change().dropna().std() * np.sqrt(252 / HORIZON)
    ew_sharpe = ew_ann / ew_vol if ew_vol > 0 else 0
    ew_dd = ((ew_nav - ew_nav.cummax()) / ew_nav.cummax()).min()

    # 打印
    ml_result = None
    for m in all_results:
        excess = m["total_ret"] - ew_total
        passes = []
        if m["sharpe"] > 0.5: passes.append("S")
        if m["max_dd"] > -0.25: passes.append("D")
        target_pass = ",".join(passes) if passes else "-"
        print(f"{m['name']:<15} {m['total_ret']:>7.1%} {m['ann_ret']:>6.1%} "
              f"{m['sharpe']:>6.2f} {m['max_dd']:>7.1%} {excess:>7.1%} "
              f"{m['avg_txn_bp']:>7.1f} {target_pass:>6}")
        if "ML" in m["name"]:
            ml_result = m

    print(f"{'EW Benchmark':<15} {ew_total:>7.1%} {ew_ann:>6.1%} "
          f"{ew_sharpe:>6.2f} {ew_dd:>7.1%}")

    # ML vs best momentum comparison
    if ml_result:
        mom_results = [m for m in all_results if "MOM" in m["name"]]
        best_mom = max(mom_results, key=lambda x: x["sharpe"])
        ml_vs_mom_sharpe = ml_result["sharpe"] - best_mom["sharpe"]
        ml_vs_mom_dd = ml_result["max_dd"] - best_mom["max_dd"]
        print(f"\nML vs Best Momentum ({best_mom['name']}):")
        print(f"  Sharpe delta: {ml_vs_mom_sharpe:+.2f}")
        print(f"  MaxDD delta:  {ml_vs_mom_dd:+.1%}")

    # ── 季度明细 ──
    if ml_result:
        nav = ml_result["nav"]
        nav["quarter"] = nav["date"].dt.to_period("Q")
        print(f"\nML Top-5 div Quarterly:")
        for q, g in nav.groupby("quarter"):
            t5 = (1 + g["ret"]).prod() - 1
            ew = (1 + g["ew_ret"]).prod() - 1
            excess = t5 - ew
            bar = "+" if excess > 0.03 else "-" if excess < -0.03 else "."
            print(f"  {q}: {t5:+6.1%} vs EW {ew:+6.1%} ({excess:+6.1%}) {bar}"
                  f"  txn={g['txn_cost'].sum()*10000:.0f}bp")

    # 保存
    if ml_result:
        ml_result["nav"].to_csv(DATA_DIR / "backtest_final_ml_top5_div.csv")
        print(f"\nSaved: {DATA_DIR / 'backtest_final_ml_top5_div.csv'}")


if __name__ == "__main__":
    main()