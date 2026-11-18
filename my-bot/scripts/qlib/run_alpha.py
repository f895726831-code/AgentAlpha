"""
FM_trade Alpha 生成 CLI — 桥接层 (底层: quant-signal-factory)

从 QSF src/factors/alpha_signals.py 导入，保持 FM_trade CLI 兼容。
调用方: run-daily-scan.ps1 (line 190)
"""

import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime

# 确保 QSF 在 sys.path 中
_QSF_ROOT = Path("signal-factory")
if str(_QSF_ROOT) not in sys.path:
    sys.path.insert(0, str(_QSF_ROOT))


def run_ensemble(
    model_names: list = None,
    predict_date: str = None,
    top_n: int = 50,
    bin_dir: str = None,
    model_dir: str = None,
    output_file: str = None,
    instruments: str = "csi500",
) -> dict:
    """集成多模型预测"""
    bin_dir = bin_dir or "D:/qlib/qlib_data/cn_data"
    predict_date = predict_date or datetime.now().strftime("%Y-%m-%d")

    import qlib
    qlib.init(provider_uri=bin_dir, region="cn")

    from src.factors.alpha_signals import AlphaSignalGenerator

    model_names = model_names or ["gbdt", "xgboost"]
    generator = AlphaSignalGenerator(
        bin_dir=bin_dir,
        model_dir=model_dir or "data/qlib_models",
    )

    result = generator.run_ensemble(
        model_names=model_names,
        date=predict_date,
        top_n=top_n,
        instruments=instruments,
    )

    from scripts.qlib.alpha_signals import format_alpha_output, save_alpha_output
    output = format_alpha_output(
        pred_df=result.get("predictions", __import__("pandas").DataFrame()),
        model_name="ensemble",
        predict_date=predict_date,
        top_n=top_n,
    )

    save_alpha_output(output, output_file)
    return output


def show_status() -> None:
    """显示模型缓存状态"""
    from scripts.qlib.alpha_signals import get_model_info

    models = ["gbdt_alpha158", "xgboost_alpha158", "gbdt_alpha360", "tabnet_alpha158", "lstm_alpha158"]
    print("模型缓存状态:")
    for name in models:
        info = get_model_info(name)
        status = "已缓存" if info["exists"] else "未缓存"
        size = f" ({info.get('size_mb', 0)} MB)" if info["exists"] else ""
        print(f"  {name}: {status}{size}")

    # 检查最新 alpha 输出
    output_path = Path("data/alpha_signals.json")
    if output_path.exists():
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                alpha = json.load(f)
            print(f"\n最新 Alpha 信号: {alpha.get('date', 'N/A')} ({alpha.get('model', 'N/A')})")
            print(f"  标的数: {alpha.get('total_stocks', 0)}")
            top5 = alpha.get("top_10", [])[:5]
            for i, s in enumerate(top5, 1):
                print(f"  {i}. {s['symbol']}  alpha={s.get('alpha_score', 'N/A')}")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="FM_trade Alpha 信号生成 (QSF 桥接)")
    parser.add_argument("--train", action="store_true", help="训练新模型")
    parser.add_argument("--force", action="store_true", help="强制重新训练")
    parser.add_argument("--model", default="gbdt_alpha158", help="模型名称")
    parser.add_argument("--date", help="预测日期 (YYYY-MM-DD)")
    parser.add_argument("--top-n", type=int, default=50, help="输出 Top N")
    parser.add_argument("--ensemble", action="store_true", help="集成多模型预测")
    parser.add_argument("--status", action="store_true", help="显示模型缓存状态")
    parser.add_argument("--output", help="输出文件路径")
    parser.add_argument("--instruments", default="csi500", help="标的池")

    args = parser.parse_args()

    if args.status:
        show_status()
        return

    predict_date = args.date or datetime.now().strftime("%Y-%m-%d")

    if args.ensemble:
        output = run_ensemble(
            predict_date=predict_date,
            top_n=args.top_n,
            output_file=args.output,
            instruments=args.instruments,
        )
    else:
        from scripts.qlib.alpha_signals import run_alpha, save_alpha_output
        output = run_alpha(
            model_name=args.model,
            train=args.train or True,
            force_train=args.force,
            predict_date=predict_date,
            top_n=args.top_n,
            output_file=args.output,
            instruments=args.instruments,
        )

    save_alpha_output(output, args.output)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()