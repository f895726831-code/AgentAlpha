"""
GFlowNet Reward Function — v4 Three-Layer Reward Engine

v4 (2026-11-13):
  Layer 1 — Quality Gate (must-pass): |IC| >= 0.015, coverage >= 80%, syntax valid
  Layer 2 — Alpha Score (quality): scaled IC + yearly stability + bull/bear consistency
  Layer 3 — Incremental Value (novelty): structural novelty via archive + pool correlation

  R = alpha_score * (1 + alpha * incremental_value)

v3 compatible: config.reward_mode="v3" uses old IC+diversity path.

Training flow:
  1. Syntax validation (FFO parser)
  2. FFO fast evaluation (IC)
  3. L1 Quality Gate → L2 Alpha Score → L3 Incremental Value
  4. Combined reward

Optimization: data preloaded once, FactorComputer used directly.
"""

import math
import hashlib
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple, Set

import numpy as np
import pandas as pd

from .config import GFNConfig
from .grammar import sequence_to_formula, validate_sequence
from .archive import MAPElitesArchive


# ─── Seed Pool ──────────────────────────────────────────────────

_FALLBACK_SEED_FORMULAS = [
    "SignedPower($returns, 2)",
    "Scale($returns)",
    "Ts_Mean($returns, 5)",
    "Rank($volume)",
    "Scale($volume)",
    "($close - Delay($close, 5)) / Delay($close, 5)",
    "($close - $vwap) / $vwap",
    "SignedPower(($close - $vwap) / $vwap, 2)",
    "Rank(($close - $vwap) / $vwap)",
]


def load_seed_pool(config: GFNConfig = None) -> list:
    """Load seed factor pool from seed_pool.json, fallback to hardcoded."""
    import json as _json
    search_paths = [
        Path(__file__).resolve().parent / "seed_pool.json",
        Path(__file__).resolve().parent / "seed_pool_fallback.json",
    ]
    for sp in search_paths:
        if sp.exists():
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    data = _json.load(f)
                seeds = data.get("seeds", [])
                if seeds:
                    result = [(s["formula"], float(s.get("ic_mean", 0.01)))
                              for s in seeds if s.get("formula")]
                    result.sort(key=lambda x: -abs(x[1]))
                    return result
            except Exception:
                continue
    return [(f, 0.02) for f in _FALLBACK_SEED_FORMULAS]


# ─── Regex Patterns ─────────────────────────────────────────────

_FIELD_PATTERN = re.compile(r'\$(close|open|high|low|volume|vwap|returns)')
_OPERATOR_PATTERN = re.compile(
    r'\b(Rank|Scale|Sign|Abs|Log|Ts_Mean|Ts_Std|Ts_Max|Ts_Min|Ts_Rank|'
    r'Ts_Sum|Ts_ArgMax|Ts_ArgMin|Delay|Delta|Return|Quantile|SignedPower|'
    r'Max|Min|If|Ts_Corr|Ts_Cov)\b'
)


def _extract_fields(formula: str) -> Set[str]:
    return set(_FIELD_PATTERN.findall(formula))


def _extract_operators(formula: str) -> Set[str]:
    return {m.lower() for m in _OPERATOR_PATTERN.findall(formula)}


def _get_struct_fingerprint(formula: str) -> str:
    """Generate L2 dedup structural fingerprint via factory.dedup."""
    try:
        from factory.dedup import structural_fingerprint
        return structural_fingerprint(formula)
    except Exception:
        return ""


# ─── RewardComputer (v3+v4 dual-mode) ────────────────────────────

class RewardComputer:
    """
    GFlowNet reward calculator — v3 and v4 dual-mode.

    v3 mode (reward_mode="v3"):
        R = w_ic * IC_score + w_div * diversity + w_val * validity
        (backward compatible, used for baseline experiment A)

    v4 mode (reward_mode="v4", default):
        L1: Quality Gate (hard pass/fail)
        L2: Alpha Score = scaled IC + stability penalty + bull/bear consistency
        L3: Incremental Value = structural novelty + pool correlation penalty
        R = alpha_score * (1 + alpha * incremental_value)

    Lifecycle:
        1. __init__ → preload Qlib data
        2. compute(formula) → single factor reward (auto-dispatch by mode)
        3. compute_batch(formulas) → batch reward
    """

    def __init__(self, config: GFNConfig, data_dir: str = None):
        self.config = config

        # Lazy import FFO modules
        self._ffo_loaded = False
        self._data = None
        self._returns = None
        self._computer = None
        self._data_dir = data_dir

        # v4: MAP-Elites Archive (created only in v4 mode)
        self.archive: Optional[MAPElitesArchive] = None
        self._use_v4 = getattr(config, 'reward_mode', 'v4') == 'v4'
        self._archive_enabled = getattr(config, 'archive_enabled', True)

        # L3 structural fingerprint tracking (always on in v4, used even without archive)
        self._fingerprints_seen: Set[str] = set()

        # Factor pool: [(formula, ic_mean)] — used in both modes
        self.factor_pool: List[Tuple[str, float]] = []

        # AST dedup: canonical formula hash → True (v3 mode)
        self._pool_hashes: Set[str] = set()

        # Structure signature cache: formula → (fields_set, operators_set)
        self._structure_cache: dict = {}

        # Pre-seed status
        self._seeded = False

        # Reward cache: formula_hash → (reward, pool_size_at_cache_time)
        self._reward_cache: dict = {}

        # Statistics
        self.total_evaluations = 0
        self.cache_hits = 0
        self.last_ic_mean: float = 0.0

        # v4: daily IC storage for L2 stability computation
        self._daily_ic_cache: dict = {}  # formula → [daily_ic_values]

    def _ensure_data_loaded(self):
        """Lazy-load Qlib data (one-time)."""
        if self._ffo_loaded:
            return

        from ffo.parser import parse as ffo_parse
        from ffo.data_loader import load_qlib_data, get_returns_matrix
        from ffo.computer import FactorComputer

        cfg = self.config

        # Warmup: 1 year before eval start
        data_start = pd.Timestamp(cfg.eval_start) - pd.DateOffset(days=365)
        data_start_str = data_start.strftime("%Y-%m-%d")

        self._data = load_qlib_data(
            data_dir=self._data_dir,
            universe=cfg.eval_universe,
            start=data_start_str,
            end=cfg.eval_end,
        )

        self._returns = get_returns_matrix(self._data)
        self._returns = self._returns[
            self._returns.index >= pd.Timestamp(cfg.eval_start)
        ]

        self._computer = FactorComputer(self._data)
        self._ffo_loaded = True

        # v4: create MAP-Elites Archive (if enabled)
        if self._use_v4 and self._archive_enabled and self.archive is None:
            self.archive = MAPElitesArchive(
                top_k=getattr(cfg, 'archive_top_k', 5),
                min_ic=getattr(cfg, 'archive_min_ic', 0.015),
            )

        # Seed pool
        if not self._seeded:
            self._seed_pool()

    # ── Public API ──────────────────────────────────────────

    def compute(self, formula: str) -> float:
        """Compute reward for a single factor. Auto-dispatches v3/v4."""
        fhash = hashlib.md5(formula.encode()).hexdigest()
        pool_size_key = len(self.factor_pool)
        if self._use_v4 and self._archive_enabled and self.archive:
            pool_size_key = self.archive.accepted_count
        elif self._use_v4:
            pool_size_key = len(self._fingerprints_seen)

        # Cache check
        if fhash in self._reward_cache:
            cached_reward, cached_pool_size = self._reward_cache[fhash]
            if cached_pool_size == pool_size_key:
                self.cache_hits += 1
                return cached_reward

        self.total_evaluations += 1

        # Dispatch
        if self._use_v4:
            reward = self._compute_v4(formula)
        else:
            reward = self._compute_v3(formula)

        self._reward_cache[fhash] = (reward, pool_size_key)
        return reward

    def compute_batch(self, formulas: List[str]) -> List[float]:
        """Batch reward computation."""
        return [self.compute(f) for f in formulas]

    def compute_from_sequence(
        self, token_indices: List[int]
    ) -> Tuple[float, Optional[str]]:
        """Compute reward from token sequence."""
        valid, formula_or_err = self._validate_and_build_formula(token_indices)
        if not valid:
            return -1.0, None
        reward = self.compute(formula_or_err)
        return reward, formula_or_err

    def update_pool(self, formula: str, ic_mean: float):
        """Manually add factor to pool with dedup."""
        if abs(ic_mean) > 0.01:
            ast_norm = self._normalize_ast(formula)
            if ast_norm not in self._pool_hashes:
                self._pool_hashes.add(ast_norm)
                self.factor_pool.append((formula, float(ic_mean)))

    def get_pool_stats(self) -> dict:
        """Get pool statistics (v3: global pool; v4: archive-driven)."""
        if self._use_v4 and self._archive_enabled and self.archive:
            stats = self.archive.coverage_stats()
            all_unique = self.archive.get_all_unique()
            ics = [abs(af.ic_mean) for af in all_unique] if all_unique else [0]
            return {
                "size": stats["total_unique"],
                "unique_structures": stats["structure_diversity"],
                "mean_ic": float(np.mean(ics)) if ics else 0,
                "max_ic": float(np.max(ics)) if ics else 0,
                "min_ic": float(np.min(ics)) if ics else 0,
                "categories": stats["categories"],
                "acceptance_rate": (
                    self.archive.accepted_count / max(self.archive.total_insertions, 1)
                ),
            }

        if self._use_v4:
            # v4 without archive: track via factor_pool + fingerprints
            ics = [abs(ic) for _, ic in self.factor_pool] if self.factor_pool else [0]
            return {
                "size": len(self.factor_pool),
                "unique_structures": len(self._fingerprints_seen),
                "mean_ic": float(np.mean(ics)) if ics else 0,
                "max_ic": float(np.max(ics)) if ics else 0,
                "min_ic": float(np.min(ics)) if ics else 0,
            }

        if not self.factor_pool:
            return {"size": 0, "mean_ic": 0, "max_ic": 0, "unique_structures": 0}

        ics = [abs(ic) for _, ic in self.factor_pool]
        return {
            "size": len(self.factor_pool),
            "unique_structures": len(self._pool_hashes),
            "mean_ic": float(np.mean(ics)),
            "max_ic": float(np.max(ics)),
            "min_ic": float(np.min(ics)),
        }

    def clear_cache(self):
        """Clear evaluation cache (call optionally after each epoch)."""
        self._reward_cache.clear()
        self.cache_hits = 0
        self._daily_ic_cache.clear()

    def rebuild_pool_hashes(self):
        """Rebuild _pool_hashes set from factor_pool (checkpoint restore)."""
        self._pool_hashes.clear()
        for formula, _ in self.factor_pool:
            ast_norm = self._normalize_ast(formula)
            self._pool_hashes.add(ast_norm)

    # ── v3 Reward Path (backward compatible) ────────────────

    def _compute_v3(self, formula: str) -> float:
        """v3 reward: IC score + diversity + validity (original logic)."""
        # 1. Syntax validation
        try:
            from ffo.parser import parse as ffo_parse
            ffo_parse(formula)
        except Exception:
            return -1.0

        # 2. Lazy-load data
        self._ensure_data_loaded()

        # 3. AST dedup check
        ast_norm = self._normalize_ast(formula)
        is_novel_structure = ast_norm not in self._pool_hashes

        # 4. Compute factor values + IC
        try:
            from ffo.parser import parse as ffo_parse
            ast = ffo_parse(formula)
            factor_values = self._computer.evaluate(ast)
            factor_values = factor_values[
                factor_values.index >= pd.Timestamp(self.config.eval_start)
            ]
            if factor_values.empty or factor_values.dropna(how="all").empty:
                return -1.0

            ic_mean = self._compute_ic_fast(factor_values)
            if np.isnan(ic_mean):
                return -1.0

            self.last_ic_mean = float(ic_mean)
        except Exception:
            return -1.0

        # 5. Diversity scoring
        ic_diversity = self._compute_ic_diversity(ic_mean)
        structural_diversity = self._compute_structural_diversity(formula)
        diversity = 0.5 * ic_diversity + 0.5 * structural_diversity

        if not is_novel_structure and structural_diversity < 0.3:
            diversity *= 0.3

        # 6. Combined reward
        w = self.config
        ic_score = min(abs(ic_mean) / 0.04, 1.0)
        reward = (
            w.reward_ic_weight * ic_score
            + w.reward_diversity_weight * diversity
            + w.reward_validity_weight * 1.0
        )
        reward = max(reward, w.reward_epsilon)

        # Factor pool update
        if abs(ic_mean) > 0.015 and is_novel_structure:
            self._pool_hashes.add(ast_norm)
            self.factor_pool.append((formula, float(ic_mean)))

        return reward

    # ── v4 Reward Path (three-layer) ────────────────────────

    def _compute_v4(self, formula: str) -> float:
        """
        v4 three-layer reward:

        L1: Quality Gate — syntax ok, |IC| >= 0.015, coverage >= 80%
        L2: Alpha Score — scaled IC + yearly stability + bull/bear consistency
        L3: Incremental Value — structural novelty + pool correlation penalty

        R = max(alpha_score * (1 + alpha * incremental_value), epsilon)
        """
        cfg = self.config

        # ── L1: Quality Gate ──
        l1_pass, ic_mean, daily_ics, factor_values = self._evaluate_l1(formula)
        if not l1_pass:
            return -1.0

        self.last_ic_mean = float(ic_mean)

        # ── L2: Alpha Score ──
        alpha_score = self._compute_l2_alpha_score(
            ic_mean, daily_ics, factor_values
        )

        # ── L3: Incremental Value ──
        fields = _extract_fields(formula)
        operators = _extract_operators(formula)
        fingerprint = _get_struct_fingerprint(formula)

        incremental_value = self._compute_l3_incremental(
            ic_mean, fields, operators, fingerprint
        )

        # ── Combined Reward ──
        alpha = getattr(cfg, 'reward_v4_l3_novelty_alpha', 0.5)
        reward = alpha_score * (1.0 + alpha * incremental_value)
        reward = max(reward, cfg.reward_epsilon)

        # ── Archive insertion / pool update ──
        if abs(ic_mean) >= cfg.archive_min_ic:
            if self._archive_enabled and self.archive is not None:
                self.archive.insert(
                    formula=formula,
                    ic_mean=float(ic_mean),
                    fields=fields,
                    operators=operators,
                    struct_fingerprint=fingerprint,
                    reward=float(reward),
                )
            # Track fingerprint regardless of archive
            if fingerprint:
                self._fingerprints_seen.add(fingerprint)
            # Always add to factor_pool for backward compat
            self.factor_pool.append((formula, float(ic_mean)))

        return reward

    def _evaluate_l1(self, formula: str) -> Tuple[bool, float, List[float], pd.DataFrame]:
        """
        L1 Quality Gate.

        Returns: (pass, ic_mean, daily_ics, factor_values)
        """
        # Syntax check
        try:
            from ffo.parser import parse as ffo_parse
            ffo_parse(formula)
        except Exception:
            return False, 0.0, [], pd.DataFrame()

        self._ensure_data_loaded()

        try:
            from ffo.parser import parse as ffo_parse
            ast = ffo_parse(formula)
            factor_values = self._computer.evaluate(ast)
            factor_values = factor_values[
                factor_values.index >= pd.Timestamp(self.config.eval_start)
            ]

            if factor_values.empty or factor_values.dropna(how="all").empty:
                return False, 0.0, [], pd.DataFrame()

            # Compute IC + daily ICs
            ic_mean, daily_ics = self._compute_ic_with_daily(factor_values)

            if np.isnan(ic_mean):
                return False, 0.0, [], pd.DataFrame()

        except Exception:
            return False, 0.0, [], pd.DataFrame()

        # L1 threshold
        min_ic = getattr(self.config, 'reward_v4_l1_min_ic', 0.015)
        if abs(ic_mean) < min_ic:
            return False, float(ic_mean), daily_ics, factor_values

        # Coverage check (non-NaN ratio)
        min_cov = getattr(self.config, 'reward_v4_l1_min_coverage', 0.80)
        total_cells = factor_values.size
        non_nan = factor_values.notna().sum().sum()
        coverage = non_nan / max(total_cells, 1)
        if coverage < min_cov:
            return False, float(ic_mean), daily_ics, factor_values

        return True, float(ic_mean), daily_ics, factor_values

    def _compute_l2_alpha_score(
        self,
        ic_mean: float,
        daily_ics: List[float],
        factor_values: pd.DataFrame,
    ) -> float:
        """
        L2 Alpha Score: scaled IC + stability + bull/bear consistency.

        Components:
          - IC scaling: min(|ic| / 0.04, 1.0)  (same as v3 scaling)
          - Yearly stability: 1.0 - min(std_yearly_ic / |mean_yearly_ic|, 1.0)
          - Bull/bear consistency: min(ic_bull/ic_bear, 2.0) if both positive

        Returns: alpha_score in [0, 1.0]
        """
        cfg = self.config

        # IC scaling (same as v3 steep scaling)
        ic_score = min(abs(ic_mean) / 0.04, 1.0)

        # Yearly IC stability
        stability = self._compute_yearly_stability(daily_ics, factor_values)
        stab_weight = getattr(cfg, 'reward_v4_l2_stability_weight', 0.3)

        # Bull/bear consistency
        bull_bear = self._compute_bull_bear_consistency(
            daily_ics, factor_values
        )
        bb_weight = getattr(cfg, 'reward_v4_l2_bullbear_weight', 0.2)

        # Combined L2 score
        ic_weight = 1.0 - stab_weight - bb_weight
        alpha_score = (
            ic_weight * ic_score
            + stab_weight * stability
            + bb_weight * bull_bear
        )

        return max(alpha_score, 0.001)

    def _compute_l3_incremental(
        self,
        ic_mean: float,
        fields: Set[str],
        operators: Set[str],
        fingerprint: str,
    ) -> float:
        """
        L3 Incremental Value: structural novelty + pool correlation.

        Components:
          - Structural novelty: 1.0 if fingerprint not seen, 0.2 if seen
          - Max pool correlation: penalty if similar IC to existing factors

        Returns: incremental_value in [0, 1.0]
        """
        cfg = self.config
        struct_weight = getattr(cfg, 'reward_v4_l3_structure_weight', 0.5)
        corr_weight = getattr(cfg, 'reward_v4_l3_max_corr_weight', 0.5)

        # Structural novelty
        struct_novelty = 1.0
        if fingerprint and fingerprint in self._fingerprints_seen:
            struct_novelty = 0.2  # Penalty for same structure

        # Pool correlation: IC similarity with existing factors
        if not self.factor_pool:
            ic_novelty = 1.0
        else:
            max_ic_sim = max(
                abs(1.0 - abs(ic_mean) / max(abs(pool_ic), 0.001))
                for _, pool_ic in self.factor_pool[-50:]  # Last 50
            )
            # Transform: high similarity → low novelty
            ic_novelty = max(0.0, 1.0 - min(max_ic_sim, 1.0))

        incremental = struct_weight * struct_novelty + corr_weight * ic_novelty
        return max(incremental, 0.0)

    # ── Shared IC computation ───────────────────────────────

    def _compute_ic_fast(self, factor_values: pd.DataFrame) -> float:
        """Fast daily mean Pearson IC."""
        forward_returns = self._returns.shift(-1)
        common_dates = factor_values.index.intersection(forward_returns.index)
        ic_daily = []
        for date in common_dates:
            fv = factor_values.loc[date].dropna()
            fr = forward_returns.loc[date].dropna()
            common = fv.index.intersection(fr.index)
            if len(common) < 30:
                continue
            ic = fv.loc[common].corr(fr.loc[common])
            if not np.isnan(ic):
                ic_daily.append(ic)
        if not ic_daily:
            return 0.0
        return float(np.mean(ic_daily))

    def _compute_ic_with_daily(
        self, factor_values: pd.DataFrame
    ) -> Tuple[float, List[float]]:
        """Compute mean IC + return daily IC series."""
        forward_returns = self._returns.shift(-1)
        common_dates = factor_values.index.intersection(forward_returns.index)
        ic_daily = []
        for date in common_dates:
            fv = factor_values.loc[date].dropna()
            fr = forward_returns.loc[date].dropna()
            common = fv.index.intersection(fr.index)
            if len(common) < 30:
                continue
            ic = fv.loc[common].corr(fr.loc[common])
            if not np.isnan(ic):
                ic_daily.append(float(ic))
        if not ic_daily:
            return 0.0, []
        return float(np.mean(ic_daily)), ic_daily

    # ── L2 sub-components ───────────────────────────────────

    def _compute_yearly_stability(
        self,
        daily_ics: List[float],
        factor_values: pd.DataFrame,
    ) -> float:
        """Yearly IC stability: 1.0 - cv_of_yearly_means."""
        if len(daily_ics) < 20:
            return 0.5  # Neutral for short history

        # Group ICs by year
        dates = factor_values.index[
            factor_values.index >= pd.Timestamp(self.config.eval_start)
        ]
        # We need to align dates with daily_ics
        yearly_means = []
        # Build date→IC mapping from the intersection dates
        fwd = self._returns.shift(-1)
        common_dates = factor_values.index.intersection(fwd.index)
        ic_by_date = {}
        idx = 0
        for date in common_dates:
            fv = factor_values.loc[date].dropna()
            fr = fwd.loc[date].dropna()
            common = fv.index.intersection(fr.index)
            if len(common) < 30:
                continue
            ic = fv.loc[common].corr(fr.loc[common])
            if not np.isnan(ic):
                ic_by_date[pd.Timestamp(date)] = float(ic)

        # Group by year
        from collections import defaultdict
        year_ics = defaultdict(list)
        for date, ic in ic_by_date.items():
            year_ics[date.year].append(ic)

        for yr_ics in year_ics.values():
            if len(yr_ics) >= 10:
                yearly_means.append(np.mean(yr_ics))

        if len(yearly_means) < 2:
            return 0.5

        mean_yearly = np.mean(yearly_means)
        std_yearly = np.std(yearly_means)
        if abs(mean_yearly) < 0.001:
            return 0.0

        cv = std_yearly / abs(mean_yearly)
        # Stability = 1.0 - cv, clamped to [0, 1]
        return max(0.0, min(1.0 - cv, 1.0))

    def _compute_bull_bear_consistency(
        self,
        daily_ics: List[float],
        factor_values: pd.DataFrame,
    ) -> float:
        """
        Bull/bear market IC consistency.

        Simplified: split eval period into first half (roughly bear-ish for CSI500)
        and second half (roughly bull-ish), compare IC means.
        """
        if len(daily_ics) < 20:
            return 0.5

        mid = len(daily_ics) // 2
        first_half = daily_ics[:mid]
        second_half = daily_ics[mid:]

        def sign_mean(ics):
            if not ics:
                return 0.0
            return np.mean(ics)

        sm1 = sign_mean(first_half)
        sm2 = sign_mean(second_half)

        # Both same sign → good
        if sm1 * sm2 > 0:
            # Penalize large magnitude differences
            ratio = min(abs(sm1), abs(sm2)) / max(abs(sm1), abs(sm2), 0.001)
            return 0.5 + 0.5 * ratio
        else:
            # Sign flip → penalty
            return 0.3

    # ── v3 Diversity sub-components (used by v3 path) ───────

    def _compute_ic_diversity(self, ic_mean: float) -> float:
        """IC-based diversity score (v3)."""
        if not self.factor_pool:
            return 1.0
        pool_size = len(self.factor_pool)
        ic_threshold = 0.005
        similar_count = sum(
            1 for _, pool_ic in self.factor_pool
            if abs(abs(pool_ic) - abs(ic_mean)) < ic_threshold
        )
        if similar_count == 0:
            return 1.0
        diversity = 1.0 - (similar_count / max(pool_size, 1))
        return max(diversity, 0.0)

    def _extract_structure(self, formula: str) -> Tuple[Set[str], Set[str]]:
        """Extract fields and operators from formula (cached)."""
        if formula in self._structure_cache:
            return self._structure_cache[formula]
        fields = _extract_fields(formula)
        operators = _extract_operators(formula)
        self._structure_cache[formula] = (fields, operators)
        return fields, operators

    def _compute_structural_diversity(self, formula: str) -> float:
        """Structural diversity via Jaccard distance (v3)."""
        if not self.factor_pool:
            return 1.0
        new_fields, new_ops = self._extract_structure(formula)
        if not new_fields and not new_ops:
            return 0.0
        distances = []
        for pool_formula, _ in self.factor_pool:
            pool_fields, pool_ops = self._extract_structure(pool_formula)
            # Field Jaccard distance
            if new_fields or pool_fields:
                field_overlap = len(new_fields & pool_fields)
                field_union = len(new_fields | pool_fields)
                field_distance = 1.0 - field_overlap / max(field_union, 1)
            else:
                field_distance = 1.0
            # Operator Jaccard distance
            if new_ops or pool_ops:
                op_overlap = len(new_ops & pool_ops)
                op_union = len(new_ops | pool_ops)
                op_distance = 1.0 - op_overlap / max(op_union, 1)
            else:
                op_distance = 1.0
            distance = 0.6 * field_distance + 0.4 * op_distance
            distances.append(distance)
        return min(distances) if distances else 1.0

    def _normalize_ast(self, formula: str) -> str:
        """Canonical AST hash for dedup (v3)."""
        try:
            from ffo.parser import parse as ffo_parse, ast_to_string
            ast = ffo_parse(formula)
            canonical = ast_to_string(ast)
            return hashlib.md5(canonical.encode()).hexdigest()
        except Exception:
            return hashlib.md5(formula.encode()).hexdigest()

    # ── Seed pool ───────────────────────────────────────────

    def _seed_pool(self):
        """Inject seed factors into pool/archive."""
        self._seeded = True
        self._reward_cache.clear()

        seed_formulas = load_seed_pool(self.config)
        cfg = self.config

        seeded_count = 0
        seeded_ic_sum = 0.0
        all_fields_used = set()
        all_ops_used = set()

        for formula, expected_ic in seed_formulas:
            try:
                from ffo.parser import parse as ffo_parse

                ffo_parse(formula)
                ast = ffo_parse(formula)
                factor_values = self._computer.evaluate(ast)
                factor_values = factor_values[
                    factor_values.index >= pd.Timestamp(cfg.eval_start)
                ]

                if factor_values.empty or factor_values.dropna(how="all").empty:
                    continue

                ic_mean = self._compute_ic_fast(factor_values)
                if np.isnan(ic_mean):
                    continue

                if abs(ic_mean) > 0.015:
                    fields = _extract_fields(formula)
                    ops = _extract_operators(formula)

                    if self._use_v4 and self.archive:
                        fp = _get_struct_fingerprint(formula)
                        accepted = self.archive.insert(
                            formula=formula,
                            ic_mean=float(ic_mean),
                            fields=fields,
                            operators=ops,
                            struct_fingerprint=fp,
                        )
                        if accepted:
                            self.factor_pool.append((formula, float(ic_mean)))
                            seeded_count += 1
                            seeded_ic_sum += abs(ic_mean)
                            all_fields_used.update(fields)
                            all_ops_used.update(ops)
                    else:
                        ast_norm = self._normalize_ast(formula)
                        if ast_norm not in self._pool_hashes:
                            self._pool_hashes.add(ast_norm)
                            self.factor_pool.append((formula, float(ic_mean)))
                            seeded_count += 1
                            seeded_ic_sum += abs(ic_mean)
                            all_fields_used.update(fields)
                            all_ops_used.update(ops)

            except Exception:
                continue

        if seeded_count > 0:
            avg_ic = seeded_ic_sum / seeded_count
            mode_tag = "v4" if self._use_v4 else "v3"
            print(
                f"[Seed {mode_tag}] {seeded_count}/{len(seed_formulas)} seeds added, "
                f"pool_size={len(self.factor_pool)}, "
                f"avg|IC|={avg_ic:.4f}, "
                f"fields={sorted(all_fields_used)}, "
                f"ops={len(all_ops_used)}",
                file=sys.stderr,
            )

    # ── Validation ──────────────────────────────────────────

    def _validate_and_build_formula(
        self, token_indices: List[int]
    ) -> Tuple[bool, Optional[str]]:
        """Validate sequence and build formula."""
        valid, err = validate_sequence(token_indices)
        if not valid:
            return False, None
        try:
            formula = sequence_to_formula(token_indices)
            return True, formula
        except Exception:
            return False, None