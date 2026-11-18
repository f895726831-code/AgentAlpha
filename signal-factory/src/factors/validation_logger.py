"""
实验自动留痕 — 每次验证运行自动记录到 JSONL

所有验证实验（模型训练、因子验证、回测评估）都会自动写入日志，
形成可审计的实验历史。日志为 JSONL 格式，每行一条记录。

核心用途:
  1. 追踪每个因子被验证了多少次（mining_rounds）
  2. 记录每道关的通过/失败状态
  3. 支持按 factor_id / date 查询历史

用法:
    from src.factors.validation_logger import ValidationLogger

    logger = ValidationLogger()
    entry_id = logger.log_validation({
        "factor_id": "factor_001",
        "factor_name": "momentum_20d",
        "hypothesis_id": "hyp_20260904_001",
        "gates": {"gate1": {"passed": True}, "gate2": {"passed": True}, "gate3": {"passed": True}},
        "passed": True,
    })
    rounds = logger.get_mining_rounds("factor_001")
"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ─── 路径配置 ───

_VALIDATION_LOG_DIR = Path("D:/AgentAlpha/signal-factory/validation_logs")
_VALIDATIONS_FILE = _VALIDATION_LOG_DIR / "validations.jsonl"


def get_validation_log_dir() -> Path:
    """获取验证日志目录（确保存在）"""
    _VALIDATION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _VALIDATION_LOG_DIR


def get_validations_file() -> Path:
    """获取验证日志文件路径"""
    get_validation_log_dir()
    return _VALIDATIONS_FILE


# ─── 数据类 ───

class ValidationEntry:
    """单次验证实验的记录"""

    __slots__ = (
        "entry_id", "timestamp", "factor_id", "factor_name",
        "hypothesis_id", "gates", "passed", "stage",
        "experiment_date", "extra",
    )

    def __init__(
        self,
        factor_id: str,
        factor_name: str = "",
        hypothesis_id: Optional[str] = None,
        gates: Optional[Dict[str, Any]] = None,
        passed: bool = False,
        stage: int = 0,
        experiment_date: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ):
        self.entry_id = str(uuid.uuid4())[:8]
        self.timestamp = datetime.now().isoformat()
        self.factor_id = factor_id
        self.factor_name = factor_name
        self.hypothesis_id = hypothesis_id
        self.gates = gates or {}
        self.passed = passed
        self.stage = stage
        self.experiment_date = experiment_date or datetime.now().strftime("%Y-%m-%d")
        self.extra = extra or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "timestamp": self.timestamp,
            "factor_id": self.factor_id,
            "factor_name": self.factor_name,
            "hypothesis_id": self.hypothesis_id,
            "gates": self.gates,
            "passed": self.passed,
            "stage": self.stage,
            "experiment_date": self.experiment_date,
            "extra": self.extra,
        }

    @property
    def mining_round(self) -> int:
        """当前这条记录是此因子的第几轮验证（由 logger 在写入时设置）"""
        return self.extra.get("mining_round", 0)


# ─── 日志器 ───

class ValidationLogger:
    """验证实验日志器"""

    def __init__(self, log_dir: Optional[Path] = None):
        self._log_dir = log_dir or _VALIDATION_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / "validations.jsonl"

    # ─── 写入 ───

    def log_validation(
        self,
        factor_id: str,
        factor_name: str = "",
        hypothesis_id: Optional[str] = None,
        gates: Optional[Dict[str, Any]] = None,
        passed: bool = False,
        stage: int = 0,
        experiment_date: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        记录一次验证实验。

        返回:
            entry_id (str): 8 位短 ID
        """
        # 计算当前是第几轮
        mining_round = self.get_mining_rounds(factor_id) + 1

        extra_dict = extra or {}
        extra_dict["mining_round"] = mining_round

        entry = ValidationEntry(
            factor_id=factor_id,
            factor_name=factor_name,
            hypothesis_id=hypothesis_id,
            gates=gates,
            passed=passed,
            stage=stage,
            experiment_date=experiment_date,
            extra=extra_dict,
        )

        d = entry.to_dict()
        with open(self._log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")

        return entry.entry_id

    # ─── 读取 ───

    def _read_all(self) -> List[Dict[str, Any]]:
        """读取所有日志条目"""
        if not self._log_file.exists():
            return []
        entries = []
        with open(self._log_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def get_history(self, factor_id: str) -> List[Dict[str, Any]]:
        """获取某个因子的全部验证历史"""
        return [e for e in self._read_all() if e.get("factor_id") == factor_id]

    def get_mining_rounds(self, factor_id: str) -> int:
        """获取某个因子已经验证过的轮数"""
        return len(self.get_history(factor_id))

    def get_experiment_count(self, date: Optional[str] = None) -> int:
        """获取某天（或全部）的实验次数"""
        target = date or datetime.now().strftime("%Y-%m-%d")
        return sum(1 for e in self._read_all() if e.get("experiment_date", "") == target)

    def get_latest(self, factor_id: str) -> Optional[Dict[str, Any]]:
        """获取某个因子的最新验证记录"""
        history = self.get_history(factor_id)
        if not history:
            return None
        return max(history, key=lambda e: e.get("timestamp", ""))

    def get_passed_factors(self) -> List[str]:
        """获取所有最近一次验证通过的因子 ID"""
        all_entries = self._read_all()
        latest = {}
        for e in all_entries:
            fid = e.get("factor_id", "")
            if fid not in latest or e.get("timestamp", "") > latest[fid].get("timestamp", ""):
                latest[fid] = e
        return [fid for fid, e in latest.items() if e.get("passed")]

    def get_summary(self) -> Dict[str, Any]:
        """获取验证日志摘要"""
        entries = self._read_all()
        if not entries:
            return {"total_experiments": 0, "unique_factors": 0, "pass_rate": 0}

        unique_factors = set(e.get("factor_id", "") for e in entries)
        passed = sum(1 for e in entries if e.get("passed"))

        return {
            "total_experiments": len(entries),
            "unique_factors": len(unique_factors),
            "total_passed": passed,
            "pass_rate": round(passed / len(entries) * 100, 1) if entries else 0,
            "first_experiment": min(e.get("timestamp", "") for e in entries),
            "last_experiment": max(e.get("timestamp", "") for e in entries),
        }


# ─── 便捷函数 ───

_logger_instance: Optional[ValidationLogger] = None


def get_logger() -> ValidationLogger:
    """获取默认日志器实例"""
    global _logger_instance
    if _logger_instance is None:
        _logger_instance = ValidationLogger()
    return _logger_instance