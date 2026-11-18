# SPDX-License-Identifier: MIT
# macro_regime.py — 宏观环境象限分类器 (Layer 0)
#
# 从宏观指标判断当前所处的宏观环境象限。
# 规则型（阈值驱动），非 ML —— 7 个资产类别截面太小，ML 不可行。
#
# 四维度判定：
#   增长 (Growth)     — PMI, GDP, 工业增加值
#   通胀 (Inflation)  — CPI, PPI
#   流动性 (Liquidity) — M2, 社融, 10Y国债, SHIBOR
#   风险偏好 (Risk Appetite) — CSI300 PE分位数, 期限利差
#
# 四象限 → 六类宏观环境 → 各类资产最优/最差排序
#
# Six Regimes:
#   | Regime     | Growth | Inflation | Liquidity   | Risk Appetite | Best  | Worst  |
#   |------------|--------|-----------|-------------|---------------|-------|--------|
#   | expansion  | ↑↑     | mild      | loose       | high          | ndx   | bond   |
#   | overheating| ↑↑     | ↑↑        | tightening  | high          | oil   | bond   |
#   | recovery   | ↑      | ↓         | loose       | recovering    | A股   | cash   |
#   | stagflation| ↓      | ↑↑        | tightening  | low           | gold  | equity |
#   | recession  | ↓↓     | ↓         | loose       | low           | bond  | equity |
#   | deflation  | ↓↓     | ↓↓        | ultra-loose | very low      | bond  | equity |

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd
import yaml


# ── 枚举定义 ────────────────────────────────────────────────────────


class MacroRegime(Enum):
    """六类宏观环境"""
    EXPANSION = "expansion"        # 扩张：增长↑↑ 通胀温和 流动性宽松
    OVERHEATING = "overheating"    # 过热：增长↑↑ 通胀↑↑ 流动性收紧
    RECOVERY = "recovery"          # 复苏：增长↑ 通胀↓ 流动性宽松
    STAGFLATION = "stagflation"    # 滞胀：增长↓ 通胀↑↑ 流动性收紧
    RECESSION = "recession"        # 衰退：增长↓↓ 通胀↓ 流动性宽松
    DEFLATION = "deflation"        # 通缩：增长↓↓ 通胀↓↓ 流动性极度宽松


class Direction(Enum):
    """维度方向指示"""
    UP = "↑"
    DOWN = "↓"
    FLAT = "→"
    STRONG_UP = "↑↑"
    STRONG_DOWN = "↓↓"


# ── 中文标签 ────────────────────────────────────────────────────────

REGIME_LABELS: Dict[MacroRegime, str] = {
    MacroRegime.EXPANSION: "扩张",
    MacroRegime.OVERHEATING: "过热",
    MacroRegime.RECOVERY: "复苏",
    MacroRegime.STAGFLATION: "滞胀",
    MacroRegime.RECESSION: "衰退",
    MacroRegime.DEFLATION: "通缩",
}

REGIME_LABELS_EN: Dict[MacroRegime, str] = {
    MacroRegime.EXPANSION: "Expansion",
    MacroRegime.OVERHEATING: "Overheating",
    MacroRegime.RECOVERY: "Recovery",
    MacroRegime.STAGFLATION: "Stagflation",
    MacroRegime.RECESSION: "Recession",
    MacroRegime.DEFLATION: "Deflation",
}

# 宏观环境 → 资产偏好映射 (1=最优, 5=最差)
REGIME_ASSET_RANKING: Dict[MacroRegime, Dict[str, int]] = {
    MacroRegime.EXPANSION: {
        "nasdaq": 1, "hk_tech": 1, "a_share": 2, "star_50": 2,
        "nikkei": 2, "india": 2, "oil": 2, "gold": 3,
        "cb_bond": 4, "bond": 5, "cash": 5,
    },
    MacroRegime.OVERHEATING: {
        "oil": 1, "gold": 2, "a_share": 3, "star_50": 3,
        "cb_bond": 3, "nasdaq": 4, "hk_tech": 4, "nikkei": 4,
        "india": 4, "bond": 5, "cash": 4,
    },
    MacroRegime.RECOVERY: {
        "a_share": 1, "star_50": 1, "hk_tech": 2, "nasdaq": 3,
        "cb_bond": 2, "nikkei": 3, "india": 3, "oil": 3,
        "bond": 4, "gold": 4, "cash": 5,
    },
    MacroRegime.STAGFLATION: {
        "gold": 1, "cash": 2, "oil": 3,
        "bond": 4, "cb_bond": 4, "a_share": 4, "star_50": 5,
        "nasdaq": 5, "hk_tech": 5, "nikkei": 5, "india": 5,
    },
    MacroRegime.RECESSION: {
        "bond": 1, "gold": 2, "cash": 3,
        "oil": 4, "cb_bond": 4, "a_share": 5, "star_50": 5,
        "nasdaq": 5, "hk_tech": 5, "nikkei": 5, "india": 5,
    },
    MacroRegime.DEFLATION: {
        "bond": 1, "cash": 2, "gold": 3,
        "oil": 4, "cb_bond": 4, "a_share": 5, "star_50": 5,
        "nasdaq": 5, "hk_tech": 5, "nikkei": 5, "india": 5,
    },
}

# 向后兼容别名
REGIME_MAP = REGIME_ASSET_RANKING

# ── 数据类 ──────────────────────────────────────────────────────────


@dataclass
class DimensionJudgment:
    """单个维度的判定结果

    Attributes:
        value: 维度评分文字描述（如 "温和扩张" / "高通胀" / "宽松"）
        direction: 方向指示（↑↑ / ↑ / → / ↓ / ↓↓）
        judgment: 判定标签（如 "expansion" / "high" / "loose"）
        signal: 标准化信号强度 (-1 ~ +1)，正值表示利多权益资产
    """
    value: str = ""
    direction: Direction = Direction.FLAT
    judgment: str = ""
    signal: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "value": self.value,
            "direction": self.direction.value,
            "judgment": self.judgment,
            "signal": round(self.signal, 4),
        }


@dataclass
class MacroRegimeResult:
    """完整的宏观环境评估结果

    Attributes:
        regime: 判定的宏观环境枚举
        regime_label_cn: 中文标签（扩张/过热/复苏/滞胀/衰退/通缩）
        confidence: 置信度 0-1，越高说明四维度信号越一致
        growth: 增长维度判定
        inflation: 通胀维度判定
        liquidity: 流动性维度判定
        risk_appetite: 风险偏好维度判定
        asset_preference: 各资产的偏好评分 (-1 ~ +1)，正值为偏好
        summary: 一段中文摘要
    """
    regime: MacroRegime
    regime_label_cn: str = ""
    confidence: float = 0.0
    growth: DimensionJudgment = field(default_factory=DimensionJudgment)
    inflation: DimensionJudgment = field(default_factory=DimensionJudgment)
    liquidity: DimensionJudgment = field(default_factory=DimensionJudgment)
    risk_appetite: DimensionJudgment = field(default_factory=DimensionJudgment)
    asset_preference: Dict[str, float] = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime.value,
            "regime_label_cn": self.regime_label_cn,
            "regime_label_en": REGIME_LABELS_EN.get(self.regime, ""),
            "confidence": round(self.confidence, 4),
            "dimensions": {
                "growth": self.growth.to_dict(),
                "inflation": self.inflation.to_dict(),
                "liquidity": self.liquidity.to_dict(),
                "risk_appetite": self.risk_appetite.to_dict(),
            },
            "asset_preference": {
                k: round(v, 4) for k, v in self.asset_preference.items()
            },
            "summary": self.summary,
        }

    def get_best_assets(self, top_n: int = 2) -> list[str]:
        """获取最优资产列表"""
        ranked = sorted(self.asset_preference.items(), key=lambda x: -x[1])
        return [a for a, _ in ranked[:top_n]]

    def get_worst_assets(self, bottom_n: int = 2) -> list[str]:
        """获取最差资产列表"""
        ranked = sorted(self.asset_preference.items(), key=lambda x: x[1])
        return [a for a, _ in ranked[:bottom_n]]


# ── 默认阈值 ────────────────────────────────────────────────────────

DEFAULT_THRESHOLDS: Dict[str, Dict[str, float]] = {
    "growth": {
        "pmi_boom": 50.0,
        "pmi_bust": 48.0,
        "gdp_boom": 6.0,
        "gdp_bust": 4.0,
        "ip_boom": 5.0,
        "ip_bust": 2.0,
    },
    "inflation": {
        "cpi_high": 3.0,
        "cpi_low": 1.0,
        "ppi_deflation": -1.0,
        "ppi_high": 5.0,
    },
    "liquidity": {
        "m2_loose": 10.0,
        "m2_tight": 8.0,
        "sfm_strong": 2.0,
        "shibor_tight": 2.0,
        "shibor_loose": 1.2,
    },
    "risk": {
        "bond_yield_high": 3.5,
        "bond_yield_low": 2.5,
        "pe_high": 80,
        "pe_low": 30,
    },
}

# ── 配置加载 ────────────────────────────────────────────────────────


def _resolve_config_path(config_path: Optional[str] = None) -> Optional[Path]:
    """解析配置文件路径"""
    if config_path is not None:
        p = Path(config_path)
        if p.exists():
            return p
        return None

    # 默认路径
    candidates = [
        Path("config/macro_allocation.yaml"),
    ]
    # 尝试基于本文件位置推断项目 root
    try:
        project_root = Path(__file__).resolve().parent.parent.parent
        candidates.insert(0, project_root / "config" / "macro_allocation.yaml")
    except (NameError, AttributeError):
        pass

    for c in candidates:
        if c.exists():
            return c
    return None


def _load_thresholds(config_path: Optional[str] = None) -> Dict[str, Dict[str, float]]:
    """从 YAML 加载阈值配置，找不到则返回默认值

    Args:
        config_path: YAML 配置文件路径，为 None 时使用默认路径

    Returns:
        四维度的阈值字典
    """
    cfg_path = _resolve_config_path(config_path)
    if cfg_path is None:
        return DEFAULT_THRESHOLDS

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception:
        return DEFAULT_THRESHOLDS

    macro_regime_cfg = config.get("macro_regime", {})
    if not macro_regime_cfg:
        # fallback: try nested under macro_allocation key (legacy config)
        macro_regime_cfg = config.get("macro_allocation", {}).get("macro_regime", {})
    if not macro_regime_cfg:
        return DEFAULT_THRESHOLDS

    # 合并：用 YAML 值覆盖默认值
    merged = {
        "growth": {**DEFAULT_THRESHOLDS["growth"], **macro_regime_cfg.get("growth", {})},
        "inflation": {**DEFAULT_THRESHOLDS["inflation"], **macro_regime_cfg.get("inflation", {})},
        "liquidity": {**DEFAULT_THRESHOLDS["liquidity"], **macro_regime_cfg.get("liquidity", {})},
        "risk": {**DEFAULT_THRESHOLDS["risk"], **macro_regime_cfg.get("risk_appetite", {})},
    }
    return merged


# =============================================================================
# MacroRegimeClassifier — 规则型宏观象限分类器
# =============================================================================


class MacroRegimeClassifier:
    """规则型宏观环境分类器

    不依赖 ML，不使用 Z-score / 历史数据。
    接收一个宏观指标快照字典，按配置的阈值逐维度判定，
    再通过规则表映射到六类宏观环境。

    Parameters
    ----------
    config_path : str or None
        macro_allocation.yaml 的路径，为 None 时自动查找默认路径。

    Usage
    -----
    >>> classifier = MacroRegimeClassifier()
    >>> indicators = {
    ...     "pmi_manufacturing": 50.5,
    ...     "cpi_yoy": 2.1,
    ...     "m2_yoy": 11.2,
    ...     ...
    ... }
    >>> result = classifier.classify(indicators, csi300_pe_percentile=65)
    >>> print(result.regime_label_cn)
    扩张
    """

    # ── 指标名称 → 英文 key 映射（兼容 macro_data.py get_latest_indicators 输出）────
    # 支持两种命名约定

    _INDICATOR_ALIASES = {
        # 英文 key → canonical
        "pmi_manufacturing": "pmi_manufacturing",
        "cpi_yoy": "cpi_yoy",
        "ppi_yoy": "ppi_yoy",
        "m2_yoy": "m2_yoy",
        "social_financing_yoy": "social_financing_yoy",
        "gdp_yoy": "gdp_yoy",
        "industrial_production_yoy": "industrial_production_yoy",
        "bond_10y_yield": "bond_10y_yield",
        "shibor_overnight": "shibor_overnight",
        # 中文别名
        "制造业PMI": "pmi_manufacturing",
        "CPI同比": "cpi_yoy",
        "PPI同比": "ppi_yoy",
        "M2同比": "m2_yoy",
        "社融增量_亿": "social_financing_yoy",
        "GDP当季同比": "gdp_yoy",
        "工业增加值同比": "industrial_production_yoy",
        "10Y国债收益率": "bond_10y_yield",
        "SHIBOR_ON": "shibor_overnight",
    }

    def __init__(self, config_path: Optional[str] = None):
        """初始化分类器，加载阈值配置"""
        self.thresholds = _load_thresholds(config_path)
        self._config_path = config_path

    # ── 主入口 ───────────────────────────────────────────────────

    def classify(
        self,
        indicators: Dict[str, float],
        csi300_pe_percentile: Optional[float] = None,
    ) -> MacroRegimeResult:
        """判读当前宏观象限

        Args:
            indicators: 指标名称 → 最新值的字典。
                支持英文 key（pmi_manufacturing, cpi_yoy, ...）
                或中文 key（制造业PMI, CPI同比, ...）。
            csi300_pe_percentile: 沪深300 PE 历史分位 (0-100)，可选。

        Returns:
            MacroRegimeResult: 包含象限、置信度、维度判定、资产偏好、摘要。

        Steps:
            1. 对四个维度分别判定 (growth / inflation / liquidity / risk_appetite)
            2. 按规则表映射到六类宏观环境
            3. 计算置信度（各维度信号一致程度）
            4. 按环境生成资产偏好
            5. 生成中文摘要
        """
        # 规范化指标 key
        normalized = self._normalize_indicators(indicators)

        # Step 1: 逐维度判定
        growth = self._judge_growth(normalized)
        inflation = self._judge_inflation(normalized)
        liquidity = self._judge_liquidity(normalized)
        risk = self._judge_risk_appetite(normalized, csi300_pe_percentile)

        # Step 2: 映射到象限
        regime, confidence = self._map_to_regime(growth, inflation, liquidity, risk)

        # Step 3-4: 资产偏好
        asset_pref = self._get_asset_preferences(regime)

        # Step 5: 组装结果
        result = MacroRegimeResult(
            regime=regime,
            regime_label_cn=REGIME_LABELS.get(regime, regime.value),
            confidence=confidence,
            growth=growth,
            inflation=inflation,
            liquidity=liquidity,
            risk_appetite=risk,
            asset_preference=asset_pref,
        )
        result.summary = self._write_summary(result)
        return result

    # ── 指标规范化 ──────────────────────────────────────────────

    def _normalize_indicators(self, indicators: Dict[str, float]) -> Dict[str, float]:
        """将中/英文指标名统一为英文 canonical key"""
        normalized: Dict[str, float] = {}
        for key, value in indicators.items():
            canonical = self._INDICATOR_ALIASES.get(key)
            if canonical is not None:
                normalized[canonical] = float(value)
            else:
                # 未知 key 也保留
                normalized[key] = float(value)
        return normalized

    # ── 维度判定方法 ─────────────────────────────────────────────

    def _judge_growth(self, indicators: Dict[str, float]) -> DimensionJudgment:
        """判断增长维度。

        指标：
        - pmi_manufacturing: >50 boom, <48 bust
        - gdp_yoy: >6% boom, <4% bust
        - industrial_production_yoy: >5% boom, <2% bust

        Returns:
            DimensionJudgment with signal [-1, +1]
        """
        thresholds = self.thresholds["growth"]
        pmi = indicators.get("pmi_manufacturing")
        gdp = indicators.get("gdp_yoy")
        ip_yoy = indicators.get("industrial_production_yoy")

        signals: list[float] = []

        # PMI
        if pmi is not None:
            if pmi >= thresholds["pmi_boom"]:
                signals.append(0.8)
            elif pmi >= thresholds["pmi_bust"]:
                signals.append(0.0)
            else:
                signals.append(-0.7)

        # GDP
        if gdp is not None:
            if gdp >= thresholds["gdp_boom"]:
                signals.append(0.7)
            elif gdp >= thresholds["gdp_bust"]:
                signals.append(0.2)
            else:
                signals.append(-0.6)

        # 工业增加值
        if ip_yoy is not None:
            if ip_yoy >= thresholds.get("ip_boom", 5.0):
                signals.append(0.5)
            elif ip_yoy >= thresholds.get("ip_bust", 2.0):
                signals.append(0.0)
            else:
                signals.append(-0.5)

        signal = float(np.clip(np.mean(signals), -1.0, 1.0)) if signals else 0.0

        # 方向 & 描述
        direction, value_str = self._signal_to_direction(signal, strong=0.5, weak=0.15)

        # 判定标签
        if signal > 0.15:
            judgment = "expansion"
        elif signal < -0.15:
            judgment = "contraction"
        else:
            judgment = "neutral"

        return DimensionJudgment(
            value=value_str,
            direction=direction,
            judgment=judgment,
            signal=signal,
        )

    def _judge_inflation(self, indicators: Dict[str, float]) -> DimensionJudgment:
        """判断通胀维度。

        指标：
        - cpi_yoy: >3% high, <1% low
        - ppi_yoy: <-1% deflation, >5% overheating

        Returns:
            DimensionJudgment with signal [-1, +1]
            （正值=有通胀压力，非权益利多方向）
        """
        thresholds = self.thresholds["inflation"]
        cpi = indicators.get("cpi_yoy")
        ppi = indicators.get("ppi_yoy")

        signals: list[float] = []

        # CPI
        if cpi is not None:
            if cpi >= thresholds["cpi_high"]:
                signals.append(0.8)
            elif cpi >= thresholds["cpi_low"]:
                signals.append(0.2)
            else:
                signals.append(-0.5)

        # PPI
        if ppi is not None:
            if ppi >= thresholds["ppi_high"]:
                signals.append(0.9)
            elif ppi >= 0:
                signals.append(0.2)
            elif ppi >= thresholds["ppi_deflation"]:
                signals.append(-0.2)
            else:
                signals.append(-0.7)

        signal = float(np.clip(np.mean(signals), -1.0, 1.0)) if signals else 0.0

        direction, value_str = self._signal_to_direction(signal, strong=0.5, weak=0.15)

        if signal > 0.5:
            judgment = "high_inflation"
        elif signal > 0.15:
            judgment = "moderate_inflation"
        elif signal > -0.5:
            judgment = "low_inflation"
        else:
            judgment = "deflation_pressure"

        return DimensionJudgment(
            value=value_str,
            direction=direction,
            judgment=judgment,
            signal=signal,
        )

    def _judge_liquidity(self, indicators: Dict[str, float]) -> DimensionJudgment:
        """判断流动性维度。

        指标：
        - m2_yoy: >10% loose, <8% tight
        - social_financing_yoy: 辅助参考（高=宽信用, >2万亿=强）
        - bond_10y_yield: >3.5% tight (risk_off), <2.5% loose
        - shibor_overnight: >2.0% tight, <1.2% loose

        Returns:
            DimensionJudgment with signal [-1, +1]
            （正值=宽松，利多权益）
        """
        thresholds = self.thresholds["liquidity"]
        m2 = indicators.get("m2_yoy")
        sf = indicators.get("social_financing_yoy")
        bond_yield = indicators.get("bond_10y_yield")
        shibor = indicators.get("shibor_overnight")

        signals: list[float] = []

        # M2
        if m2 is not None:
            if m2 >= thresholds["m2_loose"]:
                signals.append(0.7)
            elif m2 >= thresholds["m2_tight"]:
                signals.append(0.1)
            else:
                signals.append(-0.6)

        # 社融增量（万亿）
        if sf is not None:
            sfm_strong = thresholds.get("sfm_strong", 2.0)
            if sf >= sfm_strong:
                signals.append(0.5)
            elif sf >= 1.0:
                signals.append(0.1)
            elif sf >= 0.0:
                signals.append(0.0)
            else:
                signals.append(-0.4)

        # 10Y 国债收益率（越高=流动性越紧，反转符号）
        if bond_yield is not None:
            # bond_yield thresholds come from risk section in YAML
            risk_thresholds = self.thresholds.get("risk", {})
            yield_high = risk_thresholds.get("bond_yield_high", 3.5)
            yield_low = risk_thresholds.get("bond_yield_low", 2.5)
            if bond_yield >= yield_high:
                signals.append(-0.6)
            elif bond_yield >= yield_low:
                signals.append(0.0)
            else:
                signals.append(0.5)

        # SHIBOR 隔夜（越高=越紧，反转符号）
        if shibor is not None:
            s_tight = thresholds.get("shibor_tight", 2.0)
            s_loose = thresholds.get("shibor_loose", 1.2)
            if shibor >= s_tight:
                signals.append(-0.5)
            elif shibor >= s_loose:
                signals.append(0.0)
            else:
                signals.append(0.4)

        signal = float(np.clip(np.mean(signals), -1.0, 1.0)) if signals else 0.0

        direction, value_str = self._signal_to_direction(signal, strong=0.5, weak=0.15)

        if signal > 0.5:
            judgment = "ultra_loose"
        elif signal > 0.15:
            judgment = "loose"
        elif signal > -0.15:
            judgment = "neutral"
        elif signal > -0.5:
            judgment = "tight"
        else:
            judgment = "very_tight"

        return DimensionJudgment(
            value=value_str,
            direction=direction,
            judgment=judgment,
            signal=signal,
        )

    def _judge_risk_appetite(
        self,
        indicators: Dict[str, float],
        csi300_pe_percentile: Optional[float] = None,
    ) -> DimensionJudgment:
        """判断风险偏好维度。

        指标：
        - csi300_pe_percentile: >80 expensive, <30 cheap
        - bond_10y_yield: 用作风险偏好参考（利率下行=避险情绪）

        Returns:
            DimensionJudgment with signal [-1, +1]
            （正值=风险偏好高，利多权益）
        """
        thresholds = self.thresholds["risk"]
        pe_pct = csi300_pe_percentile

        signals: list[float] = []

        # PE 分位数
        if pe_pct is not None:
            pe_high = thresholds.get("pe_high", 80)
            pe_low = thresholds.get("pe_low", 30)
            if pe_pct >= pe_high:
                # 高 PE = 市场热情高 = 短期风险偏好高，但均值回归风险大
                # 这里是"情绪"维度，高 PE 反映高亢情绪
                signals.append(0.5)
            elif pe_pct >= pe_low:
                signals.append(0.0)
            else:
                # 低 PE = 市场恐惧 = 风险偏好低
                signals.append(-0.4)

        # 10Y 国债收益率作为辅助：利率下行=避险
        bond_yield = indicators.get("bond_10y_yield")
        if bond_yield is not None:
            yield_high = thresholds.get("bond_yield_high", 3.5)
            yield_low = thresholds.get("bond_yield_low", 2.5)
            # 收益率低于 2.5% → 避险情绪浓
            if bond_yield < yield_low:
                signals.append(-0.4)
            elif bond_yield < yield_high:
                signals.append(-0.1)
            else:
                signals.append(0.3)

        signal = float(np.clip(np.mean(signals), -1.0, 1.0)) if signals else 0.0

        direction, value_str = self._signal_to_direction(signal, strong=0.5, weak=0.15)

        if signal > 0.5:
            judgment = "high"
        elif signal > 0.15:
            judgment = "recovering"
        elif signal > -0.5:
            judgment = "subdued"
        else:
            judgment = "very_low"

        return DimensionJudgment(
            value=value_str,
            direction=direction,
            judgment=judgment,
            signal=signal,
        )

    # ── 象限映射规则 ─────────────────────────────────────────────

    def _map_to_regime(
        self,
        growth: DimensionJudgment,
        inflation: DimensionJudgment,
        liquidity: DimensionJudgment,
        risk: DimensionJudgment,
    ) -> Tuple[MacroRegime, float]:
        """将四维度判定映射到六类宏观环境。

        规则按优先级从高到低排列，第一条匹配即返回。

        Returns:
            (regime, confidence) — confidence 0-1 表示信号一致性
        """
        g = growth.signal
        i = inflation.signal
        l = liquidity.signal
        r = risk.signal

        # ── 规则表（优先级从高到低）────────

        # 过热：增长强 + 通胀高 + 流动性收紧
        if g > 0.3 and i > 0.3 and l < -0.2 and r > -0.3:
            regime = MacroRegime.OVERHEATING
            conf_signals = [1.0 if g > 0.3 else 0.5,
                           1.0 if i > 0.3 else 0.5,
                           1.0 if l < -0.2 else 0.5,
                           1.0 if r > -0.3 else 0.5]

        # 滞胀：增长弱 + 通胀高 + 流动性收紧
        elif g < -0.15 and i > 0.3 and l < -0.2:
            regime = MacroRegime.STAGFLATION
            conf_signals = [1.0 if g < -0.15 else 0.5,
                           1.0 if i > 0.3 else 0.5,
                           1.0 if l < -0.2 else 0.5,
                           1.0 if r < 0 else 0.5]

        # 通缩：增长弱 + 通胀极低
        elif g < -0.3 and i < -0.5:
            regime = MacroRegime.DEFLATION
            conf_signals = [1.0 if g < -0.3 else 0.5,
                           1.0 if i < -0.5 else 0.5,
                           1.0 if l > 0 else 0.5,
                           1.0 if r < -0.3 else 0.5]

        # 扩张：增长强 + 通胀不过高 + 流动性不紧
        # 必须在 RECOVERY 之前检查，避免强劲增长被错判为复苏
        elif g > 0.15 and l > -0.2 and i < 0.5:
            regime = MacroRegime.EXPANSION
            conf_signals = [1.0 if g > 0.15 else 0.5,
                           1.0 if i < 0.5 else 0.5,
                           1.0 if l > -0.2 else 0.5,
                           1.0 if r > -0.3 else 0.5]

        # 衰退：增长弱 + 通胀低 + 流动性宽松
        elif g < -0.3 and i < 0 and l > -0.15:
            regime = MacroRegime.RECESSION
            conf_signals = [1.0 if g < -0.3 else 0.5,
                           1.0 if i < 0 else 0.5,
                           1.0 if l > -0.15 else 0.5,
                           1.0 if r < 0 else 0.5]

        # 复苏：增长改善 + 通胀偏低 + 流动性宽松
        # 通胀必须低于中性 (i < 0.15) 以区别于扩张
        elif g > -0.15 and i < 0.15 and l > -0.15:
            regime = MacroRegime.RECOVERY
            conf_signals = [1.0 if g > -0.15 else 0.5,
                           1.0 if i < 0.15 else 0.5,
                           1.0 if l > -0.15 else 0.5,
                           1.0 if r > -0.3 else 0.5]

        # 兜底：根据增长和风险偏好综合判断
        elif r > 0.3 and g > 0:
            regime = MacroRegime.EXPANSION
            conf_signals = [0.5, 0.5, 0.5, 0.5]
        elif r < -0.5:
            regime = MacroRegime.RECESSION
            conf_signals = [0.5, 0.5, 0.5, 0.3]
        else:
            regime = MacroRegime.RECOVERY
            conf_signals = [0.3, 0.3, 0.3, 0.3]

        # 置信度 = 一致性信号均值
        confidence = float(np.mean(conf_signals))
        # 边界情况的额外折扣
        if g == 0.0 or l == 0.0 or i == 0.0:
            confidence = min(confidence, 0.7)

        return regime, confidence

    # ── 资产偏好 ─────────────────────────────────────────────────

    def _get_asset_preferences(self, regime: MacroRegime) -> Dict[str, float]:
        """根据宏观环境生成资产偏好评分 (-1 ~ +1)。

        排名 1 → +0.8, 2 → +0.4, 3 → 0.0, 4 → -0.4, 5 → -0.8
        """
        ranking = REGIME_ASSET_RANKING.get(regime, {})
        if not ranking:
            return {}

        rank_to_score = {
            1: 0.8,
            2: 0.4,
            3: 0.0,
            4: -0.4,
            5: -0.8,
        }
        return {asset: rank_to_score.get(rank, 0.0) for asset, rank in ranking.items()}

    # ── 摘要生成 ─────────────────────────────────────────────────

    def _write_summary(self, result: MacroRegimeResult) -> str:
        """生成一段中文宏观环境摘要"""
        g = result.growth
        i = result.inflation
        l = result.liquidity
        r = result.risk_appetite

        parts = [
            f"当前宏观环境判定为【{result.regime_label_cn}】（{REGIME_LABELS_EN.get(result.regime, '')}），置信度 {result.confidence:.0%}。",
            f"增长维度：{g.value}（{g.judgment}），",
            f"通胀维度：{i.value}（{i.judgment}），",
            f"流动性维度：{l.value}（{l.judgment}），",
            f"风险偏好：{r.value}（{r.judgment}）。",
        ]

        best = result.get_best_assets(2)
        worst = result.get_worst_assets(2)

        asset_names = {
            "nasdaq": "纳指ETF", "hk_tech": "恒生科技", "a_share": "A股沪深300",
            "oil": "石油ETF", "gold": "黄金ETF", "bond": "债券ETF", "cash": "现金/回购",
            "star_50": "科创50", "nikkei": "日经225", "india": "印度SENSEX", "cb_bond": "可转债",
        }

        best_str = "、".join(asset_names.get(a, a) for a in best)
        worst_str = "、".join(asset_names.get(a, a) for a in worst)

        parts.append(f"建议超配{best_str}，低配{worst_str}。")

        # 补充建议
        if result.confidence < 0.5:
            parts.append("当前各维度信号不一致，建议降低仓位等待更明确信号。")
        elif result.regime == MacroRegime.DEFLATION:
            parts.append("通缩环境下建议增配长久期债券和现金类资产，大幅减配权益和商品。")
        elif result.regime == MacroRegime.STAGFLATION:
            parts.append("滞胀环境下股债双杀风险高，建议以黄金和现金为主，避免重仓权益。")

        return "".join(parts)

    # ── 工具方法 ─────────────────────────────────────────────────

    @staticmethod
    def _signal_to_direction(
        signal: float,
        strong: float = 0.5,
        weak: float = 0.15,
    ) -> Tuple[Direction, str]:
        """将信号值转为 Direction 枚举和文字描述

        Args:
            signal: 信号值 [-1, +1]
            strong: 强趋势阈值
            weak: 弱趋势阈值

        Returns:
            (Direction, 中文描述)
        """
        if signal >= strong:
            return Direction.STRONG_UP, "强劲"
        elif signal >= weak:
            return Direction.UP, "温和向好"
        elif signal > -weak:
            return Direction.FLAT, "中性/平稳"
        elif signal >= -strong:
            return Direction.DOWN, "偏弱"
        else:
            return Direction.STRONG_DOWN, "疲弱"

    def get_thresholds_summary(self) -> Dict[str, Any]:
        """返回当前阈值配置摘要（用于调试/审查）"""
        return {
            "growth": dict(self.thresholds["growth"]),
            "inflation": dict(self.thresholds["inflation"]),
            "liquidity": dict(self.thresholds["liquidity"]),
            "risk": dict(self.thresholds["risk"]),
            "config_path": self._config_path or _resolve_config_path(),
        }

    def get_regime_summary(self, regime: MacroRegime) -> Dict[str, Any]:
        """获取给定宏观环境的概述信息"""
        ranking = REGIME_ASSET_RANKING.get(regime, {})
        ranked = sorted(ranking.items(), key=lambda x: x[1])
        return {
            "regime": regime.value,
            "label": REGIME_LABELS.get(regime, regime.value),
            "label_en": REGIME_LABELS_EN.get(regime, ""),
            "best_assets": [a for a, r in ranked if r == 1],
            "worst_assets": [a for a, r in ranked if r == 5],
            "asset_ranking": ranked,
        }


# =============================================================================
# 工厂函数
# =============================================================================

_classifier: Optional[MacroRegimeClassifier] = None


def load_regime_classifier(config_path: Optional[str] = None) -> MacroRegimeClassifier:
    """工厂函数：加载带有默认配置的分类器

    Args:
        config_path: YAML 配置文件路径，默认使用 config/macro_allocation.yaml

    Returns:
        配置好的 MacroRegimeClassifier 实例
    """
    return MacroRegimeClassifier(config_path=config_path)


def get_regime_classifier(config_path: Optional[str] = None) -> MacroRegimeClassifier:
    """获取模块级单例分类器（向后兼容 get_regime_classifier）"""
    global _classifier
    if _classifier is None:
        _classifier = MacroRegimeClassifier(config_path=config_path)
    return _classifier


# =============================================================================
# 向后兼容：保留旧 interface 的适配器
# =============================================================================


@dataclass
class RegimeScores:
    """四维度评分（向后兼容，等效于四个 DimensionJudgment）

    旧代码通过 ``classifier.classify(date) -> (MacroRegime, RegimeScores)``
    访问这些字段。新代码应使用 ``MacroRegimeResult``。
    """
    growth_score: float = 0.0       # -2 to +2
    inflation_score: float = 0.0    # -2 to +2
    liquidity_score: float = 0.0    # -2 to +2
    risk_appetite_score: float = 0.0  # -2 to +2

    @property
    def growth_direction(self) -> str:
        if self.growth_score > 0.5:
            return "expansion"
        elif self.growth_score < -0.5:
            return "contraction"
        return "neutral"

    @property
    def inflation_direction(self) -> str:
        if self.inflation_score > 0.5:
            return "high"
        elif self.inflation_score < -0.5:
            return "low"
        return "moderate"

    @property
    def liquidity_direction(self) -> str:
        if self.liquidity_score > 0.5:
            return "loose"
        elif self.liquidity_score < -0.5:
            return "tight"
        return "neutral"

    @classmethod
    def from_result(cls, result: MacroRegimeResult) -> "RegimeScores":
        """从 MacroRegimeResult 构建 RegimeScores（信号 ×2 映射到 -2~+2）"""
        return cls(
            growth_score=round(result.growth.signal * 2.0, 4),
            inflation_score=round(result.inflation.signal * 2.0, 4),
            liquidity_score=round(result.liquidity.signal * 2.0, 4),
            risk_appetite_score=round(result.risk_appetite.signal * 2.0, 4),
        )


class RegimeClassifier:
    """向后兼容的宏观环境分类器适配器

    保留旧 API：``classifier.classify(date) -> (MacroRegime, RegimeScores)``。
    内部委托给 ``MacroRegimeClassifier``，但需要历史 EDB DataFrame 作为数据源。

    新代码请直接使用 ``MacroRegimeClassifier.classify(indicators)``。
    """

    def __init__(
        self,
        collector: Any = None,
        lookback_months: int = 60,
    ):
        self.collector = collector
        self.lookback_months = lookback_months
        self._classifier = get_regime_classifier()
        self._edb_df: Optional[pd.DataFrame] = None

    def load_data(self) -> pd.DataFrame:
        """加载 EDB 指标数据（委托给 collector）"""
        if self.collector is None:
            from src.macro_allocation.macro_data import get_macro_collector
            self.collector = get_macro_collector()
        self._edb_df = self.collector.load_edb_cache()
        if self._edb_df is None:
            raise ValueError(
                "EDB data not cached. Run MacroDataCollector.collect_all_edb_indicators() first."
            )
        return self._edb_df

    def classify(
        self, date: Optional[str] = None
    ) -> Tuple[MacroRegime, "RegimeScores"]:
        """向后兼容：用历史 EDB 数据判定指定日期的宏观环境

        Args:
            date: 目标日期字符串 "YYYY-MM-DD"，为 None 时使用最新数据

        Returns:
            (MacroRegime, RegimeScores)
        """
        if self._edb_df is None:
            self.load_data()

        df = self._edb_df.ffill(limit=90)

        if date is not None:
            target_date = pd.Timestamp(date)
            available = df[df.index <= target_date]
            if available.empty:
                raise ValueError(f"No data available before {date}")
            latest = available.iloc[-1]
        else:
            latest = df.iloc[-1]

        # 将 DataFrame 行转为指标字典
        indicators: Dict[str, float] = {}
        for col in df.columns:
            val = latest.get(col)
            if pd.notna(val):
                indicators[str(col)] = float(val)

        # 计算 PE 分位数
        csi300_pe_pct = None
        if "沪深300PE_TTM" in df.columns:
            pe_series = df["沪深300PE_TTM"].dropna()
            if not pe_series.empty:
                latest_pe = float(latest.get("沪深300PE_TTM", float("nan")))
                if pd.notna(latest_pe):
                    csi300_pe_pct = (pe_series < latest_pe).mean() * 100.0

        # 委托给新分类器
        result = self._classifier.classify(indicators, csi300_pe_percentile=csi300_pe_pct)

        return result.regime, RegimeScores.from_result(result)

    def classify_history(self) -> pd.DataFrame:
        """返回历史各期的宏观环境分类（向后兼容）"""
        if self._edb_df is None:
            self.load_data()

        results = []
        for idx_date in self._edb_df.index:
            try:
                regime, scores = self.classify(idx_date.strftime("%Y-%m-%d"))
                results.append({
                    "date": idx_date,
                    "regime": regime.value,
                    "regime_label": REGIME_LABELS.get(regime, ""),
                    "growth_score": scores.growth_score,
                    "inflation_score": scores.inflation_score,
                    "liquidity_score": scores.liquidity_score,
                    "risk_appetite_score": scores.risk_appetite_score,
                })
            except Exception:
                continue

        return pd.DataFrame(results)

    def get_asset_ranking(self, regime: MacroRegime) -> list:
        """获取给定宏观环境下的资产偏好排序（向后兼容）"""
        ranking = REGIME_ASSET_RANKING.get(regime, {})
        return sorted(ranking.items(), key=lambda x: x[1])

    def get_regime_summary(self, regime: MacroRegime) -> Dict[str, Any]:
        """生成宏观环境概述（向后兼容）"""
        return self._classifier.get_regime_summary(regime)


# =============================================================================
# 快速判断工具
# =============================================================================

def classify_from_dict(
    indicators: Dict[str, float],
    csi300_pe_percentile: Optional[float] = None,
) -> MacroRegimeResult:
    """便捷函数：一行代码判断宏观象限

    Args:
        indicators: 宏观指标字典
        csi300_pe_percentile: CSI300 PE 分位数

    Returns:
        MacroRegimeResult
    """
    classifier = get_regime_classifier()
    return classifier.classify(indicators, csi300_pe_percentile=csi300_pe_percentile)


def is_risk_off(indicators: Dict[str, float]) -> bool:
    """快速判断是否处于 risk_off 环境

    当宏观环境为 stagflation / recession / deflation 时返回 True。
    """
    result = classify_from_dict(indicators)
    return result.regime in {
        MacroRegime.STAGFLATION,
        MacroRegime.RECESSION,
        MacroRegime.DEFLATION,
    }