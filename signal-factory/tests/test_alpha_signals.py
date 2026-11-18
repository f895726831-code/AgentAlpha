"""
AlphaSignals smoke tests — 验证核心信号生成功能
"""
import sys
from pathlib import Path

import pytest

# 确保项目根目录在 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.factors.alpha_signals import AlphaSignalGenerator
from src.factors.model_configs import (
    get_dynamic_dates,
    get_model_config,
    get_active_models,
    list_models,
)


class TestModelConfigs:
    """模型配置基础功能"""

    def test_get_dynamic_dates(self):
        end, fit_end = get_dynamic_dates()
        assert end is not None
        assert fit_end is not None
        assert end > fit_end  # end 应在 fit_end 之后
        assert len(end) == 10  # YYYY-MM-DD
        assert len(fit_end) == 10

    def test_get_model_config_valid(self):
        for name in ["gbdt", "xgboost", "catboost", "lstm", "tabnet", "gbdt_large"]:
            cfg = get_model_config(name)
            assert cfg is not None, f"Model {name} should exist"
            assert cfg.name == name
            assert cfg.display_name  # 应有显示名称

    def test_get_model_config_invalid(self):
        assert get_model_config("nonexistent") is None

    def test_get_active_models(self):
        active = get_active_models()
        assert isinstance(active, list)
        assert len(active) > 0
        assert "catboost" in active or "gbdt" in active

    def test_list_models(self):
        models = list_models()
        assert isinstance(models, list)
        assert len(models) >= 6
        assert "gbdt" in models
        assert "xgboost" in models


class TestAlphaSignalGenerator:
    """AlphaSignalGenerator 基础功能"""

    @pytest.fixture
    def gen(self, tmp_path):
        models_dir = tmp_path / "models"
        signals_dir = tmp_path / "signals"
        pred_dir = tmp_path / "predictions"
        return AlphaSignalGenerator(
            models_dir=str(models_dir),
            signals_dir=str(signals_dir),
            predictions_dir=str(pred_dir),
        )

    def test_init_creates_dirs(self, tmp_path):
        models_dir = tmp_path / "models"
        signals_dir = tmp_path / "signals"
        AlphaSignalGenerator(
            models_dir=str(models_dir),
            signals_dir=str(signals_dir),
        )
        assert models_dir.exists()
        assert signals_dir.exists()

    def test_get_latest_valid_prediction_date(self, gen):
        date = gen._get_latest_valid_prediction_date()
        if date is not None:
            assert len(date) == 10
            assert date.startswith("202")

    def test_resolve_prediction_date_auto_fallback(self, gen):
        """当传入日期超出 label 约束时应自动回退"""
        result = gen._resolve_prediction_date("2099-12-31")
        latest = gen._get_latest_valid_prediction_date()
        if latest:
            assert result == latest
        else:
            assert result == "2099-12-31"  # 无法读取日历时保持原值

    def test_resolve_prediction_date_valid(self, gen):
        """当传入日期在 label 约束内时应保持不变"""
        result = gen._resolve_prediction_date("2020-01-15")
        latest = gen._get_latest_valid_prediction_date()
        if latest and "2020-01-15" > latest:
            assert result == latest  # 仍然回退
        else:
            assert result == "2020-01-15"  # 或保持不变

    def test_compute_lookback(self, gen):
        result = gen._compute_lookback("2026-08-01", 500)
        assert result < "2026-08-01"
        assert len(result) == 10

    def test_predictions_to_ranked_empty(self, gen):
        import pandas as pd
        result = gen._predictions_to_ranked(pd.Series(dtype=float))
        assert result.empty
        assert list(result.columns) == ["symbol", "alpha_score", "rank"]

    def test_predictions_to_ranked_basic(self, gen):
        import pandas as pd
        import numpy as np
        idx = pd.MultiIndex.from_tuples(
            [("2026-08-07", "sh600519"), ("2026-08-07", "sz000001")],
            names=["datetime", "instrument"],
        )
        pred = pd.Series([0.05, -0.03], index=idx)
        result = gen._predictions_to_ranked(pred)
        assert len(result) == 2
        assert "symbol" in result.columns
        assert "alpha_score" in result.columns
        assert "rank" in result.columns
        # rank 应对应 alpha_score 降序
        assert result.iloc[0]["rank"] < result.iloc[1]["rank"]

    def test_predictions_to_ranked_multi_date(self, gen):
        """多个日期的预测应只取最新日期"""
        import pandas as pd
        idx = pd.MultiIndex.from_tuples(
            [("2026-08-01", "sh600519"), ("2026-08-07", "sh600519")],
            names=["datetime", "instrument"],
        )
        pred = pd.Series([0.05, 0.08], index=idx)
        result = gen._predictions_to_ranked(pred)
        assert len(result) == 1
        assert result.iloc[0]["alpha_score"] == 0.08

    def test_load_model_missing(self, gen):
        from src.factors.model_configs import get_model_config
        cfg = get_model_config("gbdt")
        assert gen._load_model(cfg) is None

    def test_train_invalid_model(self, gen):
        result = gen.train("nonexistent")
        assert result.get("error")

    def test_build_output_low_confidence(self, gen):
        """当多个股票 alpha 值相同时，应标记 low_confidence"""
        import pandas as pd
        ranked = pd.DataFrame({
            "symbol": ["sh600519", "sz000001", "sh600036", "sz000858", "sh601318",
                       "sz300750", "sh688981", "sh600276", "sh601012", "sh600030"],
            "alpha_score": [1.0, 1.0, 1.0, 1.0, 1.0,  # 5 个相同值
                            0.5, 0.3, 0.2, 0.1, 0.0],
            "rank": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        })
        result = gen._build_output(ranked, "test", "2026-08-07")
        assert result["low_confidence_count"] >= 5
        # 前 5 个的记录应有 low_confidence 标记
        low_conf = [r for r in result["top_10"] if r.get("low_confidence")]
        assert len(low_conf) >= 5

    def test_get_latest_signals_missing(self, gen):
        assert gen.get_latest_signals() is None

    def test_get_top_symbols_empty(self, gen):
        assert gen.get_top_symbols() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])