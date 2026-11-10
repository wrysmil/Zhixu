"""知识检索专项评测：数据集 → 检索 → 判分 → 指标 → 对比。

``routes`` **不能**在包初始化时导入：``routes`` 依赖 ``memory.routes``，
而后者依赖 ``server``，``server`` 又依赖 ``services``，``services`` 在模块级
导入本包的 ``metrics``——包初始化时导入 ``routes`` 会形成循环，gateway 启动
直接 ``ImportError``。因此这里按 PEP 562 惰性暴露导出。
"""

from typing import Any

__all__ = ["KNOWLEDGE_API_PREFIX", "knowledge_route_defs"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from nanobot.evaluation.knowledge import routes

        return getattr(routes, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
