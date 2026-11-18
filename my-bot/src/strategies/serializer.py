"""
策略序列化器 — Python 策略对象 ↔ JSON 持久化。

TiMi 生命周期:
  离线: LLM JSON → compiler.compile() → serializer.serialize() → disk
  在线: disk → serializer.deserialize() → executor.execute()

存储路径: data/strategies/{YYYY-MM-DD}.json

用法:
    from strategies.compiler import StrategyCompiler
    from strategies.serializer import StrategySerializer

    # 离线编译
    compiler = StrategyCompiler()
    result = compiler.compile(llm_json)
    serializer = StrategySerializer()
    serializer.serialize(result, date="2026-10-14")

    # 在线加载
    result = serializer.deserialize("2026-10-14")
    for strategy in result.active_strategies.values():
        if strategy.check_conditions(market_data):
            signal = strategy.generate_signal(market_data)
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .base import BaseStrategy, StrategyParams
from .compiler import CompileResult, STRATEGY_CLASS_MAP


class StrategySerializer:
    """
    策略序列化器。

    负责策略对象的 JSON 序列化/反序列化。
    策略对象本身不可直接 JSON 化, 但 StrategyParams 可序列化。
    序列化时只保存参数, 反序列化时用编译器重建策略对象。
    """

    # 默认存储根目录 (相对于 my-bot)
    DEFAULT_STORAGE_DIR = Path("data/strategies")

    def __init__(self, storage_dir: Path = None):
        self.storage_dir = Path(storage_dir) if storage_dir else self.DEFAULT_STORAGE_DIR

    def serialize(
        self,
        result: CompileResult,
        date_str: str = None,
        path: Path = None,
    ) -> Path:
        """
        序列化编译结果为 JSON 文件。

        Args:
            result: CompileResult (编译输出)
            date_str: 日期字符串 (YYYY-MM-DD)。默认今天。
            path: 自定义输出路径。覆盖日期路径。

        Returns:
            写入的文件路径
        """
        if path is None:
            date_str = date_str or date.today().isoformat()
            filename = f"{date_str}.json"
            path = self.storage_dir / filename

        path.parent.mkdir(parents=True, exist_ok=True)

        # ── 构建可序列化结构 ──
        strategies_data = {}
        for stype, strategy in result.strategies.items():
            strategies_data[stype] = {
                "active": strategy.params.active,
                "params": strategy.params.filters,
                "confidence_threshold": strategy.params.confidence_threshold,
            }

        data = {
            "version": "1.0",
            "compiled_at": datetime.now().isoformat(),
            "date": date_str or date.today().isoformat(),
            "regime": result.regime,
            "bias": result.bias,
            "mode": result.mode,
            "filters": result.filters,
            "strategies": strategies_data,
            "warnings": result.warnings,
            "meta": result.meta,
        }

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return path

    def deserialize(
        self,
        date_str: str = None,
        path: Path = None,
    ) -> CompileResult:
        """
        从 JSON 文件反序列化为 CompileResult。

        Args:
            date_str: 日期字符串 (YYYY-MM-DD)
            path: 自定义文件路径

        Returns:
            CompileResult (含策略对象)

        Raises:
            FileNotFoundError: 找不到策略文件
        """
        if path is None:
            if date_str is None:
                date_str = date.today().isoformat()
            path = self.storage_dir / f"{date_str}.json"

        if not path.exists():
            raise FileNotFoundError(f"策略文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return self._from_dict(data)

    def _from_dict(self, data: Dict[str, Any]) -> CompileResult:
        """从字典构建 CompileResult"""
        from .compiler import StrategyCompiler

        compiler = StrategyCompiler()

        # 重建 LLM JSON 格式供编译器处理
        strategy_configs = []
        for stype, cfg in data.get("strategies", {}).items():
            strategy_configs.append({
                "type": stype,
                "active": cfg.get("active", True),
                "params": cfg.get("params", {}),
            })

        llm_json = {
            "regime": data.get("regime", ""),
            "bias": data.get("bias", "balanced"),
            "mode": data.get("mode", "normal"),
            "strategies": strategy_configs,
            "filters": data.get("filters", {}),
            "meta": data.get("meta", {}),
        }

        result = compiler.compile(llm_json)

        # 恢复原始警告
        result.warnings = data.get("warnings", [])

        return result

    def list_available(self) -> list:
        """列出所有可用策略文件的日期"""
        if not self.storage_dir.exists():
            return []

        files = sorted(
            self.storage_dir.glob("*.json"),
            reverse=True,
        )
        return [f.stem for f in files]  # ["2026-10-14", "2026-10-13", ...]

    def get_latest(self, max_age_days: int = 1) -> Optional[CompileResult]:
        """
        加载最新的策略文件。

        Args:
            max_age_days: 最大允许的文件年龄（天）。默认 1 天（今天或昨天可用）。
                          超过此期限返回 None，触发上游 fallback。
                          设为 -1 禁用过期检查。

        Returns:
            CompileResult 或 None（无文件 或 文件过期）
        """
        import logging
        _log = logging.getLogger(__name__)

        available = self.list_available()
        if not available:
            return None

        latest_date_str = available[0]
        result = self.deserialize(latest_date_str)

        # ── 新鲜度合约 (v2.3) ──
        if max_age_days >= 0:
            try:
                latest_date = date.fromisoformat(latest_date_str)
                age_days = (date.today() - latest_date).days
                if age_days > max_age_days:
                    _log.warning(
                        f"策略文件 {latest_date_str}.json 已过期 "
                        f"(年龄={age_days}d, 最大={max_age_days}d) — 拒绝加载，触发 fallback"
                    )
                    return None
                result.meta["strategy_age_days"] = age_days
            except (ValueError, TypeError):
                pass  # 日期解析失败不阻塞

        return result

    def get_latest_with_age(self, max_age_days: int = 1) -> tuple:
        """
        加载最新策略 + 返回年龄信息。

        Returns:
            (CompileResult, age_days, is_stale: bool)
            如果无文件或过期: (None, -1, True)
        """
        available = self.list_available()
        if not available:
            return None, -1, True

        latest_date_str = available[0]
        result = self.deserialize(latest_date_str)

        try:
            latest_date = date.fromisoformat(latest_date_str)
            age_days = (date.today() - latest_date).days
            is_stale = age_days > max_age_days
        except (ValueError, TypeError):
            age_days = -1
            is_stale = False

        if is_stale and max_age_days >= 0:
            import logging
            _log = logging.getLogger(__name__)
            _log.warning(
                f"策略文件 {latest_date_str}.json 已过期 (年龄={age_days}d) — 应触发 fallback"
            )
            return None, age_days, True

        if result is not None:
            result.meta["strategy_age_days"] = age_days

        return result, age_days, is_stale