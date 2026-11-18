"""
hmm_divergence_monitor.py — HMM-宏观分歧监控与告警

当 HMM（从资产收益推断）与 LightGBM（从宏观指标推断）regime 判断不一致时：
  1. 记录分歧开始日期
  2. 分歧 ≤ 2 周：降置信度，继续使用 HMM
  3. 分歧 > 2 周：告警 + 建议切换至规则型 fallback + 自动降低风险资产权重

用法:
    from src.macro_allocation.hmm_divergence_monitor import DivergenceMonitor
    monitor = DivergenceMonitor()
    status = monitor.check()
    # status.diverged → True if HMM ≠ LightGBM
    # status.divergence_weeks → consecutive weeks of divergence
    # status.recommendation → "use_hmm" | "reduce_risk" | "fallback_rule"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

DATA_DIR = Path("D:/AgentAlpha/signal-factory/data/macro_assets")
STATE_FILE = DATA_DIR / "hmm_divergence_state.json"


@dataclass
class DivergenceStatus:
    """分歧状态"""

    hmm_regime: str = ""
    macro_regime: str = ""
    macro_confidence: float = 0.0
    diverged: bool = False
    divergence_weeks: int = 0
    divergence_start: Optional[str] = None  # ISO date

    # 风险等级
    level: str = "normal"  # normal / warning / alert
    recommendation: str = "use_hmm"  # use_hmm / reduce_risk / fallback_rule

    # 自动降仓参数
    risk_weight_multiplier: float = 1.0  # < 1.0 降风险资产权重
    notes: str = ""


class DivergenceMonitor:
    """
    HMM-宏观分歧监控器。

    每次调用 check() 记录当前分歧状态到磁盘。
    连续分歧超过阈值（2 周）触发告警和自动降仓。
    """

    def __init__(
        self,
        state_file: Optional[Path] = None,
        divergence_threshold_weeks: int = 2,
    ):
        self._state_file = state_file or STATE_FILE
        self._divergence_threshold = divergence_threshold_weeks

    def check(
        self,
        hmm_regime: Optional[str] = None,
        macro_regime: Optional[str] = None,
        macro_confidence: float = 0.0,
        today: Optional[str] = None,
    ) -> DivergenceStatus:
        """
        Check HMM vs macro divergence status.

        Args:
            hmm_regime: HMM regime (risk_on / neutral / risk_off)
            macro_regime: LightGBM macro regime prediction
            macro_confidence: LightGBM prediction confidence
            today: Date string (default: today)

        Returns:
            DivergenceStatus with recommendation
        """
        today = today or datetime.now().strftime("%Y-%m-%d")

        # Load previous state
        prev = self._load_state()

        # If no new data, return previous status
        if hmm_regime is None and macro_regime is None:
            return self._status_from_state(prev, today)

        # Determine current divergence
        if hmm_regime and macro_regime:
            diverged = (hmm_regime != macro_regime)
        else:
            # If either is missing, use HMM only
            diverged = False
            macro_regime = macro_regime or "unknown"

        # Update divergence streak
        if diverged:
            if prev.get("diverged"):
                # Continue streak
                weeks = prev.get("divergence_weeks", 0) + 1
                start = prev.get("divergence_start", today)
            else:
                # New divergence
                weeks = 1
                start = today
        else:
            # Reset
            weeks = 0
            start = None

        # Determine risk level and recommendation
        if not diverged:
            level = "normal"
            recommendation = "use_hmm"
            multiplier = 1.0
            notes = "HMM 与宏观一致，正常配置"
        elif weeks <= self._divergence_threshold:
            level = "warning"
            recommendation = "use_hmm"
            multiplier = 1.0
            notes = f"HMM({hmm_regime}) ≠ 宏观({macro_regime})，分歧 {weeks} 周 — 仍使用 HMM，置信度下调"
        else:
            level = "alert"
            recommendation = "fallback_rule"
            multiplier = 0.7  # Reduce risk asset weight by 30%
            notes = (
                f"HMM({hmm_regime}) ≠ 宏观({macro_regime})，分歧 {weeks} 周 > "
                f"{self._divergence_threshold} 周阈值 — 建议切换至规则型 fallback，"
                f"自动降低风险资产权重至 {multiplier:.0%}"
            )

        # Save state
        state = {
            "hmm_regime": hmm_regime or prev.get("hmm_regime", ""),
            "macro_regime": macro_regime or prev.get("macro_regime", ""),
            "macro_confidence": macro_confidence,
            "diverged": diverged,
            "divergence_weeks": weeks,
            "divergence_start": start,
            "last_checked": today,
            "level": level,
            "recommendation": recommendation,
        }
        self._save_state(state)

        return DivergenceStatus(
            hmm_regime=state["hmm_regime"],
            macro_regime=state["macro_regime"],
            macro_confidence=macro_confidence,
            diverged=diverged,
            divergence_weeks=weeks,
            divergence_start=start,
            level=level,
            recommendation=recommendation,
            risk_weight_multiplier=multiplier,
            notes=notes,
        )

    def check_macro(
        self,
        hmm_clf=None,
        today: Optional[str] = None,
    ) -> DivergenceStatus:
        """
        Convenience: run full check with loaded HMM + LightGBM models.

        Args:
            hmm_clf: HMMRegimeClassifier (already loaded)
            today: Date

        Returns:
            DivergenceStatus
        """
        today = today or datetime.now().strftime("%Y-%m-%d")

        # Get HMM regime
        if hmm_clf:
            hmm_regime = hmm_clf.get_current_regime()
        else:
            from src.macro_allocation.regime_hmm import HMMRegimeClassifier
            hmm_clf = HMMRegimeClassifier.load()
            hmm_regime = hmm_clf.get_current_regime()

        # Get LightGBM macro prediction
        macro_regime = None
        macro_confidence = 0.0
        try:
            macro_df = self._load_latest_macro()
            if macro_df is not None and len(macro_df) > 0:
                latest = macro_df.iloc[-1].dropna().to_dict()
                if latest:
                    hmm_clf_result = hmm_clf.classify_with_confidence(macro_features=latest)
                    # classify_with_confidence returns (regime, confidence, details)
                    # details has 'macro_regime' key
                    if isinstance(hmm_clf_result, tuple) and len(hmm_clf_result) >= 3:
                        details = hmm_clf_result[2]
                        macro_regime = details.get("macro_regime")
                        macro_confidence = details.get("macro_confidence", 0.0)
        except Exception:
            pass

        return self.check(
            hmm_regime=hmm_regime,
            macro_regime=macro_regime,
            macro_confidence=macro_confidence,
            today=today,
        )

    def _load_latest_macro(self) -> Optional[pd.DataFrame]:
        """Load latest macro data for LightGBM prediction."""
        fp = DATA_DIR / "edb_indicators.csv"
        if not fp.exists():
            return None
        df = pd.read_csv(fp, parse_dates=["date"], index_col="date")
        return df

    # ── State persistence ─────────────────────────────────────────────

    def _load_state(self) -> dict:
        if not self._state_file.exists():
            return {}
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_state(self, state: dict) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def _status_from_state(self, state: dict, today: str) -> DivergenceStatus:
        """Build DivergenceStatus from saved state."""
        weeks = state.get("divergence_weeks", 0)
        threshold = self._divergence_threshold

        if not state.get("diverged"):
            level, rec, mult = "normal", "use_hmm", 1.0
        elif weeks <= threshold:
            level, rec, mult = "warning", "use_hmm", 1.0
        else:
            level, rec, mult = "alert", "fallback_rule", 0.7

        return DivergenceStatus(
            hmm_regime=state.get("hmm_regime", ""),
            macro_regime=state.get("macro_regime", ""),
            macro_confidence=state.get("macro_confidence", 0.0),
            diverged=state.get("diverged", False),
            divergence_weeks=weeks,
            divergence_start=state.get("divergence_start"),
            level=level,
            recommendation=rec,
            risk_weight_multiplier=mult,
            notes=f"读取历史状态 (上次: {state.get('last_checked', 'unknown')})",
        )

    # ── Report ──────────────────────────────────────────────────────

    def generate_report(self, status: Optional[DivergenceStatus] = None) -> str:
        """Generate Markdown divergence report."""
        if status is None:
            status = self.check()

        lines = [
            "# HMM-宏观分歧监控报告",
            f"\n> 检查时间: {datetime.now().isoformat()}",
            f"\n## 当前状态",
            f"| 指标 | 值 |",
            f"|------|-----|",
            f"| HMM regime | {status.hmm_regime} |",
            f"| 宏观 regime | {status.macro_regime} |",
            f"| 宏观置信度 | {status.macro_confidence:.1%} |",
            f"| 分歧? | {'⚠️ 是' if status.diverged else '✅ 否'} |",
            f"| 连续分歧周数 | {status.divergence_weeks} |",
            f"| 分歧起始 | {status.divergence_start or 'N/A'} |",
            f"| 风险等级 | {status.level} |",
            f"| 建议 | {status.recommendation} |",
            f"| 风险权重乘数 | {status.risk_weight_multiplier:.0%} |",
            f"\n## 说明",
            f"\n{status.notes}",
        ]

        if status.recommendation == "fallback_rule":
            lines.extend([
                f"\n## ⚠️ 自动降仓已触发",
                f"\n分歧超过 {self._divergence_threshold} 周阈值，建议:",
                f"\n1. 切换至规则型 regime 分类器 (`--regime rule`)",
                f"\n2. 风险资产权重降至 {status.risk_weight_multiplier:.0%}",
                f"\n3. 增加现金/债券配置",
            ])

        return "\n".join(lines)