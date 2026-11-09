"""知识检索专项评测：数据集 → 检索 → 判分 → 指标 → 对比。"""

from nanobot.evaluation.knowledge.routes import KNOWLEDGE_API_PREFIX, knowledge_route_defs

__all__ = ["KNOWLEDGE_API_PREFIX", "knowledge_route_defs"]
