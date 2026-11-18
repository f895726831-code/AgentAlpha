"""
AgentAlpha Alpha 信号生成 — 桥接层 (底层: signal-factory)

从 QSF src/factors/alpha_signals.py 导入 AlphaSignalGenerator，保持 AgentAlpha API 兼容。
"""

import json
import os
import sys
import pickle
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

import numpy as np
import pandas as pd

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))

# Fix MLflow
os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")

# ─── 默认路径 (保持 AgentAlpha 兼容) ───

DEFAULT_BIN_DIR = "D:/qlib/qlib_data/cn_data"
DEFAULT_OUTPUT_FILE = "data/alpha_signals.json"
DEFAULT_MODEL_DIR = "data/qlib_models"


# ─── 配置加载 (保持 AgentAlpha 兼容) ───

def load_config() -> dict:
    """加载 config.json (兼容接口)"""
    from src.data.config_loader import get_config
    return get_config()


def get_alpha_config() -> dict:
    """获取 alpha 配置节"""
    config = load_config()
    return config.get("alpha", {})


def get_data_config() -> dict:
    """获取数据配置节"""
    config = load_config()
    return config.get("data", {})


# ─── Qlib 初始化 ───

def init_qlib(bin_dir: str = DEFAULT_BIN_DIR) -> bool:
    """初始化 Qlib 环境"""
    import qlib
    try:
        qlib.init(provider_uri=bin_dir, region="cn")
        return True
    except Exception as e:
        print(f"Qlib 初始化失败: {e}")
        return False


# ─── 模型持久化 ───

def get_model_path(model_name: str, model_dir: str = None) -> Path:
    """获取模型文件路径"""
    model_dir = Path(model_dir or DEFAULT_MODEL_DIR)
    model_dir.mkdir(parents=True, exist_ok=True)
    return model_dir / f"{model_name}.pkl"


def load_cached_model(model_name: str, model_dir: str = None):
    """加载缓存的模型"""
    model_path = get_model_path(model_name, model_dir)
    if model_path.exists():
        with open(model_path, "rb") as f:
            return pickle.load(f)
    return None


def save_model(model, model_name: str, model_dir: str = None) -> None:
    """保存模型到缓存"""
    model_path = get_model_path(model_name, model_dir)
    with open(model_path, "wb") as f:
        pickle.dump(model, f)


def get_model_info(model_name: str, model_dir: str = None) -> dict:
    """获取模型信息"""
    model_path = get_model_path(model_name, model_dir)
    if model_path.exists():
        stat = model_path.stat()
        return {
            "model_name": model_name,
            "exists": True,
            "size_mb": round(stat.st_size / 1024 / 1024, 2),
            "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        }
    return {"model_name": model_name, "exists": False}


# ─── 训练 ───

def train_model(
    model_name: str,
    bin_dir: str = None,
    force: bool = False,
    model_dir: str = None,
) -> Any:
    """
    训练 ML 模型（使用 QSF AlphaSignalGenerator）。

    返回:
        训练好的模型对象
    """
    bin_dir = bin_dir or DEFAULT_BIN_DIR
    init_qlib(bin_dir)

    from src.factors.alpha_signals import AlphaSignalGenerator

    generator = AlphaSignalGenerator(
        bin_dir=bin_dir,
        model_dir=model_dir or DEFAULT_MODEL_DIR,
        output_dir=Path(model_dir or DEFAULT_MODEL_DIR).parent / "output" if model_dir else None,
    )

    model = generator.train(model_name=model_name, force_retrain=force)
    return model


# ─── 预测 ───

def predict_alpha(
    model,
    model_name: str,
    bin_dir: str = None,
    predict_date: str = None,
) -> pd.DataFrame:
    """
    用训练好的模型预测 alpha 分数。

    返回:
        DataFrame with columns [datetime, instrument, score]
    """
    bin_dir = bin_dir or DEFAULT_BIN_DIR
    predict_date = predict_date or datetime.now().strftime("%Y-%m-%d")
    init_qlib(bin_dir)

    from src.factors.alpha_signals import AlphaSignalGenerator

    generator = AlphaSignalGenerator(bin_dir=bin_dir, model_dir=DEFAULT_MODEL_DIR)
    predictions = generator.predict(model_name=model_name, date=predict_date)
    return predictions


# ─── 格式化输出 ───

def format_alpha_output(
    pred_df: pd.DataFrame,
    model_name: str,
    predict_date: str,
    top_n: int = 50,
) -> dict:
    """格式化预测结果为 AgentAlpha 兼容的 JSON 结构"""
    scores = pred_df["score"].values if "score" in pred_df.columns else pred_df.iloc[:, -1].values
    instruments = pred_df.index.get_level_values("instrument").values if isinstance(pred_df.index, pd.MultiIndex) else pred_df.index.values

    # 排序
    sorted_idx = np.argsort(scores)[::-1]
    top_50 = []
    for i in sorted_idx[:top_n]:
        top_50.append({
            "symbol": str(instruments[i]),
            "alpha_score": round(float(scores[i]), 6),
            "rank": int(i) + 1,
        })

    return {
        "model": model_name,
        "date": predict_date,
        "total_stocks": len(pred_df),
        "top_50": top_50,
        "top_10": top_50[:10],
        "statistics": {
            "mean": round(float(np.mean(scores)), 6),
            "std": round(float(np.std(scores)), 6),
            "min": round(float(np.min(scores)), 6),
            "max": round(float(np.max(scores)), 6),
        },
    }


def save_alpha_output(output: dict, output_file: str = None) -> str:
    """保存 alpha 信号到 JSON 文件"""
    output_file = output_file or DEFAULT_OUTPUT_FILE
    output_path = Path(output_file)
    if not output_path.is_absolute():
        output_path = Path(__file__).resolve().parent.parent.parent / output_file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    return str(output_path)


# ─── 主入口 ───

def run_alpha(
    model_name: str = "gbdt_alpha158",
    train: bool = True,
    force_train: bool = False,
    predict_date: str = None,
    top_n: int = 50,
    bin_dir: str = None,
    model_dir: str = None,
    output_file: str = None,
    instruments: str = "csi500",
) -> dict:
    """
    运行完整的 alpha 信号生成流程。

    返回:
        dict with model, date, top_50, top_10, statistics
    """
    bin_dir = bin_dir or DEFAULT_BIN_DIR
    predict_date = predict_date or datetime.now().strftime("%Y-%m-%d")
    init_qlib(bin_dir)

    from src.factors.alpha_signals import AlphaSignalGenerator

    generator = AlphaSignalGenerator(
        bin_dir=bin_dir,
        model_dir=model_dir or DEFAULT_MODEL_DIR,
        output_dir=Path(output_file or DEFAULT_OUTPUT_FILE).parent,
    )

    result = generator.run(
        model_name=model_name,
        date=predict_date,
        train_first=train,
        top_n=top_n,
        instruments=instruments,
        force_retrain=force_train,
    )

    # 转换为 AgentAlpha 兼容格式
    output = format_alpha_output(
        pred_df=result.get("predictions", pd.DataFrame()),
        model_name=model_name,
        predict_date=predict_date,
        top_n=top_n,
    )

    save_alpha_output(output, output_file)
    return output


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="AgentAlpha Alpha 信号生成 (QSF 桥接)")
    parser.add_argument("--train", action="store_true", help="训练新模型")
    parser.add_argument("--predict", action="store_true", help="仅预测")
    parser.add_argument("--force", action="store_true", help="强制重新训练")
    parser.add_argument("--model", default="gbdt_alpha158", help="模型名称")
    parser.add_argument("--date", help="预测日期 (YYYY-MM-DD)")
    parser.add_argument("--top-n", type=int, default=50, help="输出 Top N")
    parser.add_argument("--ensemble", action="store_true", help="集成多模型预测")
    parser.add_argument("--status", action="store_true", help="显示模型缓存状态")
    parser.add_argument("--list-models", action="store_true", help="列出可用模型")
    parser.add_argument("--output", help="输出文件路径")
    parser.add_argument("--instruments", default="csi500", help="标的池")

    args = parser.parse_args()

    if args.list_models:
        from scripts.qlib.model_configs import list_available_models
        for name, desc in list_available_models().items():
            print(f"  {name}: {desc}")
        sys.exit(0)

    if args.status:
        for model_name in ["gbdt_alpha158", "xgboost_alpha158"]:
            info = get_model_info(model_name)
            status = "cached" if info["exists"] else "not cached"
            print(f"  {model_name}: {status} ({info.get('size_mb', 0)} MB)")
        sys.exit(0)

    predict_date = args.date or datetime.now().strftime("%Y-%m-%d")

    if args.ensemble:
        from src.factors.alpha_signals import AlphaSignalGenerator
        init_qlib()
        generator = AlphaSignalGenerator(bin_dir=DEFAULT_BIN_DIR, model_dir=DEFAULT_MODEL_DIR)
        result = generator.run_ensemble(
            model_names=["gbdt", "xgboost"],
            date=predict_date,
            top_n=args.top_n,
        )
        output = format_alpha_output(
            pred_df=result.get("predictions", pd.DataFrame()),
            model_name="ensemble",
            predict_date=predict_date,
            top_n=args.top_n,
        )
    else:
        output = run_alpha(
            model_name=args.model,
            train=args.train or not args.predict,
            force_train=args.force,
            predict_date=predict_date,
            top_n=args.top_n,
            output_file=args.output,
            instruments=args.instruments,
        )

    save_alpha_output(output, args.output)
    print(json.dumps(output, ensure_ascii=False, indent=2))