"""
三模型对比基线脚本 — LightGBM vs XGBoost vs CatBoost

支持多特征集对比:
    python scripts/compare_models.py                        # 默认 23 特征
    python scripts/compare_models.py --feature-set 158      # 全部 158 特征
    python scripts/compare_models.py --feature-set 360      # Alpha360 特征
    python scripts/compare_models.py --feature-set all      # 三组特征集全部对比

用法:
    python scripts/compare_models.py
    python scripts/compare_models.py --output data/output/model_comparison.json
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 修复 MLflow 文件存储后端被禁用的问题
os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

import numpy as np
import pandas as pd
import qlib
from qlib.config import C
from qlib.data import D
from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP
from qlib.contrib.data.handler import Alpha158, Alpha360
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.model.xgboost import XGBModel
from qlib.workflow import R
from qlib.utils import init_instance_by_config

# ─── 配置 ───

PROVIDER_URI = "D:/qlib/qlib_data/cn_data"
OUTPUT_DIR = Path("signal-factory/data/output/models")

MODELS = {
    "LightGBM": {
        "class": LGBModel,
        "kwargs": {
            "loss": "mse",
            "colsample_bytree": 0.8879,
            "learning_rate": 0.2,
            "subsample": 0.8789,
            "lambda_l1": 205.6999,
            "lambda_l2": 580.9768,
            "max_depth": 8,
            "num_leaves": 210,
            "num_threads": 20,
        },
    },
    "XGBoost": {
        "class": XGBModel,
        "kwargs": {
            "objective": "reg:squarederror",
            "colsample_bytree": 0.8,
            "learning_rate": 0.1,
            "subsample": 0.8,
            "max_depth": 6,
            "n_estimators": 500,
            "nthread": 20,
            "seed": 42,
        },
    },
    "CatBoost": {
        "class": None,  # 动态导入
        "module": "qlib.contrib.model.catboost_model",
        "class_name": "CatBoostModel",
        "kwargs": {
            "loss": "RMSE",
            "colsample_bylevel": 0.8,
            "learning_rate": 0.1,
            "subsample": 0.8,
            "max_depth": 6,
            "iterations": 500,
            "thread_count": 20,
            "random_seed": 42,
        },
    },
}

# ── 23 精选特征（当前基线） ──
SELECTED_23 = [
    "RESI5", "WVMA5", "RSQR5", "KLEN", "RSQR10", "CORR5", "CORR10",
    "CORD5", "CORD10", "CORD60",
    "ROC60", "RESI10", "VSTD5", "RSQR60", "CORR60", "WVMA60", "STD5",
    "RSQR20", "CORR20", "KLOW", "SUMP5", "SUMN5", "SUMD5",
]

# ── 特征集定义 ──
FEATURE_SETS = {
    "23": {
        "label": "Alpha158 (23 selected)",
        "handler_class": Alpha158,
        "infer_processors": [
            {
                "class": "FilterCol",
                "kwargs": {"fields_group": "feature", "col_list": SELECTED_23},
            },
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
    },
    "158": {
        "label": "Alpha158 (158 features)",
        "handler_class": Alpha158,
        "infer_processors": [
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
    },
    "360": {
        "label": "Alpha360 (360 features)",
        "handler_class": Alpha360,
        "infer_processors": [
            {"class": "ProcessInf", "kwargs": {}},
            {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True}},
            {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
        ],
    },
}


def build_handler_config(feature_set: str) -> dict:
    """根据特征集名称构建 DataHandler 配置"""
    from src.factors.model_configs import get_dynamic_dates
    _end_time, _fit_end_time = get_dynamic_dates()
    fs = FEATURE_SETS[feature_set]
    return {
        "start_time": "2015-01-01",
        "end_time": _end_time,
        "fit_start_time": "2015-01-01",
        "fit_end_time": _fit_end_time,
        "instruments": "csi500",
        "infer_processors": fs["infer_processors"],
        "learn_processors": [
            {"class": "DropnaLabel"},
            {"class": "CSRankNorm", "kwargs": {"fields_group": "label"}},
        ],
        "label": ["Ref($close, -5) / $close - 1"],
    }


def _build_segments() -> dict:
    """动态构建 train/valid/test 分段"""
    from src.factors.model_configs import get_dynamic_dates
    _end_time, _fit_end_time = get_dynamic_dates()
    return {
        "train": ("2015-01-01", _fit_end_time),
        "valid": (_fit_end_time, _end_time),
        "test": (_fit_end_time, _end_time),
    }


SEGMENTS = _build_segments()


def import_catboost():
    """动态导入 CatBoost 模型（避免 catboost 未安装时导入失败）"""
    try:
        from qlib.contrib.model.catboost_model import CatBoostModel
        return CatBoostModel
    except ImportError:
        print("[WARN] CatBoost not installed, skipping CatBoost comparison")
        return None


def _safe_skew(arr):
    """安全计算偏度"""
    try:
        from scipy.stats import skew
        return skew(arr)
    except Exception:
        return np.nan


def _normalize_symbol(sym: str) -> str:
    """统一标的代码格式: 去掉 sh/sz 前缀"""
    if isinstance(sym, str) and (sym.startswith("sh") or sym.startswith("sz")):
        return sym[2:]
    return sym


def load_industry_map(csv_path: str = None, instruments: str = "csi500") -> dict:
    """加载申万行业分类映射: qlib_symbol -> industry_name

    参数:
        csv_path: 行业 CSV 路径，None 时根据 instruments 自动选择
        instruments: 标的范围 ("csi500" 或 "csi300")
    """
    if csv_path is None:
        if instruments == "csi300":
            csv_path = "signal-factory/data/industry/sw_industry_csi300.csv"
        else:
            csv_path = "signal-factory/data/industry/sw_industry.csv"
    df = pd.read_csv(csv_path, dtype=str)
    industry_map = {}
    for _, row in df.iterrows():
        sym = _normalize_symbol(row["qlib_symbol"])
        industry_map[sym] = row["industry"]
    return industry_map


def neutralize_industry(pred, industry_map: dict) -> pd.Series:
    """
    截面行业中性化: 每个交易日，按行业分组，减去行业均值
    保留个股 alpha，消除行业 beta
    """
    # pred 可能是 pd.Series (MultiIndex) 或 numpy array
    if hasattr(pred, "values"):
        pred_series = pd.Series(pred.values.flatten(), index=pred.index)
    else:
        pred_series = pd.Series(np.array(pred).flatten())

    # 将 MultiIndex (datetime, instrument) 转为 DataFrame
    df = pred_series.reset_index()
    df.columns = ["datetime", "instrument", "pred"]

    # 映射行业（统一符号格式）
    df["instrument_norm"] = df["instrument"].apply(_normalize_symbol)
    df["industry"] = df["instrument_norm"].map(industry_map)
    df = df.drop(columns=["instrument_norm"])
    before_drop = len(df)
    df = df.dropna(subset=["industry"])
    after_drop = len(df)
    if before_drop > after_drop:
        print(f"  [行业中性化] {before_drop - after_drop} 只标的无行业分类，已排除")

    # 按日期+行业分组，减去行业均值
    industry_mean = df.groupby(["datetime", "industry"])["pred"].transform("mean")
    df["pred_neutral"] = df["pred"] - industry_mean

    # 重建 MultiIndex Series
    result = df.set_index(["datetime", "instrument"])["pred_neutral"]
    return result


def neutralize_industry_regression(pred, industry_map: dict, add_intercept: bool = True) -> pd.Series:
    """
    截面行业中性化 — 多元线性回归残差法（Barra 标准做法）

    对每个交易日截面:
        pred_i = α + Σ(β_j × industry_dummy_j) + ε_i

    残差 ε_i 即为行业中性化后的 alpha。

    与组内去均值法的区别:
        - 回归残差法通过 OLS 估计行业 beta，大市值公司对系数影响更大
        - 可处理行业暴露不均（某些行业股票多/少）的情况
        - 是 Barra 风险模型的标准做法

    参数:
        pred: 预测值 Series (MultiIndex: datetime, instrument)
        industry_map: {qlib_symbol: industry_name}
        add_intercept: 是否包含截距项（True = 剥离行业 + 市场 beta）
    """
    from sklearn.linear_model import LinearRegression

    if hasattr(pred, "values"):
        pred_series = pd.Series(pred.values.flatten(), index=pred.index)
    else:
        pred_series = pd.Series(np.array(pred).flatten())

    df = pred_series.reset_index()
    df.columns = ["datetime", "instrument", "pred"]
    df["instrument_norm"] = df["instrument"].apply(_normalize_symbol)
    df["industry"] = df["instrument_norm"].map(industry_map)
    df = df.drop(columns=["instrument_norm"])
    before_drop = len(df)
    df = df.dropna(subset=["industry"])
    after_drop = len(df)
    if before_drop > after_drop:
        print(f"  [回归残差法] {before_drop - after_drop} 只标的无行业分类，已排除")

    results = []

    for date, group in df.groupby("datetime"):
        # 构造行业虚拟变量（drop_first 避免共线性）
        dummies = pd.get_dummies(group["industry"], drop_first=True)
        X = dummies.values.astype(float)

        # 可选：加入截距（剥离市场 beta）
        # sklearn LinearRegression 默认有截距，无需手动加

        y = group["pred"].values

        # 至少需要 2 个行业才能回归
        n_industries = dummies.shape[1]
        n_stocks = len(y)
        if n_stocks < 10 or n_industries < 2:
            # 样本太少，退化为组内去均值
            group_mean = group.groupby("industry")["pred"].transform("mean")
            residuals = group["pred"].values - group_mean.values
        else:
            try:
                model = LinearRegression(fit_intercept=add_intercept)
                model.fit(X, y)
                y_pred = model.predict(X)
                residuals = y - y_pred
            except Exception:
                # 回归失败，退化为组内去均值
                group_mean = group.groupby("industry")["pred"].transform("mean")
                residuals = group["pred"].values - group_mean.values

        group_copy = group.copy()
        group_copy["pred_neutral"] = residuals
        results.append(group_copy)

    if not results:
        return pd.Series(dtype=float)

    df_result = pd.concat(results)
    result = df_result.set_index(["datetime", "instrument"])["pred_neutral"]
    return result


def _calc_ic(pred, dataset) -> dict:
    """计算 IC 统计量（按日截面 Rank IC）"""
    # DK_L 返回的 DataFrame 有 MultiIndex 列: ('label', '<表达式>')
    df_test = dataset.prepare("test", col_set=["feature", "label"], data_key=DataHandlerLP.DK_L)
    if df_test is None or df_test.empty:
        return {}

    # 通过 MultiIndex 访问 label 列
    label_cols = df_test.xs("label", axis=1, level=0)
    labels = label_cols.iloc[:, 0]

    if hasattr(pred, "values"):
        pred_series = pd.Series(pred.values.flatten(), index=pred.index)
    else:
        pred_series = pd.Series(np.array(pred).flatten())

    # 对齐索引
    common_idx = labels.index.intersection(pred_series.index)
    labels = labels.loc[common_idx]
    pred_series = pred_series.loc[common_idx]

    # 按日期截面计算 Rank IC (Spearman)
    df_ic = pd.DataFrame({"label": labels, "pred": pred_series})
    daily_ic = df_ic.groupby(level="datetime").apply(
        lambda g: g["pred"].corr(g["label"], method="spearman")
    )
    daily_ic = daily_ic.dropna()

    ic_mean = float(daily_ic.mean())
    ic_std = float(daily_ic.std())
    ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0

    # Pearson IC (截面)
    daily_pearson = df_ic.groupby(level="datetime").apply(
        lambda g: g["pred"].corr(g["label"], method="pearson")
    )
    daily_pearson = daily_pearson.dropna()
    pearson_ic_mean = float(daily_pearson.mean())

    return {
        "ic_mean": round(ic_mean, 6),
        "ic_std": round(ic_std, 6),
        "ic_ir": round(ic_ir, 4),
        "pearson_ic_mean": round(pearson_ic_mean, 6),
        "n_days": len(daily_ic),
    }


def _rank_models(results: list) -> list:
    """按 IC 表现排名"""
    scored = []
    for r in results:
        ic = r.get("ic_mean", 0) or 0
        icir = r.get("ic_ir", 0) or 0
        score = abs(ic) * 0.5 + abs(icir) * 0.5
        scored.append({
            "model": r["model"],
            "ic_mean": r.get("ic_mean"),
            "ic_ir": r.get("ic_ir"),
            "composite_score": round(score, 6),
        })

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    return scored


def train_and_predict(model_name: str, model_config: dict, dataset: DatasetH, industry_map: dict = None) -> dict:
    """训练模型并生成预测信号（共用 dataset，避免重复加载数据）"""
    print(f"\n{'='*60}")
    print(f"[{model_name}] 开始训练...")
    t0 = time.time()

    # 创建模型
    if model_name == "CatBoost":
        CatBoostModel = import_catboost()
        if CatBoostModel is None:
            return {"model": model_name, "status": "skipped", "error": "CatBoost not installed"}
        model = CatBoostModel(**model_config["kwargs"])
    else:
        model = model_config["class"](**model_config["kwargs"])

    # 训练
    try:
        model.fit(dataset)
    except Exception as e:
        print(f"[{model_name}] 训练失败: {e}")
        return {"model": model_name, "status": "error", "error": str(e)}

    train_time = time.time() - t0
    print(f"[{model_name}] 训练完成 ({train_time:.1f}s)")

    # 预测
    t0 = time.time()
    try:
        pred = model.predict(dataset)
    except Exception as e:
        print(f"[{model_name}] 预测失败: {e}")
        return {"model": model_name, "status": "error", "error": str(e)}

    pred_time = time.time() - t0
    print(f"[{model_name}] 预测完成 ({pred_time:.1f}s), 样本数: {len(pred)}")

    # 信号统计
    if hasattr(pred, "values"):
        pred_values = pred.values.flatten() if pred.values.ndim > 1 else pred.values
    else:
        pred_values = pred

    stats = {
        "model": model_name,
        "status": "success",
        "train_time_s": round(train_time, 1),
        "pred_time_s": round(pred_time, 1),
        "n_samples": len(pred_values),
        "pred_mean": float(np.mean(pred_values)),
        "pred_std": float(np.std(pred_values)),
        "pred_min": float(np.min(pred_values)),
        "pred_max": float(np.max(pred_values)),
        "pred_skew": float(_safe_skew(pred_values)),
    }

    # IC 分析（原始预测）
    try:
        ic_result = _calc_ic(pred, dataset)
        stats.update(ic_result)
    except Exception as e:
        print(f"  [WARN] IC 计算失败: {e}")

    print(f"  IC Mean: {stats.get('ic_mean', 'N/A')}")
    print(f"  IC IR: {stats.get('ic_ir', 'N/A')}")

    # IC 分析（行业中性化后）
    if industry_map is not None:
        try:
            pred_neutral = neutralize_industry(pred, industry_map)
            ic_neutral = _calc_ic(pred_neutral, dataset)
            stats["ic_mean_neutral"] = ic_neutral.get("ic_mean")
            stats["ic_ir_neutral"] = ic_neutral.get("ic_ir")
            stats["n_days_neutral"] = ic_neutral.get("n_days")
            print(f"  IC Mean (组内去均值): {stats.get('ic_mean_neutral', 'N/A')}")
            print(f"  IC IR   (组内去均值): {stats.get('ic_ir_neutral', 'N/A')}")
        except Exception as e:
            print(f"  [WARN] 组内去均值法 IC 计算失败: {e}")

        try:
            pred_reg = neutralize_industry_regression(pred, industry_map)
            ic_reg = _calc_ic(pred_reg, dataset)
            stats["ic_mean_reg"] = ic_reg.get("ic_mean")
            stats["ic_ir_reg"] = ic_reg.get("ic_ir")
            stats["n_days_reg"] = ic_reg.get("n_days")
            print(f"  IC Mean (回归残差法): {stats.get('ic_mean_reg', 'N/A')}")
            print(f"  IC IR   (回归残差法): {stats.get('ic_ir_reg', 'N/A')}")
        except Exception as e:
            print(f"  [WARN] 回归残差法 IC 计算失败: {e}")

    # MLflow 记录（可选，失败不影响主流程）
    try:
        with R.start(experiment_name=f"model_compare_{model_name}"):
            R.log_params(**model_config["kwargs"])
            R.log_metrics(train_time=train_time, pred_time=pred_time)
            record = R.get_recorder()
            record.save_objects(**{f"{model_name}_pred": pred})
    except Exception as e:
        print(f"  [INFO] MLflow 记录跳过: {e}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="三模型对比基线")
    parser.add_argument("--feature-set", choices=["23", "158", "360", "all"], default="23",
                        help="特征集: 23(精选), 158(全部Alpha158), 360(Alpha360), all(全部对比)")
    parser.add_argument("--models", nargs="+", choices=["LightGBM", "XGBoost", "CatBoost"],
                        default=["LightGBM", "XGBoost", "CatBoost"],
                        help="要对比的模型 (默认全部)")
    parser.add_argument("--output", default=None, help="输出 JSON 路径")
    args = parser.parse_args()

    feature_sets_to_run = ["23", "158", "360"] if args.feature_set == "all" else [args.feature_set]

    print("=" * 60)
    print("三模型对比基线: LightGBM vs XGBoost vs CatBoost")
    print(f"数据: {PROVIDER_URI}")
    print(f"特征集: {', '.join(FEATURE_SETS[fs]['label'] for fs in feature_sets_to_run)}")
    print(f"模型: {', '.join(args.models)}")
    print(f"训练: {SEGMENTS['train'][0]} ~ {SEGMENTS['train'][1]}")
    print(f"验证: {SEGMENTS['valid'][0]} ~ {SEGMENTS['valid'][1]}")
    print(f"测试: {SEGMENTS['test'][0]} ~ {SEGMENTS['test'][1]}")
    print("=" * 60)

    # 初始化 Qlib（只做一次）
    qlib.init(provider_uri=PROVIDER_URI, region="cn")

    # 加载行业分类（只做一次，默认 CSI 500）
    print("\n[初始化] 加载申万行业分类...")
    try:
        industry_map = load_industry_map(instruments="csi500")
        print(f"[初始化] 行业分类: {len(set(industry_map.values()))} 个行业, {len(industry_map)} 只标的")
    except Exception as e:
        print(f"[初始化] 行业分类加载失败: {e}，跳过行业中性化")
        industry_map = None

    # 过滤模型
    models_to_run = {k: v for k, v in MODELS.items() if k in args.models}

    all_results = {}  # feature_set -> [model_results]

    for fs_key in feature_sets_to_run:
        fs_info = FEATURE_SETS[fs_key]
        handler_config = build_handler_config(fs_key)

        print(f"\n{'#'*60}")
        print(f"# 特征集: {fs_info['label']}")
        print(f"{'#'*60}")

        # 创建 DataHandler + Dataset
        print(f"\n[初始化] 加载 {fs_info['label']} 数据...")
        t0 = time.time()
        handler = fs_info["handler_class"](**handler_config)
        dataset = DatasetH(handler=handler, segments=SEGMENTS)
        print(f"[初始化] 数据加载完成 ({time.time() - t0:.1f}s)")

        results = []
        for model_name, config in models_to_run.items():
            result = train_and_predict(model_name, config, dataset, industry_map)
            results.append(result)

        all_results[fs_key] = results

        # 本组汇总
        print(f"\n{'='*60}")
        print(f"[{fs_info['label']}] 模型对比汇总")
        _print_summary(results, industry_map is not None)

    # 跨特征集对比（仅当跑了多组时）
    if len(feature_sets_to_run) > 1:
        _print_cross_comparison(all_results, industry_map is not None)

    # 保存结果
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = Path(args.output) if args.output else OUTPUT_DIR / "model_comparison.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "feature_sets": list(feature_sets_to_run),
            "results": all_results,
            "config": {
                "segments": SEGMENTS,
                "label": "Ref($close, -5) / $close - 1",
            },
        }, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n结果已保存: {output_path}")


def _print_summary(results, has_neutral=True):
    """打印单组模型对比汇总"""
    print("-" * 80)
    header = f"{'模型':<12} {'IC Mean':>8} {'IC IR':>7}"
    if has_neutral:
        header += f" {'IC(中性)':>8} {'IR(中性)':>7}"
    header += f" {'训练':>7}"
    print(header)
    print("-" * 80)

    for r in results:
        status = r.get("status", "unknown")
        ic = r.get("ic_mean", "—")
        icir = r.get("ic_ir", "—")
        train_t = r.get("train_time_s", "—")
        ic_str = f"{ic:.6f}" if isinstance(ic, (int, float)) else str(ic)
        icir_str = f"{icir:.4f}" if isinstance(icir, (int, float)) else str(icir)
        train_str = f"{train_t:.1f}s" if isinstance(train_t, (int, float)) else str(train_t)

        line = f"{r['model']:<12} {ic_str:>8} {icir_str:>7}"
        if has_neutral:
            ic_m = r.get("ic_mean_neutral", "—")
            icir_m = r.get("ic_ir_neutral", "—")
            ic_m_str = f"{ic_m:.6f}" if isinstance(ic_m, (int, float)) else str(ic_m)
            icir_m_str = f"{icir_m:.4f}" if isinstance(icir_m, (int, float)) else str(icir_m)
            line += f" {ic_m_str:>8} {icir_m_str:>7}"
        line += f" {train_str:>7}"
        print(line)

    print("-" * 80)

    # 排名
    success = [r for r in results if r.get("status") == "success"]
    if len(success) >= 2:
        ranking = _rank_models(success)
        print("\n综合排名 (IC + ICIR):")
        for i, m in enumerate(ranking, 1):
            print(f"  {i}. {m['model']} — composite: {m['composite_score']:.6f}")


def _print_cross_comparison(all_results: dict, has_neutral=True):
    """打印跨特征集对比"""
    print(f"\n{'#'*60}")
    print("# 跨特征集对比 (按模型分组)")
    print(f"{'#'*60}")

    for model_name in MODELS:
        print(f"\n--- {model_name} ---")
        header = f"{'特征集':<20} {'IC Mean':>8} {'IC IR':>7}"
        if has_neutral:
            header += f" {'IC(中性)':>8} {'IR(中性)':>7}"
        header += f" {'训练':>7}"
        print(header)
        print("-" * 70)

        for fs_key in ["23", "158", "360"]:
            if fs_key not in all_results:
                continue
            fs_label = FEATURE_SETS[fs_key]["label"]
            for r in all_results[fs_key]:
                if r.get("model") != model_name:
                    continue
                ic = r.get("ic_mean", "—")
                icir = r.get("ic_ir", "—")
                train_t = r.get("train_time_s", "—")
                ic_str = f"{ic:.6f}" if isinstance(ic, (int, float)) else str(ic)
                icir_str = f"{icir:.4f}" if isinstance(icir, (int, float)) else str(icir)
                train_str = f"{train_t:.1f}s" if isinstance(train_t, (int, float)) else str(train_t)

                line = f"{fs_label:<20} {ic_str:>8} {icir_str:>7}"
                if has_neutral:
                    ic_m = r.get("ic_mean_neutral", "—")
                    icir_m = r.get("ic_ir_neutral", "—")
                    ic_m_str = f"{ic_m:.6f}" if isinstance(ic_m, (int, float)) else str(ic_m)
                    icir_m_str = f"{icir_m:.4f}" if isinstance(icir_m, (int, float)) else str(icir_m)
                    line += f" {ic_m_str:>8} {icir_m_str:>7}"
                line += f" {train_str:>7}"
                print(line)
        print("-" * 70)


if __name__ == "__main__":
    main()