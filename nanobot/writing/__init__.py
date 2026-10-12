"""知序写作域：文章持久化与版本保护。

`database.py` 负责连接与 schema，`models.py` 负责字段语义与校验，
`repository.py` 负责单事务内的版本 CAS 与幂等。WebUI 传输层见
``nanobot/webui/writing_routes.py``；接口形状的唯一真相源是
``.ai-runtime-artifacts/contracts/2026-10-12-contract-writing-article-store.md``。
"""
