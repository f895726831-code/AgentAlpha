"""
假设前置注册表 — 先写假设、打时间戳，再跑回测

核心设计（来自祁天娇访谈"铁律 vs 想象力"）:
  1. AI 可以自由提假设，但必须在回测前注册
  2. 注册时打不可篡改的时间戳
  3. 回测完成后绑定 hypothesis_id
  4. 无 hypothesis_id 的结果标注"未注册假设，置信度降低"

经济逻辑模板（Gate 3 参考）:
  - 行为金融学: 动量/反转/过度反应/注意力偏差
  - 市场微观结构: 流动性/信息不对称/交易成本
  - 制度性: 政策/监管/税收/指数调整

用法:
    from src.factors.hypothesis_registry import HypothesisRegistry

    registry = HypothesisRegistry()
    h = registry.register("低估值股票未来 3 个月收益高于高估值股票", "factor_001")
    # ... 回测完成后 ...
    registry.bind_result(h.hypothesis_id, {"gate1_score": 75, "verdict": "Deploy"})
"""

import hashlib
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

_REGISTRY_LOG_DIR = Path("D:/AgentAlpha/signal-factory/validation_logs")
_REGISTRY_FILE = _REGISTRY_LOG_DIR / "hypothesis_registry.jsonl"


# ─── 数据类 ───

class Hypothesis:
    """注册的假设"""

    __slots__ = (
        "hypothesis_id", "text", "text_hash", "factor_id", "author",
        "registered_at", "bound_result", "bound_at", "category", "notes",
    )

    def __init__(
        self,
        hypothesis_id: str,
        text: str,
        factor_id: str,
        author: str = "ai",
        category: str = "",
        notes: str = "",
    ):
        self.hypothesis_id = hypothesis_id
        self.text = text
        self.text_hash = _hash_text(text)
        self.factor_id = factor_id
        self.author = author
        self.registered_at = datetime.now().isoformat()
        self.category = category
        self.notes = notes
        self.bound_result: Optional[Dict[str, Any]] = None
        self.bound_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "text": self.text,
            "text_hash": self.text_hash,
            "factor_id": self.factor_id,
            "author": self.author,
            "registered_at": self.registered_at,
            "category": self.category,
            "notes": self.notes,
            "bound_result": self.bound_result,
            "bound_at": self.bound_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Hypothesis":
        h = cls(
            hypothesis_id=d["hypothesis_id"],
            text=d["text"],
            factor_id=d.get("factor_id", ""),
            author=d.get("author", "ai"),
            category=d.get("category", ""),
            notes=d.get("notes", ""),
        )
        h.registered_at = d.get("registered_at", "")
        h.bound_result = d.get("bound_result")
        h.bound_at = d.get("bound_at")
        return h


# ─── 工具函数 ───

def _hash_text(text: str) -> str:
    """计算假设文本的 SHA-256 哈希（用于去重）"""
    normalized = text.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


# ─── 注册表 ───

class HypothesisRegistry:
    """假设前置注册表"""

    def __init__(self, registry_dir: Optional[Path] = None):
        self._registry_dir = registry_dir or _REGISTRY_LOG_DIR
        self._registry_dir.mkdir(parents=True, exist_ok=True)
        self._registry_file = self._registry_dir / "hypothesis_registry.jsonl"

    # ─── 核心接口 ───

    def register(
        self,
        hypothesis_text: str,
        factor_id: str,
        author: str = "ai",
        category: str = "",
        notes: str = "",
    ) -> Hypothesis:
        """
        注册一个新的假设。

        必须先注册假设，再运行回测。注册时打上不可篡改的时间戳。

        参数:
            hypothesis_text: 假设描述（如 "低估值股票未来收益更高"）
            factor_id: 关联的因子 ID
            author: 作者（"ai" / "human" / model name）
            category: 经济逻辑分类（"behavioral" / "microstructure" / "institutional"）
            notes: 额外备注

        返回:
            Hypothesis 对象

        异常:
            ValueError: 如果相同文本的假设已注册
        """
        text_hash = _hash_text(hypothesis_text)

        # 检查是否已有相同假设
        existing = self._find_by_hash(text_hash)
        if existing:
            raise ValueError(
                f"相同假设已注册: {existing.hypothesis_id} "
                f"(注册时间 {existing.registered_at})"
            )

        hypothesis_id = f"hyp_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

        h = Hypothesis(
            hypothesis_id=hypothesis_id,
            text=hypothesis_text,
            factor_id=factor_id,
            author=author,
            category=category,
            notes=notes,
        )

        with open(self._registry_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(h.to_dict(), ensure_ascii=False) + "\n")

        return h

    def bind_result(
        self,
        hypothesis_id: str,
        validation_result: Dict[str, Any],
    ) -> Optional[Hypothesis]:
        """
        将回测/验证结果绑定到已注册的假设。

        调用时机: 回测或验证完成后。

        参数:
            hypothesis_id: 假设 ID
            validation_result: 验证结果 dict

        返回:
            更新后的 Hypothesis，或 None（假设不存在）
        """
        h = self.get_hypothesis(hypothesis_id)
        if h is None:
            return None

        h.bound_result = validation_result
        h.bound_at = datetime.now().isoformat()

        # 更新注册表文件
        self._update_entry(h)

        return h

    def get_hypothesis(self, hypothesis_id: str) -> Optional[Hypothesis]:
        """根据 ID 获取假设"""
        all_entries = self._read_all()
        for d in all_entries:
            if d.get("hypothesis_id") == hypothesis_id:
                return Hypothesis.from_dict(d)
        return None

    def get_hypothesis_by_factor(self, factor_id: str) -> Optional[Hypothesis]:
        """获取某个因子关联的假设（取最新注册的）"""
        all_entries = self._read_all()
        matching = [d for d in all_entries if d.get("factor_id") == factor_id]
        if not matching:
            return None
        latest = max(matching, key=lambda d: d.get("registered_at", ""))
        return Hypothesis.from_dict(latest)

    # ─── 查询 ───

    def list_registered(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        列出所有已注册的假设。

        参数:
            status: 过滤条件 — "bound"（已绑定结果）/ "unbound"（未绑定）
        """
        entries = self._read_all()
        if status == "bound":
            entries = [e for e in entries if e.get("bound_result")]
        elif status == "unbound":
            entries = [e for e in entries if not e.get("bound_result")]
        return entries

    def get_all_hypotheses(self) -> List[Hypothesis]:
        """获取所有假设对象"""
        return [Hypothesis.from_dict(d) for d in self._read_all()]

    def get_summary(self) -> Dict[str, Any]:
        """获取注册表摘要"""
        entries = self._read_all()
        bound = sum(1 for e in entries if e.get("bound_result"))
        return {
            "total_hypotheses": len(entries),
            "bound": bound,
            "unbound": len(entries) - bound,
            "unique_factors": len(set(e.get("factor_id", "") for e in entries)),
        }

    # ─── 置信度扣分 ───

    def get_unregistered_penalty(self) -> Dict[str, Any]:
        """
        未注册假设的置信度扣分规则。

        返回:
            {
                "penalty_type": "confidence_reduction",
                "reduction": 0.2,  # 20%
                "reason": "假设未在回测前注册，事后补的故事按零分处理",
                "message": "未注册假设，置信度降低 20%"
            }
        """
        return {
            "penalty_type": "confidence_reduction",
            "reduction": 0.2,
            "reason": "假设未在回测前注册，事后补的故事按零分处理",
            "message": "未注册假设，置信度降低 20%",
        }

    # ─── 内部方法 ───

    def _read_all(self) -> List[Dict[str, Any]]:
        if not self._registry_file.exists():
            return []
        entries = []
        with open(self._registry_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries

    def _find_by_hash(self, text_hash: str) -> Optional[Hypothesis]:
        for d in self._read_all():
            if d.get("text_hash") == text_hash:
                return Hypothesis.from_dict(d)
        return None

    def _update_entry(self, h: Hypothesis):
        """更新注册表中的一条记录"""
        entries = self._read_all()
        updated = False
        for i, d in enumerate(entries):
            if d.get("hypothesis_id") == h.hypothesis_id:
                entries[i] = h.to_dict()
                updated = True
                break

        if not updated:
            return

        with open(self._registry_file, "w", encoding="utf-8") as f:
            for d in entries:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")


# ─── 便捷函数 ───

_registry_instance: Optional[HypothesisRegistry] = None


def get_registry() -> HypothesisRegistry:
    """获取默认注册表实例"""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = HypothesisRegistry()
    return _registry_instance