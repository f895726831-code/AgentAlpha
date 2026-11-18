"""
API 兼容层 — 委托给 factory.metadata.FactorRegistry

修复 api_server.py 中损坏的 /api/v1/factors 端点。

api_server.py 在模块顶层导入: from store import FactorStore
此模块提供 FactorStore 类, 与原始接口兼容。

用法 (api_server.py 无需修改):
    from store import FactorStore
    store = FactorStore()
    store.list_active()
"""

from factory.metadata import FactorRegistry

# 全局单例
_registry = None


def _get_registry() -> FactorRegistry:
    """延迟初始化注册表单例"""
    global _registry
    if _registry is None:
        _registry = FactorRegistry()
    return _registry


class FactorStore:
    """
    兼容 api_server.py 的 FactorStore 接口。

    方法:
        list_active() → [{id, formula, ic_mean, source, discovery_date}, ...]
        get(factor_id) → {id, formula, ic_mean, source, ic_history} or None
    """

    def list_active(self):
        """列出活跃因子 (|IC| >= 0.02)"""
        registry = _get_registry()
        return [
            {
                "id": m.normalized,
                "formula": m.formula,
                "ic_mean": m.ic_mean,
                "source": m.source_channel,
                "discovery_date": m.discovery_date,
                "operators_used": m.operators_used,
                "complexity": m.complexity,
            }
            for m in registry.list_active(min_ic=0.02)
        ]

    def get(self, factor_id: str):
        """按 ID 获取单个因子"""
        registry = _get_registry()
        m = registry.get(factor_id)
        if m is None:
            return None
        return {
            "id": m.normalized,
            "formula": m.formula,
            "ic_mean": m.ic_mean,
            "source": m.source_channel,
            "ic_history": m.ic_history,
            "discovery_date": m.discovery_date,
            "operators_used": m.operators_used,
            "complexity": m.complexity,
            "last_evaluated": m.last_evaluated,
        }