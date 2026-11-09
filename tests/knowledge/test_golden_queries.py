"""Golden queries：离线、纯关键词的检索命中率回归（Task 21）。

不做真实 embedding：用 `NoneEmbedder` + `vector_store=None` 让 Retriever 走
`mode="keyword"`，只验证「分词 + FTS5 + 融合/去重/MMR」这条链在真实中文文档
语料上的召回是否还能达到验收线（≥ 80% 的 query 至少命中一条含预期词的块）。

语料就是 fixture 里的 `content`，每条一段真实风格文档片段，单独成文档、单独成块；
命中判据是「任一返回块的 content 包含 `expect_any` 中任一词（大小写不敏感）」，
因此本测试能发现分词退化、FTS 作用域/过滤写错、去重误杀等真实回归。

fixture 位于 `tests/knowledge/fixtures/golden_queries.json`，纯离线、确定性。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.embedding import NoneEmbedder
from nanobot.knowledge.repository import ChunkRecord, KnowledgeRepository
from nanobot.knowledge.retriever import Retriever, RetrieverSettings
from nanobot.knowledge.tokenizer import tokenize_index

FIXTURE = Path(__file__).parent / "fixtures" / "golden_queries.json"
WORKSPACE = "golden-ws"
KB_NAME = "golden-kb"
MIN_HIT_RATE = 0.8


def _load_cases() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_golden_fixture_shape() -> None:
    cases = _load_cases()
    assert len(cases) >= 20, f"golden cases 不足 20 条：{len(cases)}"
    for index, case in enumerate(cases):
        assert {"query", "expect_any", "content"} <= set(case), f"case #{index} 缺字段"
        assert isinstance(case["query"], str) and case["query"].strip()
        assert isinstance(case["expect_any"], list) and case["expect_any"]
        assert all(isinstance(term, str) and term for term in case["expect_any"])
        assert isinstance(case["content"], str) and case["content"].strip()


def _seed_corpus(repo: KnowledgeRepository, cases: list[dict[str, object]]) -> str:
    """每个 case 一篇 ready 文档 + 一个块（search_text 与查询同一分词函数）。"""
    kb = repo.create_kb(name=KB_NAME, workspace_id=WORKSPACE)
    for index, case in enumerate(cases):
        content = str(case["content"])
        document = repo.insert_document(
            kb_id=kb.id,
            workspace_id=WORKSPACE,
            title=f"golden-{index}",
            source_kind="external",
            source_path=f"/tmp/golden/{index}.md",
            file_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            file_size=len(content.encode("utf-8")),
        )
        attempt = repo.claim_document(document.id, lease_owner="golden-test")
        assert attempt is not None, f"golden 文档无法认领：{document.id}"
        chunk = ChunkRecord(
            id=f"chk_golden_{index}",
            doc_id=document.id,
            kb_id=kb.id,
            workspace_id=WORKSPACE,
            chunk_index=0,
            content=content,
            heading_path="",
            page=None,
            locator="",
            start_offset=0,
            end_offset=len(content),
            char_count=len(content),
            content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            search_text=tokenize_index(content),
        )
        committed = repo.commit_chunks(document.id, attempt=attempt, chunks=[chunk])
        assert committed, f"golden 文档写入失败：{document.id}"
    return kb.id


def test_golden_queries_keyword_hit_rate(tmp_path: Path) -> None:
    database = KnowledgeDatabase(tmp_path / "state.db")
    database.init_schema()
    repo = KnowledgeRepository(database)
    cases = _load_cases()
    kb_id = _seed_corpus(repo, cases)

    retriever = Retriever(
        repo,
        embedder=NoneEmbedder(),
        vector_store=None,
        settings=RetrieverSettings(),
    )

    misses: list[str] = []
    for case in cases:
        query = str(case["query"])
        expected = [str(term).lower() for term in case["expect_any"]]  # type: ignore[union-attr]
        result = retriever.search(
            query, workspace_ids=[WORKSPACE], kb_ids=[kb_id], mode="keyword"
        )
        assert result.mode_used == "keyword"
        assert result.hits, f"关键词检索零命中：{query!r}"
        matched = any(
            term in hit.content.lower() for hit in result.hits for term in expected
        )
        if not matched:
            tops = [hit.content[:40] for hit in result.hits[:2]]
            misses.append(f"{query!r} -> {tops}")

    hit_count = len(cases) - len(misses)
    hit_rate = hit_count / len(cases)
    assert hit_rate >= MIN_HIT_RATE, (
        f"golden 命中率 {hit_rate:.0%}（{hit_count}/{len(cases)}）低于 {MIN_HIT_RATE:.0%}；"
        f"未命中：{misses}"
    )


def test_golden_fixture_content_is_indexable() -> None:
    """每条 content 必须至少产出一个关键词 token，避免空语料混进命中率。"""
    for case in _load_cases():
        assert tokenize_index(str(case["content"])) != "", case["query"]
        assert sqlite3.sqlite_version_info >= (3, 9), "FTS5 需要 SQLite ≥ 3.9"
