"""图谱 prompt 默认值（配置 `knowledge.graph.prompts` 的单一来源）。

占位符：
- `DEFAULT_GRAPH_EXTRACTION_PROMPT`：`<<heading>>` / `<<content>>`
- `DEFAULT_QUERY_ENTITY_USER_PROMPT`：`<<query>>`
"""

from __future__ import annotations

DEFAULT_GRAPH_EXTRACTION_PROMPT = (
    "你是知识图谱抽取器。阅读片段，抽取其中的实体与实体间关系。\n"
    "只输出一个 JSON 对象，不要解释、不要 Markdown 围栏：\n"
    '{"entities":[{"name":"名称","type":"概念/服务/系统/人物/文档/其他",'
    '"description":"一句话"}],"relations":[{"source":"实体名","target":"实体名",'
    '"relation":"关系名","description":"一句话","strength":1-10}]}\n'
    "规则：实体名不超过 40 字；不要代词；关系两端必须是 entities 中出现的名称；"
    "没有实体就输出 {\"entities\": [], \"relations\": []}。\n"
    "标题路径：<<heading>>\n片段：\n<<content>>"
)

# 对齐 WeKnora `config/config.yaml` 的 `extract_entity`：按关联度抽实体名，
# few-shot 输出 `[{"entity": "..."}]`（不是入库抽取的 entities/relations 结构）。
DEFAULT_QUERY_ENTITY_SYSTEM_PROMPT = (
    "Based on the user's question, process the key information extraction task "
    "following these steps:\n"
    "1. Analyze logical connections: First, fully analyze the text content, "
    "identify its core logical relationships, and briefly annotate the core logic type;\n"
    "2. Extract key entities: Based on the identified logical relationships, "
    "precisely extract key information from the text and classify it into clear "
    "entities, ensuring no core information is omitted and no redundant content is added;\n"
    "3. Prioritize entities: Sort by the closeness of each entity's association with "
    "the core topic of the text, presenting the most important entities for "
    "understanding the main idea first;\n\n"
    "# Examples\n"
    'Q: "\'Romeo and Juliet\' is a tragedy written by William Shakespeare early in '
    'his career, and is one of the most frequently performed plays in world literature."\n'
    "A:\n"
    "```json\n"
    "[\n"
    '  {"entity": "Romeo and Juliet"},\n'
    '  {"entity": "William Shakespeare"},\n'
    '  {"entity": "world literature"}\n'
    "]\n"
    "```\n"
    "\n# Output contract\n"
    "Do not answer the question. Return ONLY a JSON array of "
    '{"entity": "..."} objects; no prose, no markdown. '
    "If there is no entity, return [].\n"
)

DEFAULT_QUERY_ENTITY_USER_PROMPT = "# Question\nQ: <<query>>\nA:"
