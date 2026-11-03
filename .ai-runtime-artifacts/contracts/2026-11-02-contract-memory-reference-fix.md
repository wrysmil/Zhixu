---
artifact: contract
route: api-and-interface-design
skills: [api-and-interface-design]
skills_evidence: [.claude/skills/api-and-interface-design/SKILL.md]
source: [.ai-runtime-artifacts/specs/2026-11-02-memory-reference-and-recall-contamination-spec.md]
created_at: 2026-11-02
---

# WU 接口契约

WU-01 独占 adapter/engine/context；WU-02 独占 profile_extractor/extractor/prompts；无共享写文件。
计数只接受最终结构化 MemoryHit，按 (kind, record_id) 去重，校验 row workspace，事实与情节分表，不级联。
检索公开返回签名不改，search_hits 无副作用，统计异常不能传播到对话。
画像 evidence 为模型输出的可选字符串，缺失视为无可验证来源，不能用于自动画像写入；experience 独立保留既有格式。
无 HTTP API、分页、错误协议或数据库 schema 变更；计数沿用现有 access_count 列。
