"""
样本外测试集访问控制 — 物理隔离 + 一次性原则 + 污染追踪

核心设计（来自祁天娇访谈"验证先于产能"）:
  1. 测试集物理隔离: 放在 AI agent 无权访问的独立目录
  2. 一次性原则: 测试集碰过一次即标记为"已污染"，再次使用自动拒绝
  3. 污染追踪: 记录每个因子访问测试集的时间戳

与 leakage_checker.py 互补:
  - leakage_checker: 检测代码中的未来函数（静态分析）
  - oos_guard: 控制数据访问（运行时门禁）

用法:
    from src.data.oos_guard import OOSGuard

    guard = OOSGuard()
    result = guard.check_access("factor_001", "2026-09-04")
    if result.allowed:
        # 使用测试集...
        guard.mark_accessed("factor_001", "2026-09-04")
    else:
        print(f"拒绝: {result.reason}")
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

_OOS_LOG_DIR = Path("D:/AgentAlpha/signal-factory/validation_logs")
_OOS_ACCESS_LOG = _OOS_LOG_DIR / "oos_access.jsonl"
_OOS_TEST_SET_DIR = Path("D:/AgentAlpha/signal-factory/data/oos_test_set")


# ─── 数据类 ───

class OOSAccessResult:
    """访问检查结果"""

    __slots__ = ("allowed", "reason", "access_count", "first_access", "last_access")

    def __init__(
        self,
        allowed: bool,
        reason: str = "",
        access_count: int = 0,
        first_access: Optional[str] = None,
        last_access: Optional[str] = None,
    ):
        self.allowed = allowed
        self.reason = reason
        self.access_count = access_count
        self.first_access = first_access
        self.last_access = last_access

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "access_count": self.access_count,
            "first_access": self.first_access,
            "last_access": self.last_access,
        }

    def __repr__(self) -> str:
        status = "ALLOWED" if self.allowed else "BLOCKED"
        return f"OOSAccessResult({status}, {self.reason})"


# ─── 访问控制器 ───

class OOSGuard:
    """样本外测试集访问控制器"""

    def __init__(
        self,
        test_set_path: Optional[Path] = None,
        log_dir: Optional[Path] = None,
    ):
        self._test_set_path = test_set_path or _OOS_TEST_SET_DIR
        self._log_dir = log_dir or _OOS_LOG_DIR
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._access_log = self._log_dir / "oos_access.jsonl"

    # ─── 核心接口 ───

    def check_access(self, factor_id: str, date: Optional[str] = None) -> OOSAccessResult:
        """
        检查某个因子是否可以访问测试集。

        规则:
          - 首次访问 → 允许，但会记录
          - 已访问过 → 拒绝（一次性原则）
          - 测试集目录不存在 → 拒绝（物理隔离未配置）

        参数:
            factor_id: 因子/策略 ID
            date: 访问日期（用于日志记录）

        返回:
            OOSAccessResult
        """
        if not self._test_set_exists():
            return OOSAccessResult(
                allowed=False,
                reason=f"测试集目录不存在: {self._test_set_path}",
            )

        history = self._get_access_history(factor_id)

        if not history:
            return OOSAccessResult(
                allowed=True,
                reason="首次访问 — 允许，但访问后将被标记为已污染",
                access_count=0,
            )

        return OOSAccessResult(
            allowed=False,
            reason=f"测试集已被污染（{len(history)} 次历史访问，首次 {history[0].get('timestamp', '?')}）",
            access_count=len(history),
            first_access=history[0].get("timestamp"),
            last_access=history[-1].get("timestamp"),
        )

    def mark_accessed(
        self,
        factor_id: str,
        date: Optional[str] = None,
        access_type: str = "validation",
        caller: str = "unknown",
    ) -> str:
        """
        标记因子已访问测试集。

        调用时机: 在 check_access() 返回 allowed=True 后，实际使用测试集之前。

        返回:
            access_id (str)
        """
        access_id = str(uuid.uuid4())[:8]
        entry = {
            "access_id": access_id,
            "factor_id": factor_id,
            "timestamp": datetime.now().isoformat(),
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "access_type": access_type,
            "caller": caller,
        }

        with open(self._access_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        return access_id

    def is_contaminated(self, factor_id: str) -> bool:
        """检查因子是否已污染测试集"""
        return len(self._get_access_history(factor_id)) > 0

    def get_access_history(self, factor_id: str) -> List[Dict[str, Any]]:
        """获取因子的完整访问历史"""
        return self._get_access_history(factor_id)

    # ─── 批量操作 ───

    def check_batch(
        self,
        factor_ids: List[str],
        date: Optional[str] = None,
    ) -> Dict[str, OOSAccessResult]:
        """批量检查多个因子的访问权限"""
        return {fid: self.check_access(fid, date) for fid in factor_ids}

    def get_contaminated_factors(self) -> List[str]:
        """获取所有已污染测试集的因子 ID"""
        all_entries = self._read_all()
        contaminated = set()
        for e in all_entries:
            contaminated.add(e.get("factor_id", ""))
        return sorted(contaminated)

    def get_summary(self) -> Dict[str, Any]:
        """获取访问控制摘要"""
        entries = self._read_all()
        unique_factors = len(set(e.get("factor_id", "") for e in entries))
        return {
            "total_accesses": len(entries),
            "contaminated_factors": unique_factors,
            "test_set_exists": self._test_set_exists(),
            "test_set_path": str(self._test_set_path),
        }

    # ─── 内部方法 ───

    def _test_set_exists(self) -> bool:
        return self._test_set_path.exists()

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self._access_log.exists():
            return []
        entries = []
        with open(self._access_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def _get_access_history(self, factor_id: str) -> List[Dict[str, Any]]:
        return [e for e in self._read_all() if e.get("factor_id") == factor_id]

    # ─── 管理操作 ───

    def reset_factor(self, factor_id: str) -> int:
        """
        重置某个因子的污染状态（管理员操作，谨慎使用）。

        返回:
            删除的访问记录数
        """
        entries = self._read_all()
        keep = [e for e in entries if e.get("factor_id") != factor_id]
        removed = len(entries) - len(keep)

        with open(self._access_log, "w", encoding="utf-8") as f:
            for e in keep:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        return removed

    def create_test_set(self, source_dir: Optional[Path] = None) -> Path:
        """
        创建测试集目录（如不存在）。

        参数:
            source_dir: 可选，从指定目录复制数据到测试集目录

        返回:
            测试集目录路径
        """
        self._test_set_path.mkdir(parents=True, exist_ok=True)

        if source_dir and source_dir.exists():
            import shutil
            for f in source_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, self._test_set_path / f.name)

        return self._test_set_path


# ─── 便捷函数 ───

_guard_instance: Optional[OOSGuard] = None


def get_guard() -> OOSGuard:
    """获取默认 OOSGuard 实例"""
    global _guard_instance
    if _guard_instance is None:
        _guard_instance = OOSGuard()
    return _guard_instance