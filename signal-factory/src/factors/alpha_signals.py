"""
Alpha 信号生成 — Qlib ML 模型训练 → 预测 → 排名 → 输出

用 Qlib 的 ML 模型（GBDT、XGBoost 等）训练 alpha 信号，产出每日股票排名，
输入到 FM_trade Stage 1（选股）阶段。

核心流程:
  1. 初始化 Qlib 环境
  2. 加载 Alpha158 因子集
  3. 训练模型（或加载已有模型）
  4. 全市场预测 → alpha 分数
  5. 按 alpha 排名 → Top N 股票列表
  6. 输出 data/alpha_signals.json

用法:
    # 命令行
    python -m src.factors.alpha_signals --model gbdt --date 2026-08-07

    # Python API
    from src.factors.alpha_signals import AlphaSignalGenerator

    gen = AlphaSignalGenerator()
    result = gen.run("gbdt")
    print(result["top_10"])
"""

import json
import os
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

# Fix Windows GBK encoding: emoji characters (⚠⚡✅) in print statements
# crash with UnicodeEncodeError on Windows terminals defaulting to GBK.
# This was the root cause of XGBoost appearing to "return empty" — the
# ensemble pipeline crashed at _resolve_prediction_date before reaching
# the prediction step.
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.factors.model_configs import (
    ModelConfig,
    get_model_config,
    get_active_models,
    get_model_ensemble_configs,
    list_models,
)


def _normalize_symbol(sym: str) -> str:
    """统一标的代码格式: 去掉 sh/sz 前缀，用于行业分类匹配"""
    if isinstance(sym, str) and len(sym) >= 2 and sym[:2] in ("sh", "sz"):
        return sym[2:]
    return sym


class AlphaSignalGenerator:
    """
    Alpha 信号生成器。

    封装 Qlib 模型训练、预测、排名全流程。

    用法:
        gen = AlphaSignalGenerator()
        result = gen.run("gbdt")               # 单模型
        result = gen.run_ensemble(["gbdt", "xgboost"])  # 集成
    """

    def __init__(
        self,
        models_dir: str = "data/output/models",
        signals_dir: str = "data/output",
        predictions_dir: str = "data/output/predictions",
        label_horizon: int = 5,
    ):
        self.models_dir = Path(models_dir)
        self.signals_dir = Path(signals_dir)
        self.predictions_dir = Path(predictions_dir)
        self.label_horizon = label_horizon  # label 需要多少未来交易日

        for d in [self.models_dir, self.signals_dir, self.predictions_dir]:
            d.mkdir(parents=True, exist_ok=True)

        self._qlib_initialized = False

    def _init_qlib(self):
        """初始化 Qlib 环境"""
        if self._qlib_initialized:
            return

        from src.data.config_loader import get_qlib_bin_dir, get_qlib_region, get_qlib_calendar_path, get_qlib_local_path

        bin_dir = get_qlib_bin_dir()

        if not Path(bin_dir).exists():
            raise FileNotFoundError(
                f"Qlib bin 数据目录不存在: {bin_dir}\n"
                f"请先运行 scripts/sync_data.py 同步数据"
            )

        # 使用本地 Qlib 0.9.8.dev32（含 Windows 兼容性补丁）
        _QLIB_LOCAL = get_qlib_local_path()
        if _QLIB_LOCAL not in sys.path:
            sys.path.insert(0, _QLIB_LOCAL)

        import qlib
        from qlib.config import C
        from qlib_compat import init_qlib

        os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
        init_qlib()

        # 使用 threading 后端避免多进程问题
        C["joblib_backend"] = "threading"
        C["kernels"] = 1

        self._qlib_initialized = True

    # ─── 训练 ───

    def _get_latest_valid_prediction_date(self) -> Optional[str]:
        """
        计算 label 约束下最晚可预测日期。

        5 日 forward return label 需要未来 5 个交易日数据，
        因此最晚可预测日 = 日历最后一天 - label_horizon 个交易日。

        返回:
            最晚可预测日期 (YYYY-MM-DD) 或 None（无法读取日历时）
        """
        from src.data.config_loader import get_qlib_calendar_path
        cal_path = Path(get_qlib_calendar_path())
        if not cal_path.exists():
            return None

        with open(cal_path, "r", encoding="utf-8") as f:
            dates = [line.strip() for line in f if line.strip() and line.strip() != "1970-01-01"]

        if len(dates) <= self.label_horizon:
            return None

        # 日历最后一天往前推 label_horizon 个交易日
        return dates[-(self.label_horizon + 1)]

    def _get_latest_calendar_date(self) -> Optional[str]:
        """获取日历文件中的最新交易日（不扣除 label horizon）。

        用于 infer_only 模式：不需要 label，只需要特征数据存在即可预测。
        """
        from src.data.config_loader import get_qlib_calendar_path
        cal_path = Path(get_qlib_calendar_path())
        if not cal_path.exists():
            return None
        with open(cal_path, "r", encoding="utf-8") as f:
            dates = [line.strip() for line in f if line.strip() and line.strip() != "1970-01-01"]
        return dates[-1] if dates else None

    def _resolve_prediction_date(self, date: Optional[str]) -> str:
        """
        解析预测日期，自动回退到 label 约束内的最晚可用日期。

        参数:
            date: 用户指定的日期 (None = 今天)

        返回:
            实际使用的预测日期 (YYYY-MM-DD)
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        latest_valid = self._get_latest_valid_prediction_date()

        if latest_valid and date > latest_valid:
            print(
                f"  ⚠ 日期 {date} 超出 label 约束范围 "
                f"(最晚可预测: {latest_valid})，自动回退"
            )
            return latest_valid

        return date

    def _model_path(self, cfg: ModelConfig, instruments: str) -> Path:
        """生成标的特定的模型文件路径，避免 CSI 300/500 模型互相覆盖"""
        return self.models_dir / f"{cfg.name}_{instruments}_{cfg.train_frequency}.pkl"

    def train(
        self,
        model_name: str = "gbdt",
        instruments: str = "csi500",
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        force_retrain: bool = False,
    ) -> Dict[str, Any]:
        """
        训练单个模型。

        参数:
            model_name: 模型名称 (gbdt/xgboost/lstm/tabnet)
            instruments: 训练标的范围
            start_time: 训练起始日 (None=使用配置默认值)
            end_time: 训练结束日 (None=使用配置默认值)
            force_retrain: 是否强制重新训练

        返回:
            {
                model_name: str,
                model_path: str,
                ic_mean: float,
                ic_ir: float,
                rank_ic: float,
                training_time: float,
                status: str,
            }
        """
        cfg = get_model_config(model_name)
        if cfg is None:
            return {"error": f"未知模型: {model_name}", "available": list_models()}

        self._init_qlib()

        # 模型保存路径（标的特定）
        model_path = self._model_path(cfg, instruments)
        if model_path.exists() and not force_retrain:
            return {
                "model_name": model_name,
                "model_path": str(model_path),
                "status": "cached",
                "note": "模型已存在，使用 force_retrain=True 强制重训",
            }

        start = start_time or cfg.train_start
        end = end_time or cfg.handler_kwargs.get("end_time", "2026-08-01")

        print(f"[Alpha 训练] {cfg.display_name}")
        print(f"  标的: {instruments}")
        print(f"  区间: {start} ~ {end}")
        print(f"  特征: {cfg.handler_class}")

        t0 = time.time()

        try:
            result = self._train_model(cfg, instruments, start, end)
        except Exception as e:
            return {
                "model_name": model_name,
                "status": "error",
                "error": str(e),
                "training_time": round(time.time() - t0, 1),
            }

        result["training_time"] = round(time.time() - t0, 1)
        result["model_path"] = str(model_path)

        return result

    def _train_model(
        self,
        cfg: ModelConfig,
        instruments: str,
        start_time: str,
        end_time: str,
    ) -> Dict[str, Any]:
        """内部训练逻辑"""
        from qlib.contrib.data.dataset import DatasetH
        from qlib.workflow import R

        # 构建 handler（不覆盖 fit_end_time，使用配置默认值确保 label 有足够数据）
        handler = cfg.build_handler(
            instruments=instruments,
            start_time=start_time,
            end_time=end_time,
        )

        # 构建 dataset
        # 使用配置的 fit_end_time 作为训练截止日
        fit_end = cfg.handler_kwargs.get("fit_end_time", end_time)
        from datetime import timedelta
        start_dt = pd.Timestamp(start_time)
        fit_end_dt = pd.Timestamp(fit_end)
        total_days = max(1, (fit_end_dt - start_dt).days)
        valid_days = max(1, int(total_days * cfg.valid_ratio))
        valid_start = (fit_end_dt - timedelta(days=valid_days * 2)).strftime("%Y-%m-%d")
        dataset = DatasetH(
            handler=handler,
            segments={
                "train": (start_time, valid_start),
                "valid": (valid_start, fit_end),
                "test": (fit_end, end_time),
            },
        )

        # 构建模型
        model = cfg.build_model()

        # 训练 — 根据模型 fit() 接口自适应传递参数
        # 树模型接受 num_boost_round/early_stopping_rounds/verbose_eval
        # 线性模型/LSTM 不接受这些参数
        import inspect
        fit_sig = inspect.signature(model.fit)
        fit_kwargs = {}
        if "num_boost_round" in fit_sig.parameters:
            fit_kwargs["num_boost_round"] = cfg.num_boost_round
        if "early_stopping_rounds" in fit_sig.parameters:
            fit_kwargs["early_stopping_rounds"] = cfg.early_stopping_rounds
        if "verbose_eval" in fit_sig.parameters:
            fit_kwargs["verbose_eval"] = cfg.verbose_eval

        with R.start(experiment_name=f"alpha_{cfg.name}", recorder_name=cfg.name):
            model.fit(dataset, **fit_kwargs)
            R.save_objects(trained_model=model)

        # 在训练集上评估 IC
        pred_train = model.predict(dataset, segment="train")
        ic_result = self._compute_ic(pred_train, dataset, "train")

        # 在验证集上评估 IC（OOS holdout，供 OOSWeightGuard 使用）
        try:
            pred_valid = model.predict(dataset, segment="valid")
            valid_ic_result = self._compute_ic(pred_valid, dataset, "valid")
            valid_ic = valid_ic_result.get("ic_mean")
        except Exception:
            valid_ic = None

        # 记录 OOS 验证 IC 到权重守卫（P1-8）
        try:
            from src.factors.oos_weight_guard import get_guard
            guard = get_guard()
            guard.record_ic(cfg.name, instruments, end_time, valid_ic)
            if valid_ic is not None:
                print(f"  [OOS Guard] {cfg.name}/{instruments} valid_ic={valid_ic:.4f}")
        except Exception as e:
            print(f"  [OOS Guard] 记录失败: {e}")

        # 保存模型（标的特定路径）
        model_path = self._model_path(cfg, instruments)
        self._save_model(model, model_path)

        return {
            "model_name": cfg.name,
            "status": "trained",
            "ic_mean": ic_result.get("ic_mean"),
            "ic_ir": ic_result.get("ic_ir"),
            "rank_ic": ic_result.get("rank_ic"),
            "valid_ic": valid_ic,
        }

    # ─── 预测 ───

    def predict(
        self,
        model_name: str = "gbdt",
        instruments: str = "csi500",
        date: Optional[str] = None,
        infer_only: bool = False,
    ) -> pd.DataFrame:
        """
        用训练好的模型生成全市场 alpha 预测。

        参数:
            model_name: 模型名称
            instruments: 预测标的范围
            date: 预测日期 (None = 最新交易日期)
            infer_only: 纯特征推断模式，跳过 label 约束检查

        返回:
            DataFrame with columns [symbol, alpha_score, rank]
        """
        self._init_qlib()

        cfg = get_model_config(model_name)
        if cfg is None:
            raise ValueError(f"未知模型: {model_name}")

        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        if infer_only:
            # infer_only: resolve to latest calendar date (no label constraint needed).
            # DatasetH needs the date to exist in the Qlib calendar/bin data.
            latest_cal = self._get_latest_calendar_date()
            if latest_cal and date > latest_cal:
                print(
                    f"  ⚡ 纯推断: 请求日 {date} → 最新日历日 {latest_cal}"
                )
                date = latest_cal
        else:
            date = self._resolve_prediction_date(date)

        print(f"[Alpha 预测] {cfg.display_name} @ {date}")

        # 尝试加载已有模型（标的特定）
        model = self._load_model(cfg, instruments)
        if model is None:
            raise RuntimeError(
                f"模型 {model_name} 尚未训练，请先调用 train('{model_name}')"
            )

        from qlib.contrib.data.dataset import DatasetH

        # 构建 handler（预测期）
        lookback_start = self._compute_lookback(date, cfg.min_train_samples)
        handler = cfg.build_handler(
            instruments=instruments,
            start_time=lookback_start,
            end_time=date,
            fit_start_time=lookback_start,
            fit_end_time=date,
        )

        dataset = DatasetH(
            handler=handler,
            segments={"test": (date, date)},
        )

        # 预测
        pred = model.predict(dataset, segment="test")

        if pred.empty:
            return pd.DataFrame(columns=["symbol", "alpha_score", "rank"])

        # 转换为排名 DataFrame
        result = self._predictions_to_ranked(pred)

        # infer_only 回退: 如果最新日历日数据不完整（<50 只），回退到安全日期
        if infer_only and len(result) < 50:
            safe_date = self._resolve_prediction_date(date)
            print(
                f"  ⚠  infer-only 数据不完整 ({len(result)} stocks @ {date})，"
                f"回退到安全日期 {safe_date}"
            )
            lookback_start = self._compute_lookback(safe_date, cfg.min_train_samples)
            handler = cfg.build_handler(
                instruments=instruments,
                start_time=lookback_start,
                end_time=safe_date,
                fit_start_time=lookback_start,
                fit_end_time=safe_date,
            )
            dataset = DatasetH(
                handler=handler,
                segments={"test": (safe_date, safe_date)},
            )
            pred = model.predict(dataset, segment="test")
            if pred.empty:
                return pd.DataFrame(columns=["symbol", "alpha_score", "rank"])
            result = self._predictions_to_ranked(pred)
            # 更新日期以反映实际使用的日期
            date = safe_date

        return result

    def predict_ensemble(
        self,
        model_names: Optional[List[str]] = None,
        instruments: str = "csi500",
        date: Optional[str] = None,
        method: str = "mean",
        infer_only: bool = False,
        model_weights: Optional[Dict[str, float]] = None,
    ) -> pd.DataFrame:
        """
        集成多个模型的预测。

        参数:
            model_names: 模型列表 (None = 默认活跃模型)
            instruments: 预测标的范围
            date: 预测日期
            method: 集成方法 ("mean" / "median" / "rank_mean" / "weighted_mean")
            model_weights: 手动指定权重 (仅 method="mean" 时生效)。
                           method="weighted_mean" 自动从 OOSWeightGuard 获取权重。

        返回:
            DataFrame with columns [symbol, alpha_score, rank]
        """
        names = model_names or get_active_models()
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        if not infer_only:
            date = self._resolve_prediction_date(date)

        # infer_only fallback: check first model, retry with safe date if data incomplete
        if infer_only:
            self._init_qlib()
            first_name = names[0]
            first_cfg = get_model_config(first_name)
            if first_cfg:
                lookback_start = self._compute_lookback(date, first_cfg.min_train_samples)
                from qlib.contrib.data.dataset import DatasetH
                test_handler = first_cfg.build_handler(
                    instruments=instruments,
                    start_time=lookback_start,
                    end_time=date,
                    fit_start_time=lookback_start,
                    fit_end_time=date,
                )
                test_ds = DatasetH(handler=test_handler, segments={"test": (date, date)})
                test_model = self._load_model(first_cfg, instruments)
                if test_model is not None:
                    try:
                        test_pred = test_model.predict(test_ds, segment="test")
                        if test_pred is not None and hasattr(test_pred, '__len__') and len(test_pred) < 50:
                            safe_date = self._resolve_prediction_date(date)
                            print(
                                f"  ⚠  infer-only 数据不完整 ({len(test_pred)} stocks @ {date})，"
                                f"回退到安全日期 {safe_date}"
                            )
                            date = safe_date
                    except Exception:
                        pass  # 检查失败不影响主流程

        # ─── OOS 权重解析 (P1-8) ───
        if method == "weighted_mean" or (method == "mean" and model_weights):
            if method == "weighted_mean":
                try:
                    from src.factors.oos_weight_guard import get_guard
                    guard = get_guard()
                    model_weights = guard.compute_weights(names, instruments, date)
                    if model_weights:
                        print(f"  [OOS 权重] " + ", ".join(
                            f"{n}={w:.3f}" for n, w in sorted(model_weights.items(), key=lambda x: -x[1])
                        ))
                except Exception as e:
                    print(f"  [OOS 权重] 获取失败, 回退等权: {e}")
                    model_weights = None
            else:
                print(f"  [手动权重] " + ", ".join(
                    f"{n}={w:.3f}" for n, w in sorted(model_weights.items(), key=lambda x: -x[1])
                ))

        print(f"[Alpha 集成] {len(names)} 个模型 @ {date}: {', '.join(names)}")
        if infer_only:
            print(f"  ⚡ 纯推断模式（跳过 label 约束）")

        all_preds = []
        errors = []
        for name in names:
            try:
                pred = self.predict(name, instruments, date, infer_only=infer_only)
                if not pred.empty:
                    pred["model"] = name
                    all_preds.append(pred)
                else:
                    errors.append((name, "EMPTY", "模型预测返回空结果（可能数据不足或特征不匹配）"))
            except UnicodeEncodeError as e:
                errors.append((name, "ENCODING", f"编码错误: {e}"))
                print(f"  ⚠ {name}: 编码错误 — 检查终端是否为 UTF-8")
            except RuntimeError as e:
                msg = str(e)
                if "尚未训练" in msg:
                    errors.append((name, "NOT_TRAINED", msg))
                elif "模型文件" in msg:
                    errors.append((name, "LOAD_FAIL", msg))
                else:
                    errors.append((name, "PREDICT_FAIL", msg))
                print(f"  ⚠ {name}: {e}")
            except Exception as e:
                errors.append((name, "PREDICT_FAIL", str(e)))
                print(f"  ⚠ {name}: {e}")

        if errors:
            for name, err_type, err_msg in errors:
                print(f"  [降级] {name}: [{err_type}] {err_msg}")

        if not all_preds:
            err_detail = "; ".join(f"{n}: [{t}] {m}" for n, t, m in errors)
            raise RuntimeError(
                f"所有模型预测均失败 ({len(errors)}/{len(names)}): {err_detail}"
            )

        combined = pd.concat(all_preds, ignore_index=True)

        # 集成
        if method in ("mean", "weighted_mean"):
            if model_weights and any(w != 1.0/len(names) for w in model_weights.values()):
                # 加权平均 (P1-8 OOS 自动降权 或 手动权重)
                w = model_weights
                combined["weight"] = combined["model"].map(w)
                # fallback for models not in weight dict
                mask = combined["weight"].isna()
                if mask.any():
                    combined.loc[mask, "weight"] = 1.0 / len(names)
                # groupby weighted average
                grouped = combined.groupby("symbol")
                total_w = grouped["weight"].transform("sum")
                combined["weighted_score"] = combined["alpha_score"] * combined["weight"]
                summed = grouped["weighted_score"].sum()
                ensemble = (summed / grouped["weight"].sum()).reset_index(name="alpha_score")
            else:
                # 等权平均（无权重或所有权重相等）
                ensemble = combined.groupby("symbol")["alpha_score"].mean().reset_index()
        elif method == "median":
            ensemble = combined.groupby("symbol")["alpha_score"].median().reset_index()
        elif method == "rank_mean":
            # 对每个模型的预测分别排名，然后取平均排名
            combined["model_rank"] = combined.groupby("model")["alpha_score"].rank(ascending=False)
            ensemble = combined.groupby("symbol")["model_rank"].mean().reset_index()
            ensemble = ensemble.rename(columns={"model_rank": "alpha_score"})
        else:
            raise ValueError(f"未知集成方法: {method}")

        ensemble["rank"] = ensemble["alpha_score"].rank(ascending=False)
        return ensemble.sort_values("rank")

    # ─── 完整运行 ───

    def run(
        self,
        model_name: str = "gbdt",
        instruments: str = "csi500",
        date: Optional[str] = None,
        train_first: bool = True,
        top_n: int = 50,
        infer_only: bool = False,
        neutralize_industry: bool = False,
        output_path: Optional[str] = None,
        force_retrain: bool = False,
    ) -> Dict[str, Any]:
        """
        完整运行：训练 → 预测 → 排名 → 保存。

        这是主要的用户入口。

        返回:
            {
                date, model, top_10, top_50, all_scores, ic_metrics, status
            }
        """
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        if not infer_only:
            date = self._resolve_prediction_date(date)

        print(f"{'='*60}")
        print(f"Alpha 信号生成: {model_name} @ {date}")
        print(f"{'='*60}")

        # 训练
        if train_first:
            train_result = self.train(model_name, instruments, force_retrain=force_retrain)
            if train_result.get("status") == "error":
                return {"status": "error", "error": train_result.get("error")}
        else:
            train_result = {"status": "cached"}

        # 预测
        try:
            ranked = self.predict(model_name, instruments, date, infer_only=infer_only)
        except RuntimeError as e:
            return {"status": "error", "error": str(e)}

        if ranked.empty:
            return {"status": "warning", "warning": "无预测结果（可能数据不足）", "date": date}

        # 行业中性化
        if neutralize_industry:
            ranked = self._neutralize_alpha_scores(ranked, instruments)

        # 构建输出
        result = self._build_output(ranked, model_name, date, top_n)
        result["train_result"] = train_result
        result["status"] = "ok"

        # 保存（infer-only 模式不保存到默认路径，避免覆盖标准模式输出）
        if not infer_only:
            self._save_signals(result, output_path)

        print(f"\n✅ Alpha 信号生成完成: {len(ranked)} 只标的")
        if result.get("top_10"):
            for i, s in enumerate(result["top_10"][:5], 1):
                print(f"  {i}. {s['symbol']}  alpha={s['alpha_score']:.4f}")

        return result

    def run_ensemble(
        self,
        model_names: Optional[List[str]] = None,
        instruments: str = "csi500",
        date: Optional[str] = None,
        train_first: bool = True,
        top_n: int = 50,
        infer_only: bool = False,
        neutralize_industry: bool = False,
        output_path: Optional[str] = None,
        force_retrain: bool = False,
        oos_weight: bool = False,
    ) -> Dict[str, Any]:
        """
        集成多模型运行。

        参数:
            oos_weight: 启用 OOS 自动降权（P1-8）。使用 OOSWeightGuard 滚动 IC 计算权重。

        返回:
            {date, models, ensemble_method, top_10, top_50, all_scores, status}
        """
        names = model_names or get_active_models()
        if date is None:
            date = datetime.now().strftime("%Y-%m-%d")

        if not infer_only:
            date = self._resolve_prediction_date(date)

        print(f"{'='*60}")
        print(f"Alpha 集成信号: {', '.join(names)} @ {date}")
        if infer_only:
            print(f"  ⚡ 纯推断模式（跳过 label 约束）")
        print(f"{'='*60}")

        # 训练所有模型
        if train_first:
            for name in names:
                self.train(name, instruments, force_retrain=force_retrain)

        # 集成预测
        ensemble_method = "weighted_mean" if oos_weight else "mean"
        ranked = self.predict_ensemble(
            names, instruments, date,
            method=ensemble_method,
            infer_only=infer_only,
        )

        if ranked.empty:
            return {"status": "warning", "warning": "无预测结果", "date": date}

        # 行业中性化
        if neutralize_industry:
            ranked = self._neutralize_alpha_scores(ranked, instruments)

        result = self._build_output(ranked, f"ensemble_{'+'.join(names)}", date, top_n)
        result["models"] = names
        result["status"] = "ok"

        # infer-only 模式不保存到默认路径（避免覆盖标准模式输出），
        # 由 CLI --output 参数单独保存
        if not infer_only:
            self._save_signals(result, output_path)

        print(f"\n✅ 集成信号生成完成: {len(ranked)} 只标的")
        if result.get("top_10"):
            for i, s in enumerate(result["top_10"][:5], 1):
                print(f"  {i}. {s['symbol']}  alpha={s['alpha_score']:.4f}")

        return result

    # ─── 内部方法 ───

    def _compute_ic(
        self,
        predictions: pd.Series,
        dataset: Any,
        segment: str,
    ) -> Dict[str, Optional[float]]:
        """计算 IC (Information Coefficient)"""
        try:
            labels = dataset.prepare(segment, col_set="label")
            if labels is None or labels.empty:
                return {"ic_mean": None, "ic_ir": None, "rank_ic": None}

            label_series = labels.iloc[:, 0] if labels.ndim > 1 else labels

            # 对齐 index
            common = predictions.index.intersection(label_series.index)
            if len(common) < 10:
                return {"ic_mean": None, "ic_ir": None, "rank_ic": None}

            p = predictions.loc[common]
            l = label_series.loc[common]

            # Pearson IC
            ic = p.corr(l)
            # Rank IC
            rank_ic = p.rank().corr(l.rank())

            return {
                "ic_mean": round(float(ic), 4) if not pd.isna(ic) else None,
                "ic_ir": None,  # 需要时序 IC 才能计算 IR
                "rank_ic": round(float(rank_ic), 4) if not pd.isna(rank_ic) else None,
            }
        except Exception:
            return {"ic_mean": None, "ic_ir": None, "rank_ic": None}

    def _predictions_to_ranked(self, predictions: pd.Series) -> pd.DataFrame:
        """将预测 Series 转换为排名 DataFrame"""
        if predictions.empty:
            return pd.DataFrame(columns=["symbol", "alpha_score", "rank"])

        df = predictions.reset_index()

        # 如果返回了多个日期的预测（MultiIndex），只取最新日期
        if "datetime" in df.columns and len(df) > 0:
            latest_date = df["datetime"].max()
            df = df[df["datetime"] == latest_date].copy()
            # 预测值列名可能是 0 或 "prediction" 等，取最后一列
            value_col = [c for c in df.columns if c not in ("datetime", "instrument")]
            if value_col:
                df = df[["instrument", value_col[0]]].copy()
                df.columns = ["instrument", "alpha_score"]
            else:
                df = df[["instrument"]].copy()
                df["alpha_score"] = 0.0
        elif len(df.columns) == 2:
            df.columns = ["instrument", "alpha_score"]
        else:
            # 多列情况：取前两列
            df = df.iloc[:, :2].copy()
            df.columns = ["instrument", "alpha_score"]

        df = df.rename(columns={"instrument": "symbol"})
        df["alpha_score"] = pd.to_numeric(df["alpha_score"], errors="coerce")
        df = df.dropna(subset=["alpha_score"])
        df["rank"] = df["alpha_score"].rank(ascending=False)
        return df.sort_values("rank")

    def _compute_lookback(self, date: str, min_samples: int) -> str:
        """计算回看起始日期"""
        return (pd.Timestamp(date) - pd.Timedelta(days=min_samples * 2)).strftime("%Y-%m-%d")

    def _load_industry_map(self, instruments: str = "csi500") -> dict:
        """加载申万行业分类映射: normalized_symbol -> industry_name"""
        if instruments == "csi300":
            csv_path = "D:/AgentAlpha/signal-factory/data/industry/sw_industry_csi300.csv"
        else:
            csv_path = "D:/AgentAlpha/signal-factory/data/industry/sw_industry.csv"

        industry_map = {}
        try:
            df = pd.read_csv(csv_path, dtype=str)
            for _, row in df.iterrows():
                sym = _normalize_symbol(row["qlib_symbol"])
                industry_map[sym] = row["industry"]
        except Exception as e:
            print(f"  [行业分类] 加载失败: {e}")
        return industry_map

    def _neutralize_alpha_scores(
        self,
        ranked: pd.DataFrame,
        instruments: str = "csi500",
    ) -> pd.DataFrame:
        """
        行业中性化：对已排名的 alpha 分数进行行业中性化。
        输入和输出都是 DataFrame，包含 symbol 和 alpha_score 列。
        """
        industry_map = self._load_industry_map(instruments)
        if not industry_map:
            return ranked

        df = ranked.copy()
        # 统一符号格式
        df["instrument_norm"] = df["symbol"].apply(_normalize_symbol)
        df["industry"] = df["instrument_norm"].map(industry_map)
        before = len(df)
        df = df.dropna(subset=["industry"])
        after = len(df)
        dropped = before - after
        if dropped > 0:
            print(f"  [行业中性化] {dropped} 只标的无行业分类，已排除")

        if df.empty:
            return ranked

        # 按行业分组，减去行业均值
        df["alpha_score"] = df["alpha_score"] - df.groupby("industry")["alpha_score"].transform("mean")
        df = df.drop(columns=["industry", "instrument_norm"])

        # 重新排名
        df["rank"] = df["alpha_score"].rank(ascending=False)
        return df.sort_values("rank")

    def _build_output(
        self,
        ranked: pd.DataFrame,
        model_name: str,
        date: str,
        top_n: int = 50,
    ) -> Dict[str, Any]:
        """构建输出字典"""
        top = ranked.head(min(top_n, len(ranked)))

        # ── 检测低置信度股票（alpha 值相同说明模型无法区分）──
        score_counts = ranked["alpha_score"].value_counts()
        # 如果某个 alpha 值出现超过 5 次，且占总标的 10% 以上，标记为低置信度
        low_conf_threshold = max(5, len(ranked) * 0.1)
        low_conf_scores = set(
            score_counts[score_counts >= low_conf_threshold].index
        )

        def _build_record(row):
            rec = {"symbol": row["symbol"], "alpha_score": round(float(row["alpha_score"]), 6), "rank": int(row["rank"])}
            if row["alpha_score"] in low_conf_scores:
                rec["low_confidence"] = True
            return rec

        # ── 构建 @contract 元数据块 ──
        now = datetime.now()
        contract = self._build_contract(date, model_name, ranked, now)

        result = {
            "@contract": contract,
            "date": date,
            "model": model_name,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_stocks": len(ranked),
            "low_confidence_count": len(ranked[ranked["alpha_score"].isin(low_conf_scores)]),
            "top_10": [_build_record(r) for _, r in top.head(10).iterrows()],
            "top_50": [_build_record(r) for _, r in top.head(50).iterrows()],
            "all_scores": dict(zip(ranked["symbol"], ranked["alpha_score"])),
            "rankings": dict(zip(ranked["symbol"], ranked["rank"])),
        }
        return result

    def _build_contract(
        self, date: str, model_name: str, ranked: pd.DataFrame, now: datetime
    ) -> Dict[str, Any]:
        """构建 @contract 元数据块（v1.0.0 schema）"""
        from datetime import timedelta

        # 读取日历最新日期
        calendar_last = date  # fallback
        try:
            from src.data.config_loader import get_qlib_bin_dir
            bin_dir = Path(get_qlib_bin_dir())
            cal_path = bin_dir / "calendars" / "day.txt"
            if cal_path.exists():
                with open(cal_path) as f:
                    lines = f.readlines()
                if lines:
                    calendar_last = lines[-1].strip()
        except Exception:
            pass

        # 计算数据滞后天数
        try:
            pred_dt = datetime.strptime(date, "%Y-%m-%d")
            cal_dt = datetime.strptime(calendar_last[:10], "%Y-%m-%d")
            data_lag_days = (cal_dt - pred_dt).days
        except Exception:
            data_lag_days = -1

        # 信号有效期: as_of_date + 1 天 09:30
        try:
            pred_dt = datetime.strptime(date, "%Y-%m-%d")
            fresh_until = (pred_dt + timedelta(days=1)).strftime("%Y-%m-%d") + "T09:30:00+08:00"
        except Exception:
            fresh_until = "unknown"

        # 推断 universe: 从 ranked 中唯一前缀判断
        prefixes = set()
        for s in ranked["symbol"].head(100):
            if s.startswith("sh"):
                prefixes.add("sh")
            elif s.startswith("sz"):
                prefixes.add("sz")
        universe = "csi500" if len(ranked) > 300 else "csi300"

        return {
            "schema_version": "1.0.0",
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%S"),
            "as_of_date": date,
            "source": "quant-signal-factory/src/factors/alpha_signals.py",
            "universe": universe,
            "horizon": "5d",
            "model": model_name,
            "total_instruments": len(ranked),
            "staleness": {
                "design_lag_trading_days": self.label_horizon,
                "data_lag_calendar_days": data_lag_days,
                "excess_lag_days": max(0, data_lag_days - self.label_horizon - 2),
                "signal_fresh_until": fresh_until,
                "note": f"设计滞后={self.label_horizon}交易日(label horizon)，日历滞后={data_lag_days}d。excess_lag_days>0 表示流水线可能未按时运行。"
            },
        }

    def _save_signals(self, result: Dict[str, Any], output_path: Optional[str] = None):
        """保存 alpha 信号到 JSON 文件

        Args:
            result: 信号结果字典
            output_path: 可选的自定义输出路径，None 则使用默认路径
        """
        if output_path:
            path = Path(output_path)
        else:
            path = self.signals_dir / "alpha_signals.json"

        # 序列化 — 确保 numpy 类型可序列化
        def _serialize(obj):
            if isinstance(obj, (np.integer,)):
                return int(obj)
            if isinstance(obj, (np.floating,)):
                return float(obj)
            if isinstance(obj, np.ndarray):
                return obj.tolist()
            return str(obj)

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=_serialize)

        print(f"  信号已保存: {path}")

    def _save_model(self, model, path: Path):
        """保存模型到磁盘（含元数据）"""
        import pickle
        import sys

        # 收集库版本信息
        metadata = {
            "saved_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "python_version": sys.version.split()[0],
        }
        try:
            import qlib
            metadata["qlib_version"] = qlib.__version__
        except Exception:
            pass
        try:
            import xgboost
            metadata["xgboost_version"] = xgboost.__version__
        except Exception:
            pass
        try:
            import catboost
            metadata["catboost_version"] = catboost.__version__
        except Exception:
            pass

        with open(path, "wb") as f:
            pickle.dump({"metadata": metadata, "model": model}, f)
        print(f"  模型已保存: {path}")

    def _load_model(self, cfg: ModelConfig, instruments: str = "csi500"):
        """从磁盘加载模型（标的特定路径），检测版本不匹配"""
        import pickle
        import sys

        model_path = self._model_path(cfg, instruments)

        # 兼容旧格式（无标的标识的文件名），回退尝试
        if not model_path.exists():
            legacy_path = self.models_dir / f"{cfg.name}_{cfg.train_frequency}.pkl"
            if legacy_path.exists():
                model_path = legacy_path
        if not model_path.exists():
            return None

        with open(model_path, "rb") as f:
            data = pickle.load(f)

        # 兼容旧格式（直接保存的模型，无 metadata 包装）
        if isinstance(data, dict) and "model" in data:
            metadata = data.get("metadata", {})
            model = data["model"]
        else:
            metadata = {}
            model = data

        # ── 版本检查 ──
        if metadata:
            try:
                import qlib
                saved_qlib = metadata.get("qlib_version")
                if saved_qlib and saved_qlib != qlib.__version__:
                    print(f"  ⚠ Qlib 版本不匹配: 保存时 {saved_qlib}, 当前 {qlib.__version__}")
            except Exception:
                pass

            current_py = sys.version.split()[0]
            saved_py = metadata.get("python_version")
            if saved_py and saved_py != current_py:
                print(f"  ⚠ Python 版本不匹配: 保存时 {saved_py}, 当前 {current_py}")

        # ── 轻量级健康检查：验证模型内部状态有效 ──
        health = self._check_model_health(cfg, model)
        if not health["ok"]:
            print(f"  ⚠ 模型健康检查失败: {health['reason']}")
            return None

        return model

    def _check_model_health(self, cfg: ModelConfig, model) -> Dict[str, Any]:
        """
        加载后轻量级健康检查，验证模型内部状态是否有效。

        不依赖加载数据，仅检查模型对象的内部状态：
          - XGBoost: booster 对象存在且 num_features > 0
          - LightGBM: booster 对象存在且 num_trees > 0
          - CatBoost: 模型已 fit
          - LSTM: 模型参数已加载
        """
        name = cfg.name

        try:
            if name == "xgboost":
                # XGBoost: Qlib XGBModel stores booster in .model attribute
                booster = None
                if hasattr(model, "model") and model.model is not None:
                    booster = model.model
                elif hasattr(model, "booster") and model.booster is not None:
                    booster = model.booster

                if booster is not None:
                    nf = booster.num_features() if hasattr(booster, "num_features") else None
                    nr = booster.num_boosted_rounds() if hasattr(booster, "num_boosted_rounds") else None
                    if nf is None or nf == 0:
                        return {"ok": False, "reason": f"XGBoost num_features={nf}"}
                    return {"ok": True, "detail": f"XGBoost OK, {nf} features, {nr} rounds"}
                else:
                    return {"ok": False, "reason": "XGBoost booster/model is None"}

            elif name == "gbdt":
                # LightGBM: check if fitted
                if hasattr(model, "model") and model.model is not None:
                    try:
                        nt = model.model.num_trees()
                        if nt == 0:
                            return {"ok": False, "reason": "LightGBM has 0 trees"}
                        return {"ok": True, "detail": f"LightGBM OK, {nt} trees"}
                    except Exception:
                        return {"ok": True, "detail": "LightGBM loaded (trees check skipped)"}
                else:
                    return {"ok": False, "reason": "LightGBM model is None"}

            elif name == "catboost":
                # CatBoost: check if fitted
                if hasattr(model, "model") and model.model is not None:
                    try:
                        if model.model.is_fitted():
                            return {"ok": True, "detail": "CatBoost fitted"}
                        return {"ok": False, "reason": "CatBoost not fitted"}
                    except Exception:
                        return {"ok": True, "detail": "CatBoost loaded (fit check skipped)"}
                else:
                    return {"ok": False, "reason": "CatBoost model is None"}

            elif name in ("lstm", "tabnet"):
                # LSTM/TabNet: check model params exist
                if hasattr(model, "model") and model.model is not None:
                    return {"ok": True, "detail": f"{name} loaded"}
                return {"ok": False, "reason": f"{name} model is None"}

            else:
                # Unknown model type: assume OK
                return {"ok": True, "detail": "unknown model type, assumed OK"}

        except Exception as e:
            return {"ok": False, "reason": f"health check exception: {e}"}

    def get_latest_signals(self) -> Optional[Dict]:
        """获取最近生成的 alpha 信号"""
        signals_path = self.signals_dir / "alpha_signals.json"
        if not signals_path.exists():
            return None

        with open(signals_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_top_symbols(self, n: int = 10) -> List[str]:
        """获取 Top N 标的代码"""
        signals = self.get_latest_signals()
        if signals is None or "top_50" not in signals:
            return []

        return [s["symbol"] for s in signals["top_50"][:n]]

    # ─── Layer 1: 板块过滤（2026-09-09 新增）───

    def filter_by_sector(
        self,
        allowed_sectors: List[str],
        instruments: str = "csi500",
        date: Optional[str] = None,
        top_n: int = 50,
    ) -> Dict[str, Any]:
        """
        在指定板块内筛选 alpha 信号。

        用于 Layer 1 板块轮动集成：板块模型选出 Top 5 行业后，
        仅在这些行业内选股。

        Args:
            allowed_sectors: 允许的申万行业代码列表，如 ["801080", "801750"]
            instruments: 标的集合
            date: 日期
            top_n: 返回数量

        Returns:
            过滤后的 alpha 信号，格式同 run()
        """
        # 加载行业映射
        sector_map = self._load_industry_map(instruments)
        if sector_map.empty:
            return self.get_latest_signals() or {}

        # 获取全市场信号
        signals = self.get_latest_signals()
        if signals is None:
            return {}

        # 过滤
        filtered_top_50 = []
        filtered_all_scores = {}
        filtered_rankings = {}

        for item in signals.get("top_50", []):
            symbol = item["symbol"]
            # 标准化 symbol 格式
            norm_symbol = self._normalize_symbol_for_map(symbol)
            if norm_symbol in sector_map.index:
                sw_code = sector_map.loc[norm_symbol, "sw_code"]
                if isinstance(sw_code, pd.Series):
                    sw_code = sw_code.iloc[0]
                if str(sw_code) in allowed_sectors:
                    filtered_top_50.append(item)
                    filtered_all_scores[symbol] = signals["all_scores"].get(symbol)
                    filtered_rankings[symbol] = len(filtered_top_50)

        return {
            "date": signals.get("date"),
            "model": signals.get("model"),
            "generated_at": signals.get("generated_at"),
            "total_stocks": len(filtered_top_50),
            "filter_sectors": allowed_sectors,
            "top_10": filtered_top_50[:10],
            "top_50": filtered_top_50[:top_n],
            "all_scores": filtered_all_scores,
            "rankings": filtered_rankings,
            "models": signals.get("models", []),
            "status": "ok",
        }

    @staticmethod
    def _normalize_symbol_for_map(symbol: str) -> str:
        """标准化标的代码以匹配行业映射"""
        # sh600519 → 600519.SH
        symbol = symbol.lower()
        if symbol.startswith("sh"):
            return symbol[2:] + ".SH"
        elif symbol.startswith("sz"):
            return symbol[2:] + ".SZ"
        elif symbol.startswith("bj"):
            return symbol[2:] + ".BJ"
        return symbol


# ─── 便捷函数 ───


def generate_alpha_signals(
    model_name: str = "gbdt",
    instruments: str = "csi500",
    date: Optional[str] = None,
    train_first: bool = True,
    top_n: int = 50,
) -> Dict[str, Any]:
    """
    便捷函数：生成 alpha 信号。

    用法:
        signals = generate_alpha_signals("gbdt")
        print(signals["top_10"])
    """
    gen = AlphaSignalGenerator()
    return gen.run(model_name, instruments, date, train_first, top_n)


def generate_ensemble_signals(
    model_names: Optional[List[str]] = None,
    instruments: str = "csi500",
    date: Optional[str] = None,
    top_n: int = 50,
) -> Dict[str, Any]:
    """
    便捷函数：集成多模型生成 alpha 信号。
    """
    gen = AlphaSignalGenerator()
    return gen.run_ensemble(model_names, instruments, date, train_first=True, top_n=top_n)


def get_latest_alpha_signals() -> Optional[Dict]:
    """获取最近生成的 alpha 信号"""
    gen = AlphaSignalGenerator()
    return gen.get_latest_signals()


# ─── CLI ───

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Alpha 信号生成")
    parser.add_argument("--model", default="gbdt", help="模型名称 (gbdt/xgboost/lstm/tabnet)")
    parser.add_argument("--ensemble", action="store_true", help="使用集成模式")
    parser.add_argument("--models", nargs="+", help="集成模型列表")
    parser.add_argument("--instruments", default="csi500", help="标的范围")
    parser.add_argument("--date", help="预测日期 (YYYY-MM-DD)")
    parser.add_argument("--top-n", type=int, default=50, help="输出 Top N")
    parser.add_argument("--no-train", action="store_true", help="跳过训练，仅预测")
    parser.add_argument("--force-retrain", action="store_true", help="强制重训（忽略已缓存的模型文件）")
    parser.add_argument("--infer-only", action="store_true", help="纯特征推断，跳过 label 约束检查")
    parser.add_argument("--neutralize-industry", action="store_true", help="应用行业中性化（消除行业 beta）")
    parser.add_argument("--oos-weight", action="store_true", help="启用 OOS 自动降权集成（P1-8）")
    parser.add_argument("--output", help="输出 JSON 文件路径")
    args = parser.parse_args()

    gen = AlphaSignalGenerator()

    if args.ensemble:
        result = gen.run_ensemble(
            model_names=args.models,
            instruments=args.instruments,
            date=args.date,
            train_first=not args.no_train,
            top_n=args.top_n,
            infer_only=args.infer_only,
            neutralize_industry=args.neutralize_industry,
            output_path=args.output,
            force_retrain=args.force_retrain,
            oos_weight=args.oos_weight,
        )
    else:
        result = gen.run(
            model_name=args.model,
            instruments=args.instruments,
            date=args.date,
            train_first=not args.no_train,
            top_n=args.top_n,
            infer_only=args.infer_only,
            neutralize_industry=args.neutralize_industry,
            output_path=args.output,
            force_retrain=args.force_retrain,
        )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)
        print(f"结果已保存: {args.output}")

    if result.get("status") == "error":
        sys.exit(1)