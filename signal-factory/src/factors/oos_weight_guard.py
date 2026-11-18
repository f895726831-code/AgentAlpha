"""
OOS 性能驱动的集成权重守卫 (P1-8)

追踪每个模型的 OOS (out-of-sample) Validation IC 滚动窗口，当某模型
IC 持续走低时自动降低其在 ensemble 中的权重。

核心设计原则:
  1. 等权为默认 — 新模型或无历史数据时从等权开始
  2. 渐进降权 — IC 衰减连续惩罚，设有 floor (30%)
  3. 自动恢复 — IC 回升时权重逐步恢复
  4. 持久化 — IC 历史和权重状态写入 JSON，跨重训周期保持
  5. 分池追踪 — CSI300/CSI500 分开记录（因子表不同，IC 不可比）

权重公式:
  rolling_ic = mean(最近 N 次 valid IC)
  penalty = max(0, target_ic - rolling_ic) / target_ic  # 0=无惩罚, 1=全惩罚
  raw_weight = max(floor, 1.0 / N_models - penalty / N_models)
  最终归一化使 Σweights = 1.0

用法:
    from src.factors.oos_weight_guard import OOSWeightGuard

    guard = OOSWeightGuard()
    guard.record_ic("catboost", "csi500", "2026-11-03", 0.028)
    weights = guard.compute_weights(["catboost", "xgboost", "gbdt"], "csi500")
    # => {"catboost": 0.35, "xgboost": 0.33, "gbdt": 0.32}
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── 默认配置 ───

_WEIGHT_DIR = Path("D:/AgentAlpha/signal-factory/data/output")
_DEFAULT_WINDOW = 4       # 滚动窗口大小（约 1 个月周度重训）
_DEFAULT_FLOOR = 0.30     # 最低权重（30%，不完全剔除）
_DEFAULT_TARGET_IC = 0.03 # 目标 IC 阈值（低于此值开始惩罚）


class OOSWeightGuard:
    """OOS 性能驱动的集成权重守卫"""

    def __init__(
        self,
        weight_dir: Optional[Path] = None,
        window: int = _DEFAULT_WINDOW,
        floor: float = _DEFAULT_FLOOR,
        target_ic: float = _DEFAULT_TARGET_IC,
    ):
        self._dir = weight_dir or _WEIGHT_DIR
        self._dir.mkdir(parents=True, exist_ok=True)
        self._window = window
        self._floor = floor
        self._target_ic = target_ic

    # ─── 存储路径 ───

    def _state_path(self, instruments: str) -> Path:
        return self._dir / f"oos_weight_state_{instruments}.json"

    # ─── 公共接口 ───

    def record_ic(
        self,
        model_name: str,
        instruments: str,
        date: str,
        valid_ic: Optional[float],
    ) -> None:
        """
        记录某模型在某日期的 OOS Validation IC。

        在每周重训完成后调用，将模型的验证集 IC 写入滚动历史。

        参数:
            model_name: 模型名称 (gbdt/xgboost/catboost/...)
            instruments: 标的池 (csi500/csi300)
            date: 重训日期 (YYYY-MM-DD)
            valid_ic: 验证集 Pearson IC，None 表示训练失败
        """
        state = self._load_state(instruments)

        if model_name not in state["ic_history"]:
            state["ic_history"][model_name] = []

        history = state["ic_history"][model_name]

        # 如果同一天已有记录，跳过（防止重复运行）
        if history and history[-1].get("date") == date:
            return

        entry = {
            "date": date,
            "valid_ic": valid_ic,
            "recorded_at": datetime.now().isoformat(),
        }
        history.append(entry)

        # 保留最近 2x window 条记录（防止文件无限增长）
        if len(history) > self._window * 3:
            state["ic_history"][model_name] = history[-(self._window * 2):]

        state["last_updated"] = datetime.now().isoformat()
        self._save_state(instruments, state)

    def compute_weights(
        self,
        model_names: List[str],
        instruments: str,
        date: Optional[str] = None,
    ) -> Dict[str, float]:
        """
        计算 OOS 调整后的集成权重。

        每个模型从等权开始，根据滚动验证 IC 调整:
          - IC >= target_ic → 保持等权
          - IC < target_ic → 按比例降权，最低降至 floor

        所有未知模型（无 IC 历史）维持等权。

        参数:
            model_names: 模型名称列表
            instruments: 标的池
            date: 参考日期（用于日志，不影响计算）

        返回:
            {model_name: weight} — 权重已归一化，和为 1.0
        """
        state = self._load_state(instruments)
        n = len(model_names)
        if n == 0:
            return {}

        equal_weight = 1.0 / n
        raw_weights: Dict[str, float] = {}

        for name in model_names:
            rolling_ic = self._get_rolling_ic(state, name)
            raw_weights[name] = self._ic_to_weight(rolling_ic, equal_weight)

        # 归一化
        total = sum(raw_weights.values())
        if total > 0:
            return {k: v / total for k, v in raw_weights.items()}
        return {k: equal_weight for k in model_names}

    def get_rolling_ic(self, model_name: str, instruments: str) -> Optional[float]:
        """获取某模型当前的滚动 IC（用于外部查询/报告）"""
        state = self._load_state(instruments)
        return self._get_rolling_ic(state, model_name)

    def get_summary(self, instruments: str) -> Dict[str, Any]:
        """获取权重状态摘要（用于报告/Health Check）"""
        state = self._load_state(instruments)
        model_names = list(state["ic_history"].keys())

        summary = {
            "instruments": instruments,
            "last_updated": state.get("last_updated"),
            "window": self._window,
            "floor": self._floor,
            "target_ic": self._target_ic,
            "models": {},
        }

        if model_names:
            weights = self.compute_weights(model_names, instruments)
            for name in model_names:
                rolling_ic = self._get_rolling_ic(state, name)
                summary["models"][name] = {
                    "rolling_ic": rolling_ic,
                    "weight": weights.get(name, 1.0 / len(model_names)),
                    "records": len(state["ic_history"].get(name, [])),
                }

        return summary

    # ─── 内部逻辑 ───

    def _get_rolling_ic(self, state: dict, model_name: str) -> Optional[float]:
        """从状态中提取滚动窗口 IC 均值"""
        history = state.get("ic_history", {}).get(model_name, [])
        if not history:
            return None

        # 取最近 window 条有效 IC 记录
        recent = [h["valid_ic"] for h in history[-self._window:]
                  if h.get("valid_ic") is not None]
        if not recent:
            return None

        return float(np.mean(recent))

    def _ic_to_weight(self, rolling_ic: Optional[float], equal_weight: float) -> float:
        """
        将滚动 IC 转换为原始权重（归一化前）。

        IC 为 None（无数据）→ 等权
        IC >= target_ic → 等权
        IC < target_ic → 按比例降权至 floor
        """
        if rolling_ic is None:
            return equal_weight

        if rolling_ic >= self._target_ic:
            return equal_weight

        if self._target_ic <= 0:
            return equal_weight

        # penalty ∈ [0, 1]: 0=无惩罚, 1=全惩罚
        penalty = (self._target_ic - rolling_ic) / self._target_ic
        penalty = min(1.0, max(0.0, penalty))

        # 降权：从等权逐步降到 floor * 等权
        raw = equal_weight * (1.0 - penalty * (1.0 - self._floor))
        return max(equal_weight * self._floor, raw)

    # ─── 持久化 ───

    def _load_state(self, instruments: str) -> dict:
        path = self._state_path(instruments)
        if not path.exists():
            return {"ic_history": {}, "last_updated": None}
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {"ic_history": {}, "last_updated": None}

    def _save_state(self, instruments: str, state: dict) -> None:
        path = self._state_path(instruments)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)


# ─── 全局单例 ───

_guard_instance: Optional[OOSWeightGuard] = None


def get_guard() -> OOSWeightGuard:
    """获取默认 OOSWeightGuard 实例"""
    global _guard_instance
    if _guard_instance is None:
        _guard_instance = OOSWeightGuard()
    return _guard_instance