"""
HMM Regime Classifier — unsupervised regime discovery from asset returns.

Replaces the rule-based StableRegimeClassifier with a Gaussian HMM trained
on 11-asset return data. Regimes are discovered from actual return
distributions, not from macro indicator thresholds (which failed ANOVA).

Architecture:
  - GaussianHMM (n_components=4, covariance_type="full")
  - Weekly returns for training (lower noise, ~400 obs)
  - Viterbi decoding for state sequence
  - Post-hoc state labeling by macro profile matching
  - Same RegimeHistory interface as StableRegimeClassifier

Dependencies: hmmlearn, joblib
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import joblib
import numpy as np
import pandas as pd
import sys
from hmmlearn.hmm import GaussianHMM

# Fix Windows GBK encoding
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Import the RegimeHistory types from the existing module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.macro_allocation.regime_backtest import (
    RegimeHistory,
    RegimePeriod,
    StableRegimeClassifier,
    STABLE_REGIMES,
    REGIME_LABELS_CN,
)

DEFAULT_DATA_DIR = Path("d:/AgentAlpha/signal-factory/data/macro_assets")
DEFAULT_MODEL_PATH = DEFAULT_DATA_DIR / "hmm_regime_model.pkl"

# Asset order (must match conditional_stats.py ASSET_ORDER)
ASSET_ORDER = [
    "oil", "gold", "bond", "nasdaq", "hk_tech",
    "a_share", "star_50", "nikkei", "india", "cb_bond",
]

# Equity-like assets for regime labeling
EQUITY_ASSETS = ["a_share", "star_50", "nasdaq", "hk_tech", "nikkei", "india"]
DEFENSIVE_ASSETS = ["bond", "cb_bond", "gold"]


class HMMRegimeClassifier:
    """
    HMM-based regime classifier.

    Drop-in replacement for StableRegimeClassifier.
    Same classify() → RegimeHistory interface.
    """

    def __init__(
        self,
        n_components: int = 4,
        returns_freq: str = "weekly",
        data_dir: Optional[Path] = None,
        model_path: Optional[Path] = None,
        random_state: int = 42,
        smooth_weeks: int = 4,
    ):
        """
        Args:
            n_components: Number of hidden states (3 or 4).
                          4 matches the STABLE risk_on/risk_off/stagflation/overheat taxonomy.
            returns_freq: "daily" or "weekly" — training frequency.
            data_dir: Path to macro_assets data directory.
            model_path: Path to save/load serialized model.
            random_state: Random seed for reproducibility.
            smooth_weeks: EMA half-life in weeks for feature smoothing.
                          Higher = more persistent regimes, less noise.
        """
        self.n_components = n_components
        self.returns_freq = returns_freq
        self.data_dir = data_dir or DEFAULT_DATA_DIR
        self.model_path = model_path or DEFAULT_MODEL_PATH
        self.random_state = random_state
        self.smooth_weeks = smooth_weeks

        self._model: Optional[GaussianHMM] = None
        self._state_labels: Dict[int, str] = {}
        self._daily_regime: Optional[pd.Series] = None
        self._macro_df: Optional[pd.DataFrame] = None
        self._transition_matrix: Optional[np.ndarray] = None

    # ── Data loading ───────────────────────────────────────────────────

    def _load_returns(self) -> pd.DataFrame:
        """Load 11-asset aligned returns, resample to target frequency."""
        from src.macro_allocation.asset_returns import AssetReturns

        ar = AssetReturns(data_dir=self.data_dir)
        returns_df = ar.load_aligned_returns(log_return=False)  # simple returns

        # Keep only the 10 non-cash assets
        cols = [c for c in ASSET_ORDER if c in returns_df.columns]
        if len(cols) < 8:
            raise ValueError(
                f"Expected >=8 assets, got {len(cols)}: {returns_df.columns.tolist()}"
            )
        returns_df = returns_df[cols].dropna(how="all")

        if self.returns_freq == "weekly":
            # Resample to weekly (Friday close): compound daily → weekly
            weekly = (1 + returns_df).resample("W-FRI").prod() - 1
            weekly = weekly.dropna(how="all")
            print(f"  Resampled to weekly: {len(weekly)} weeks "
                  f"({weekly.index[0].date()} → {weekly.index[-1].date()})")
            # Compute equity composite + rolling z-score for HMM input
            return self._build_features(weekly)
        elif self.returns_freq == "monthly":
            monthly = (1 + returns_df).resample("ME").prod() - 1
            monthly = monthly.dropna(how="all")
            print(f"  Resampled to monthly: {len(monthly)} months "
                  f"({monthly.index[0].date()} → {monthly.index[-1].date()})")
            # Build features with 12-month rolling window (vs 52-week for weekly)
            return self._build_features(monthly, rolling_window=12)
        else:
            return returns_df.dropna(how="all")

    def _build_features(self, returns: pd.DataFrame, rolling_window: int = 52) -> pd.DataFrame:
        """
        Build HMM input features from periodic returns.

        Args:
            returns: DataFrame of asset returns (weekly or monthly).
            rolling_window: Periods for rolling z-score (52 for weekly, 12 for monthly).

        Uses equity composite z-score + cross-asset vol as 2D features.
        This captures regime shifts better than raw multi-asset returns.
        """
        # Equity composite: average of equity-like assets
        equity_cols = [c for c in EQUITY_ASSETS if c in returns.columns]
        defensive_cols = [c for c in DEFENSIVE_ASSETS if c in returns.columns]

        features = pd.DataFrame(index=returns.index)
        features["equity_return"] = returns[equity_cols].mean(axis=1)
        features["defensive_return"] = returns[defensive_cols].mean(axis=1)

        # Rolling z-score of equity composite
        rolling_mean = features["equity_return"].rolling(rolling_window, min_periods=max(6, rolling_window//2)).mean()
        rolling_std = features["equity_return"].rolling(rolling_window, min_periods=max(6, rolling_window//2)).std()
        features["equity_zscore"] = (
            (features["equity_return"] - rolling_mean) / rolling_std.replace(0, 0.001)
        )

        # Cross-asset dispersion (high dispersion = stress regime)
        features["cross_sectional_vol"] = returns[equity_cols].std(axis=1)

        # Equity-defensive spread
        features["equity_defensive_spread"] = (
            features["equity_return"] - features["defensive_return"]
        )

        # Drop rows without enough rolling history
        features = features.dropna()

        # EMA smoothing to suppress weekly noise and get persistent regimes
        if self.smooth_weeks > 0:
            span = self.smooth_weeks
            for col in features.columns:
                features[col] = features[col].ewm(span=span, adjust=False).mean()
            # Drop NaN introduced by EMA warmup
            features = features.dropna()

        if len(features) < 100:
            raise ValueError(f"Too few observations after feature building: {len(features)}")

        print(f"  Features built: {len(features)} obs × {len(features.columns)} cols")
        print(f"  Equity z-score range: [{features['equity_zscore'].min():.2f}, "
              f"{features['equity_zscore'].max():.2f}]")
        return features

    def _load_macro_data(self) -> pd.DataFrame:
        """Load macro indicators for post-hoc state labeling."""
        if self._macro_df is not None:
            return self._macro_df

        fp = self.data_dir / "edb_indicators.csv"
        if not fp.exists():
            return pd.DataFrame()

        df = pd.read_csv(fp, parse_dates=["date"], index_col="date")
        self._macro_df = df
        return df

    # ── Training ───────────────────────────────────────────────────────

    def fit(self, returns_df: Optional[pd.DataFrame] = None) -> "HMMRegimeClassifier":
        """
        Train the HMM on asset returns.

        Args:
            returns_df: If None, loads from AssetReturns.

        Returns:
            self (for chaining)
        """
        if returns_df is None:
            returns_df = self._load_returns()

        print(f"\n{'='*60}")
        print(f"  Training HMM Regime Classifier")
        print(f"  n_components={self.n_components}, freq={self.returns_freq}")
        print(f"  Data: {len(returns_df)} obs × {len(returns_df.columns)} assets")
        print(f"{'='*60}")

        # Handle remaining NaN
        returns_clean = returns_df.fillna(0.0).values

        # Validate data
        if np.any(np.isinf(returns_clean)):
            returns_clean = np.nan_to_num(returns_clean, nan=0.0, posinf=0.1, neginf=-0.1)

        # Train GaussianHMM with multiple initializations to avoid local minima
        best_model = None
        best_score = -np.inf
        n_init = 5

        # "diag" covariance is more stable for small sample (751 weeks for 4 states)
        covariance_type = "diag"

        for init_idx in range(n_init):
            model = GaussianHMM(
                n_components=self.n_components,
                covariance_type=covariance_type,
                n_iter=2000,
                tol=1e-6,
                random_state=self.random_state + init_idx,
                verbose=False,
                init_params="stmc",  # random init for means, precisions
            )
            try:
                model.fit(returns_clean)
                score = model.score(returns_clean)
                if score > best_score:
                    best_score = score
                    best_model = model
            except Exception as e:
                print(f"    Init {init_idx}: failed ({e})")
                continue

        if best_model is None:
            raise RuntimeError("All HMM initializations failed")

        self._model = best_model
        print(f"  Converged: {self._model.monitor_.converged}")
        print(f"  Log likelihood: {best_score:.2f} (best of {n_init} inits)")

        # Store transition matrix
        self._transition_matrix = self._model.transmat_.copy()

        # Decode states
        states = self._model.predict(returns_clean)

        # Label states post-hoc
        self._label_states(states, returns_df)

        # Build daily regime series
        self._build_daily_regime(states, returns_df)

        # Post-process: merge regimes shorter than min_weeks
        self._merge_short_regimes(min_weeks=4)

        print(f"\n  State distribution (after merge):")
        vc = self._daily_regime.value_counts()
        for label, count in vc.items():
            print(f"    {label}: {count} days ({count/len(self._daily_regime)*100:.1f}%)")

        print(f"\n  State distribution:")
        for state_id in range(self.n_components):
            label = self._state_labels.get(state_id, f"state_{state_id}")
            pct = (states == state_id).mean() * 100
            duration = self._avg_state_duration(state_id)
            print(f"    State {state_id} → {label}: {pct:.1f}% "
                  f"(avg duration: {duration:.1f} weeks)")

        print(f"\n  Transition matrix:")
        for i in range(self.n_components):
            row = " ".join(f"{self._transition_matrix[i, j]:.3f}" for j in range(self.n_components))
            print(f"    {self._state_labels.get(i, f'S{i}')}: [{row}]")

        return self

    # ── State labeling ────────────────────────────────────────────────

    def _label_states(self, states: np.ndarray, data_df: pd.DataFrame) -> None:
        """
        Post-hoc label HMM states by return/feature characteristics.

        Handles two input types:
          - Features DataFrame (equity_zscore, cross_sectional_vol, …)
          - Raw returns DataFrame (a_share, nasdaq, …) — computes equity mean directly
        """
        is_features = "equity_zscore" in data_df.columns

        if is_features:
            state_stats = self._label_from_features(states, data_df)
        else:
            state_stats = self._label_from_returns(states, data_df)

        # Sort by equity_zscore or proxy (low → high)
        sort_key = "equity_zscore" if is_features else "equity_mean"
        sorted_states = sorted(state_stats.items(), key=lambda x: x[1][sort_key])

        if self.n_components == 4:
            labels = {
                sorted_states[0][0]: "risk_off",
                sorted_states[1][0]: "stagflation",
                sorted_states[2][0]: "risk_on",
                sorted_states[3][0]: "overheat",
            }
        elif self.n_components == 3:
            labels = {
                sorted_states[0][0]: "risk_off",
                sorted_states[1][0]: "neutral",
                sorted_states[2][0]: "risk_on",
            }
        else:  # n=2
            labels = {
                sorted_states[0][0]: "risk_off",
                sorted_states[1][0]: "risk_on",
            }

        self._state_labels = labels

        # Print diagnostic
        if is_features:
            self._print_state_profiles_features(state_stats, labels)
        else:
            self._print_state_profiles_returns(state_stats, labels)

    def _label_from_features(self, states, features_df):
        """Compute per-state stats from engineered features."""
        state_stats = {}
        for s in range(self.n_components):
            mask = states == s
            s_feat = features_df.values[mask]
            state_stats[s] = {
                "n_obs": mask.sum(),
                "equity_zscore": float(np.mean(s_feat[:, features_df.columns.get_loc("equity_zscore")])),
                "cross_vol": float(np.mean(s_feat[:, features_df.columns.get_loc("cross_sectional_vol")])),
                "equity_return": float(np.mean(s_feat[:, features_df.columns.get_loc("equity_return")])),
                "defensive_return": float(np.mean(s_feat[:, features_df.columns.get_loc("defensive_return")])),
                "spread": float(np.mean(s_feat[:, features_df.columns.get_loc("equity_defensive_spread")])),
            }
        return state_stats

    def _print_state_profiles_features(self, state_stats, labels):
        print(f"\n  Per-state profiles:")
        for s in range(self.n_components):
            ss = state_stats[s]
            label = labels.get(s, f"S{s}")
            ann_equity = (1 + ss["equity_return"]) ** 52 - 1
            ann_def = (1 + ss["defensive_return"]) ** 52 - 1
            print(f"    {label:12s}: z={ss['equity_zscore']:+.2f}, "
                  f"equity={ann_equity:+.1%}, def={ann_def:+.1%}, "
                  f"cross_vol={ss['cross_vol']:.4f}, "
                  f"spread={ss['spread']:+.4f}, n={ss['n_obs']}")

    def _label_from_returns(self, states, returns_df):
        """Compute per-state stats from raw asset returns."""
        equity_cols = [c for c in EQUITY_ASSETS if c in returns_df.columns]
        defensive_cols = [c for c in DEFENSIVE_ASSETS if c in returns_df.columns]

        state_stats = {}
        for s in range(self.n_components):
            mask = states == s
            s_ret = returns_df.values[mask]
            eq_mean = float(np.mean(s_ret[:, [returns_df.columns.get_loc(c) for c in equity_cols]]))
            def_mean = float(np.mean(s_ret[:, [returns_df.columns.get_loc(c) for c in defensive_cols]]))
            eq_vol = float(np.mean(np.std(s_ret[:, [returns_df.columns.get_loc(c) for c in equity_cols]], axis=0)))

            state_stats[s] = {
                "n_obs": mask.sum(),
                "equity_mean": eq_mean,
                "equity_vol": eq_vol,
                "defensive_mean": def_mean,
                "equity_zscore": eq_mean / max(eq_vol, 0.0001),  # proxy for labeling sort
            }
        return state_stats

    def _print_state_profiles_returns(self, state_stats, labels):
        print(f"\n  Per-state profiles (monthly):")
        for s in range(self.n_components):
            ss = state_stats[s]
            label = labels.get(s, f"S{s}")
            ann_eq = (1 + ss["equity_mean"]) ** 12 - 1
            ann_def = (1 + ss["defensive_mean"]) ** 12 - 1
            print(f"    {label:12s}: eq_mean={ss['equity_mean']:+.4f} "
                  f"(ann={ann_eq:+.1%}), def_mean={ss['defensive_mean']:+.4f} "
                  f"(ann={ann_def:+.1%}), eq_vol={ss['equity_vol']:.4f}, n={ss['n_obs']}")

    def _avg_state_duration(self, state_id: int) -> float:
        """Expected duration of state_id in weeks (1 / (1 - diag))."""
        if self._transition_matrix is None:
            return 0
        diag = self._transition_matrix[state_id, state_id]
        if diag >= 1.0:
            return float("inf")
        return 1.0 / (1.0 - diag) if diag < 1.0 else float("inf")

    def _build_daily_regime(
        self, states: np.ndarray, returns_df: pd.DataFrame
    ) -> None:
        """Build daily-frequency regime series from weekly HMM states."""
        weekly_idx = returns_df.index[:len(states)]
        regime_names = np.array([
            self._state_labels.get(s, f"state_{s}") for s in states
        ])

        weekly_regime = pd.Series(regime_names, index=weekly_idx)

        # Expand to daily: forward-fill + reindex to full calendar
        daily_idx = pd.date_range(
            start=weekly_idx[0], end=weekly_idx[-1], freq="B"
        )
        daily_regime = weekly_regime.reindex(daily_idx, method="ffill")
        daily_regime = daily_regime.ffill().bfill()

        self._daily_regime = daily_regime

    def _merge_short_regimes(self, min_weeks: int = 4) -> None:
        """
        Merge regime segments shorter than min_weeks into adjacent longer segments.

        This eliminates micro-switches from weekly HMM noise while preserving
        the overall regime structure. Operates on _daily_regime in-place.
        """
        if self._daily_regime is None or len(self._daily_regime) < min_weeks * 5:
            return

        min_days = min_weeks * 5  # approximate business days
        regime_s = self._daily_regime.copy()
        merged_count = 0

        # Find contiguous segments
        segments = []
        current_label = regime_s.iloc[0]
        start_idx = 0
        for i in range(1, len(regime_s)):
            if regime_s.iloc[i] != current_label:
                segments.append((start_idx, i - 1, current_label))
                current_label = regime_s.iloc[i]
                start_idx = i
        segments.append((start_idx, len(regime_s) - 1, current_label))

        # Merge short segments
        i = 0
        while i < len(segments):
            start, end, label = segments[i]
            duration = end - start + 1
            if duration < min_days:
                # Find best neighbor to merge into
                if i == 0:
                    # First segment: merge into next
                    target = segments[i + 1][2] if i + 1 < len(segments) else label
                elif i == len(segments) - 1:
                    # Last segment: merge into previous
                    target = segments[i - 1][2]
                else:
                    # Middle: merge into longer neighbor
                    prev_dur = segments[i - 1][1] - segments[i - 1][0] + 1
                    next_dur = segments[i + 1][1] - segments[i + 1][0] + 1
                    target = segments[i - 1][2] if prev_dur >= next_dur else segments[i + 1][2]

                # Apply merge
                regime_s.iloc[start:end + 1] = target
                merged_count += 1

                # Rebuild segments (could optimize but N is small)
                segments = []
                current_label = regime_s.iloc[0]
                start_idx = 0
                for j in range(1, len(regime_s)):
                    if regime_s.iloc[j] != current_label:
                        segments.append((start_idx, j - 1, current_label))
                        current_label = regime_s.iloc[j]
                        start_idx = j
                segments.append((start_idx, len(regime_s) - 1, current_label))
                i = 0  # restart scan
            else:
                i += 1

        self._daily_regime = regime_s
        if merged_count > 0:
            print(f"  Merged {merged_count} short regime(s) (< {min_weeks} weeks)")

    # ── Classification (same interface as StableRegimeClassifier) ──────

    def get_current_regime(self, date: Optional[Any] = None) -> str:
        """
        Get the current regime for real-time pipeline inference.

        Args:
            date: Optional date (datetime/str). Defaults to latest available.

        Returns:
            Regime label string (risk_on/neutral/risk_off).
        """
        if self._daily_regime is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        if date is not None:
            date = pd.Timestamp(date)
            # Find closest available date (backward fill)
            available = self._daily_regime.index[self._daily_regime.index <= date]
            if len(available) == 0:
                # Before training range: use first available
                return str(self._daily_regime.iloc[0])
            return str(self._daily_regime.loc[available[-1]])

        # Latest available
        return str(self._daily_regime.iloc[-1])

    def get_regime_transition_risk(self, horizon_months: int = 3) -> Dict[str, float]:
        """
        Estimate regime transition probabilities over horizon.

        Uses the transition matrix raised to horizon_months power.
        Returns probability distribution over regimes.
        """
        if self._transition_matrix is None:
            return {}
        current = self.get_current_regime()
        state_ids = {v: k for k, v in self._state_labels.items()}
        current_id = state_ids.get(current, 0)

        # T^horizon
        t_matrix = np.linalg.matrix_power(self._transition_matrix, horizon_months)
        probs = t_matrix[current_id]

        return {
            self._state_labels.get(i, f"state_{i}"): float(probs[i])
            for i in range(self.n_components)
        }

    def classify_with_confidence(
        self,
        macro_features: Optional[Dict[str, float]] = None,
    ) -> Tuple[str, float, Dict]:
        """
        Classify current regime with optional macro consistency check.

        Args:
            macro_features: Optional dict of macro indicators for consistency check.

        Returns:
            (regime, confidence, details) where details includes:
              - hmm_regime: HMM primary prediction
              - hmm_transition_risk: 3-month forward probabilities
              - macro_consistent: True if macro labeler agrees (if available)
              - macro_regime: LightGBM prediction (if available)
        """
        regime = self.get_current_regime()
        trans_risk = self.get_regime_transition_risk(horizon_months=3)

        # Base confidence: diagonal of transition matrix
        state_ids = {v: k for k, v in self._state_labels.items()}
        current_id = state_ids.get(regime, 0)
        base_conf = float(self._transition_matrix[current_id, current_id]) if self._transition_matrix is not None else 0.5

        details = {
            "hmm_regime": regime,
            "hmm_transition_risk": trans_risk,
            "macro_consistent": None,
            "macro_regime": None,
        }

        # Macro consistency check
        if macro_features:
            try:
                labeler = HMMLightGBMLabeler.load()
                macro_regime, macro_conf, _ = labeler.predict(macro_features)
                details["macro_regime"] = macro_regime
                details["macro_confidence"] = macro_conf
                details["macro_consistent"] = (macro_regime == regime)
                # Boost or penalize confidence
                if macro_regime == regime:
                    base_conf = min(base_conf + 0.1, 0.95)
                else:
                    base_conf = max(base_conf - 0.15, 0.3)
            except (FileNotFoundError, Exception):
                pass

        return regime, base_conf, details

    def classify(self, window: int = 24) -> RegimeHistory:
        """
        Run full-history regime classification.

        Returns:
            RegimeHistory with daily_labels, monthly_scores, periods, summary.
            Same interface as StableRegimeClassifier.classify().
        """
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        if self._daily_regime is None:
            raise RuntimeError("No regime labels. Call fit() first.")

        daily_labels = self._daily_regime.copy()

        # Compute confidence (based on state posterior probability)
        # For simplicity: use transition matrix diagonal as confidence proxy
        conf_map = {}
        for s in range(self.n_components):
            label = self._state_labels.get(s, f"state_{s}")
            diag = self._transition_matrix[s, s] if self._transition_matrix is not None else 0.9
            conf_map[label] = 0.5 + 0.4 * diag  # scale to [0.5, 0.9]

        conf_series = pd.Series(0.6, index=daily_labels.index)
        for label, conf in conf_map.items():
            conf_series[daily_labels == label] = conf

        # Build periods
        periods = []
        if len(daily_labels) > 0:
            current_regime = daily_labels.iloc[0]
            start_idx = daily_labels.index[0]
            for i in range(1, len(daily_labels)):
                if daily_labels.iloc[i] != current_regime:
                    end_idx = daily_labels.index[i - 1]
                    periods.append(RegimePeriod(
                        regime=current_regime,
                        start_date=str(start_idx.date()),
                        end_date=str(end_idx.date()),
                        n_days=(end_idx - start_idx).days,
                        confidence=float(conf_series.loc[start_idx:end_idx].mean()),
                    ))
                    current_regime = daily_labels.iloc[i]
                    start_idx = daily_labels.index[i]
            # Last period
            periods.append(RegimePeriod(
                regime=current_regime,
                start_date=str(start_idx.date()),
                end_date=str(daily_labels.index[-1].date()),
                n_days=(daily_labels.index[-1] - start_idx).days,
                confidence=float(conf_series.loc[start_idx:].mean()),
            ))

        # Summary
        summary = {}
        for r in STABLE_REGIMES:
            summary[r] = float((daily_labels == r).mean())

        # Monthly scores (placeholder — HMM doesn't produce monthly dimension scores)
        monthly_scores = pd.DataFrame(
            index=pd.date_range(
                daily_labels.index[0], daily_labels.index[-1], freq="ME"
            ),
            columns=["z_growth", "z_inflation", "z_rates", "z_risk"],
            data=0.0,
        )

        return RegimeHistory(
            daily_labels=daily_labels,
            monthly_scores=monthly_scores,
            periods=periods,
            summary=summary,
        )

    def get_daily_regime(self) -> pd.Series:
        """Get cached daily regime labels."""
        if self._daily_regime is None:
            raise RuntimeError("Model not trained. Call fit() first.")
        return self._daily_regime

    # ── Validation ────────────────────────────────────────────────────

    def validate_known_events(self, history: RegimeHistory) -> Dict[str, bool]:
        """Validate key historical events. Same interface as StableRegimeClassifier."""
        labels = history.daily_labels
        checks = {}

        # 2015 crash (June-Sep 2015)
        mask = (labels.index >= "2015-06-15") & (labels.index <= "2015-09-30")
        if mask.any():
            checks["2015_crash_risk_off"] = (labels[mask] == "risk_off").mean() > 0.3

        # 2020 COVID (Feb-Mar 2020)
        mask = (labels.index >= "2020-02-01") & (labels.index <= "2020-03-31")
        if mask.any():
            checks["2020_covid_risk_off"] = (labels[mask] == "risk_off").mean() > 0.3

        # 2020 Recovery (Jul-Dec 2020)
        mask = (labels.index >= "2020-07-01") & (labels.index <= "2020-12-31")
        if mask.any():
            checks["2020_recovery_risk_on"] = (labels[mask] == "risk_on").mean() > 0.3

        # 2024 924 rally (Sep-Oct 2024)
        mask = (labels.index >= "2024-09-24") & (labels.index <= "2024-10-31")
        if mask.any():
            checks["2024_924_risk_on"] = (labels[mask] == "risk_on").mean() > 0.3

        return checks

    def generate_report(self, history: Optional[RegimeHistory] = None) -> str:
        """Generate Markdown regime classification report."""
        now = datetime.now().isoformat()
        if history is None:
            history = self.classify()

        checks = self.validate_known_events(history)

        lines = [
            "# HMM Regime 分类报告",
            f"\n> 生成时间: {now}",
            f"\n> 方法: GaussianHMM (n={self.n_components}, freq={self.returns_freq})",
            f"\n> 日期范围: {history.daily_labels.index.min().date()} → "
            f"{history.daily_labels.index.max().date()}",
            f"\n## Regime 分布",
        ]
        # Count actual regimes present
        regime_counts = history.daily_labels.value_counts()
        all_regimes = list(STABLE_REGIMES) + ["neutral"]
        for r in all_regimes:
            pct = regime_counts.get(r, 0) / len(history.daily_labels) * 100
            if pct > 0 or r in STABLE_REGIMES:
                lines.append(f"| {r} | {REGIME_LABELS_CN.get(r, r)} | {pct:.1f}% |")

        lines.append(f"\n## 关键事件验证")
        for event, passed in checks.items():
            lines.append(f"| {event} | ... | {'✅ PASS' if passed else '❌ FAIL'} |")

        lines.append(f"\n## 最近 Regime 转换")
        for p in history.periods[-20:]:
            cn = REGIME_LABELS_CN.get(p.regime, p.regime)
            lines.append(f"| {p.start_date} | {p.end_date} | {cn} | "
                        f"{p.n_days}d | {p.confidence:.2f} |")

        return "\n".join(lines)

    # ── Serialization ──────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> Path:
        """Serialize trained model to disk."""
        path = path or self.model_path
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "n_components": self.n_components,
            "returns_freq": self.returns_freq,
            "smooth_weeks": self.smooth_weeks,
            "model": self._model,
            "state_labels": self._state_labels,
            "transition_matrix": self._transition_matrix,
            "daily_regime": self._daily_regime,
            "random_state": self.random_state,
        }
        joblib.dump(state, path)
        print(f"  Model saved to: {path}")
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "HMMRegimeClassifier":
        """Load trained model from disk."""
        path = path or DEFAULT_MODEL_PATH
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Model not found: {path}")

        state = joblib.load(path)
        obj = cls(
            n_components=state["n_components"],
            returns_freq=state.get("returns_freq", "weekly"),
            smooth_weeks=state.get("smooth_weeks", 4),
            data_dir=DEFAULT_DATA_DIR,
            model_path=path,
            random_state=state.get("random_state", 42),
        )
        obj._model = state["model"]
        obj._state_labels = state.get("state_labels", {})
        obj._transition_matrix = state.get("transition_matrix")
        obj._daily_regime = state.get("daily_regime")
        print(f"  Model loaded from: {path}")
        return obj


# ── Column name aliases for macro data ──────────────────────────────

COL_PMI = "制造业PMI"
COL_WTI = "WTI原油"
COL_10Y = "10Y国债收益率"
COL_SHIBOR = "SHIBOR_ON"
COL_CSI300_PE = "沪深300PE_TTM"
COL_M2_YOY = "M2同比"


# ── Factory function ─────────────────────────────────────────────────

def create_regime_classifier(method: str = "rule", **kwargs):
    """
    Factory: 'rule' → StableRegimeClassifier, 'hmm' → HMMRegimeClassifier.

    Usage:
        clf = create_regime_classifier("hmm")
        clf.fit()
        history = clf.classify()
    """
    if method == "hmm":
        return HMMRegimeClassifier(**kwargs)
    return StableRegimeClassifier(**kwargs)


# ═══════════════════════════════════════════════════════════════════════
# LightGBM Labeler — point-in-time daily regime classification
# ═══════════════════════════════════════════════════════════════════════

# Monthly macro features with good coverage for LightGBM training
LGBM_FEATURE_COLS = [
    "制造业PMI", "制造业PMI_同比", "非制造业PMI", "非制造业PMI_同比",
    "M2_余额_亿", "M2同比", "M2环比",
    "M1_余额_亿", "M1同比", "货币(M1)-环比增长",
    "10Y国债收益率", "沪深300PE_TTM",
]

# Optional features with shorter history
LGBM_EXTENDED_COLS = [
    "社会融资规模_亿", "新增人民币贷款", "新增企业债券融资",
]

LGBM_DEFAULT_PATH = DEFAULT_DATA_DIR / "hmm_lightgbm_labeler.pkl"


class HMMLightGBMLabeler:
    """
    LightGBM classifier that maps macro indicators → HMM regime labels.

    Trained on monthly macro data with HMM state labels as target.
    Provides daily point-in-time classification without look-ahead bias.
    """

    def __init__(
        self,
        hmm_classifier: Optional[HMMRegimeClassifier] = None,
        feature_cols: Optional[List[str]] = None,
        model_path: Optional[Path] = None,
        random_state: int = 42,
    ):
        self._hmm = hmm_classifier
        self._feature_cols = feature_cols or LGBM_FEATURE_COLS
        self._model_path = model_path or LGBM_DEFAULT_PATH
        self._random_state = random_state
        self._model = None
        self._label_encoder: Dict[str, int] = {}
        self._label_decoder: Dict[int, str] = {}
        self._feature_importance: Optional[pd.DataFrame] = None
        self._train_score: float = 0.0

    # ── Training ───────────────────────────────────────────────────────

    def fit(
        self,
        macro_df: Optional[pd.DataFrame] = None,
        hmm: Optional[HMMRegimeClassifier] = None,
        use_extended: bool = False,
        cv_folds: int = 5,
    ) -> "HMMLightGBMLabeler":
        """
        Train LightGBM on monthly macro → HMM regime mapping.

        Args:
            macro_df: Monthly macro indicators. Loads from EDB if None.
            hmm: Trained HMM classifier. Loads from disk if None.
            use_extended: Include 社融 features (shorter history).
            cv_folds: Cross-validation folds for evaluation.
        """
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("lightgbm not installed. Run: pip install lightgbm")

        hmm = hmm or self._hmm
        if hmm is None:
            hmm = HMMRegimeClassifier.load()

        # Load macro data
        if macro_df is None:
            macro_df = self._load_macro_monthly()

        # Build feature matrix
        if use_extended:
            feature_cols = self._feature_cols + [c for c in LGBM_EXTENDED_COLS if c in macro_df.columns]
        else:
            feature_cols = [c for c in self._feature_cols if c in macro_df.columns]

        # Get HMM labels at monthly frequency
        hmm_daily = hmm.get_daily_regime()
        monthly_labels = hmm_daily.resample("ME").last().dropna()
        monthly_labels = monthly_labels[monthly_labels.isin(["risk_on", "risk_off", "neutral"])]

        # Align
        common_idx = macro_df.index.intersection(monthly_labels.index)
        if len(common_idx) < 50:
            raise ValueError(f"Too few aligned obs: {len(common_idx)}")

        X = macro_df.loc[common_idx, feature_cols].copy()
        y = monthly_labels.loc[common_idx].values

        # Handle NaNs: forward-fill then drop remaining
        X = X.ffill().dropna()
        common_idx = X.index.intersection(common_idx)
        y = monthly_labels.loc[common_idx].values

        # Encode labels
        unique_labels = sorted(set(y))
        self._label_encoder = {label: i for i, label in enumerate(unique_labels)}
        self._label_decoder = {i: label for label, i in self._label_encoder.items()}
        y_encoded = np.array([self._label_encoder[l] for l in y])

        n_classes = len(unique_labels)
        print(f"\n{'='*60}")
        print(f"  Training LightGBM Labeler")
        print(f"  Samples: {len(X)}, Features: {len(feature_cols)}, Classes: {n_classes}")
        print(f"  Class distribution: {dict(zip(*np.unique(y, return_counts=True)))}")
        print(f"{'='*60}")

        # Train with CV
        params = {
            "objective": "multiclass",
            "num_class": n_classes,
            "metric": "multi_logloss",
            "boosting_type": "gbdt",
            "num_leaves": 8,
            "max_depth": 3,
            "learning_rate": 0.05,
            "n_estimators": 200,
            "min_child_samples": 15,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "random_state": self._random_state,
            "verbose": -1,
        }

        model = lgb.LGBMClassifier(**params)
        model.fit(X.values, y_encoded)

        # CV score
        from sklearn.model_selection import cross_val_score
        cv_scores = cross_val_score(
            model, X.values, y_encoded, cv=min(cv_folds, len(X) // 10),
            scoring="accuracy"
        )
        self._train_score = float(cv_scores.mean())
        print(f"  CV accuracy: {self._train_score:.1%} (±{cv_scores.std():.1%})")

        # Feature importance
        self._feature_importance = pd.DataFrame({
            "feature": feature_cols,
            "importance": model.feature_importances_,
        }).sort_values("importance", ascending=False)

        print(f"  Top 5 features:")
        for _, row in self._feature_importance.head(5).iterrows():
            print(f"    {row['feature']}: {row['importance']:.4f}")

        self._model = model
        self._feature_cols = feature_cols
        return self

    def _load_macro_monthly(self) -> pd.DataFrame:
        """Load monthly macro indicators."""
        fp = self._model_path.parent / "edb_indicators.csv"
        fp = DEFAULT_DATA_DIR / "edb_indicators.csv"
        if not fp.exists():
            raise FileNotFoundError(f"Macro data not found: {fp}")
        df = pd.read_csv(fp, parse_dates=["date"], index_col="date")
        monthly = df.resample("ME").last()
        return monthly

    # ── Prediction ─────────────────────────────────────────────────────

    def predict(self, features: Dict[str, float]) -> Tuple[str, float, Dict[str, float]]:
        """
        Point-in-time regime prediction from macro features.

        Args:
            features: Dict of macro indicator name → current value.

        Returns:
            (regime_label, confidence, class_probabilities)
        """
        if self._model is None:
            raise RuntimeError("Model not trained. Call fit() first.")

        # Build feature vector
        X = np.zeros((1, len(self._feature_cols)))
        for i, col in enumerate(self._feature_cols):
            X[0, i] = features.get(col, 0.0)

        probs = self._model.predict_proba(X)[0]
        pred_class = int(np.argmax(probs))
        regime = self._label_decoder[pred_class]
        confidence = float(probs[pred_class])

        prob_dict = {
            self._label_decoder[i]: float(probs[i])
            for i in range(len(probs))
        }
        return regime, confidence, prob_dict

    def predict_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Batch predict on a DataFrame of macro features.
        Returns DataFrame with predicted_regime, confidence, and per-class probabilities.
        """
        if self._model is None:
            raise RuntimeError("Model not trained.")

        X = df[self._feature_cols].ffill().fillna(0.0).values
        probs = self._model.predict_proba(X)
        preds = np.argmax(probs, axis=1)
        confs = probs[np.arange(len(preds)), preds]

        result = pd.DataFrame(index=df.index)
        result["predicted_regime"] = [self._label_decoder[p] for p in preds]
        result["confidence"] = confs
        for i in range(probs.shape[1]):
            result[f"prob_{self._label_decoder[i]}"] = probs[:, i]
        return result

    # ── Serialization ──────────────────────────────────────────────────

    def save(self, path: Optional[Path] = None) -> Path:
        path = path or self._model_path
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        state = {
            "model": self._model,
            "feature_cols": self._feature_cols,
            "label_encoder": self._label_encoder,
            "label_decoder": self._label_decoder,
            "feature_importance": self._feature_importance,
            "train_score": self._train_score,
        }
        joblib.dump(state, path)
        print(f"  LightGBM labeler saved to: {path}")
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "HMMLightGBMLabeler":
        path = path or LGBM_DEFAULT_PATH
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Labeler not found: {path}")

        state = joblib.load(path)
        obj = cls(feature_cols=state["feature_cols"], model_path=path)
        obj._model = state["model"]
        obj._label_encoder = state["label_encoder"]
        obj._label_decoder = state["label_decoder"]
        obj._feature_importance = state.get("feature_importance")
        obj._train_score = state.get("train_score", 0.0)
        print(f"  LightGBM labeler loaded from: {path}")
        return obj

    @property
    def feature_importance(self) -> Optional[pd.DataFrame]:
        return self._feature_importance

    @property
    def train_score(self) -> float:
        return self._train_score


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Train and evaluate
    clf = HMMRegimeClassifier(n_components=4, returns_freq="weekly")
    clf.fit()
    clf.save()

    history = clf.classify()
    print(clf.generate_report(history))

    # Quick ANOVA check
    print(f"\n\n## Regime Return Comparison")
    from src.macro_allocation.asset_returns import AssetReturns
    ar = AssetReturns()
    returns = ar.load_aligned_returns()

    common_idx = returns.index.intersection(history.daily_labels.index)
    returns_aligned = returns.loc[common_idx]
    regimes_aligned = history.daily_labels.loc[common_idx]

    for regime in STABLE_REGIMES:
        mask = regimes_aligned == regime
        if mask.sum() < 60:
            continue
        r = returns_aligned[mask]
        ann = r.mean() * 252 * 100
        print(f"\n{REGIME_LABELS_CN.get(regime, regime)} ({mask.sum()} 天):")
        for asset in ann.index:
            if asset != "cash":
                print(f"  {asset}: {ann[asset]:.1f}%")