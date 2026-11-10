"""`KnowledgeService.retriever_with`：同一套依赖 + 不同检索参数（评测用）。

评测要在「同一套融合 / 去重 / MMR 实现」上跑不同参数，因此该构造器必须复用
产品实例的 repo / embedder / vector store，只替换 `RetrieverSettings`；
同时不得改变产品默认 `search()` 的行为。

语料构造照抄 `tests/knowledge/test_golden_queries.py::_seed_corpus`：
直接用 `KnowledgeRepository.insert_document` / `claim_document` / `commit_chunks`
+ `ChunkRecord`，不给产品类加任何测试专用方法。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from nanobot.knowledge.embedding import NoneEmbedder
from nanobot.knowledge.repository import ChunkRecord, KnowledgeRepository
from nanobot.knowledge.retriever import Retriever, RetrieverSettings
from nanobot.knowledge.service import KnowledgeService
from nanobot.knowledge.tokenizer import tokenize_index

WORKSPACE = "retriever-with-ws"
KB_NAME = "retriever-with-kb"
QUERY = "向量检索"

# 5 篇结构一致、唯一用词不同的文档：都能命中同一 query，且不会被部分包含去重误杀。
_CONTENTS = (
    "向量检索的第一部分介绍索引构建流程：分块、写入向量库、再回表补齐原始文本。",
    "向量检索的第二部分介绍召回路由：先走语义路，再走关键词路，两路结果合并去重。",
    "向量检索的第三部分介绍融合打分：加权 RRF 把名次折算成分数，并按分数降序展示。",
    "向量检索的第四部分介绍冗余压制：MMR 在相关性与相似度之间取平衡，避免同质内容。",
    "向量检索的第五部分介绍参数调优：候选池大小、上限条数、阈值与权重都需要按语料校准。",
)


def _seed_corpus(repo: KnowledgeRepository, contents: tuple[str, ...]) -> str:
    """每个内容一篇 ready 文档 + 一个块（search_text 与查询同一分词函数）。"""
    kb = repo.create_kb(name=KB_NAME, workspace_id=WORKSPACE)
    for index, content in enumerate(contents):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        document = repo.insert_document(
            kb_id=kb.id,
            workspace_id=WORKSPACE,
            title=f"retriever-with-{index}",
            source_kind="external",
            source_path=f"/tmp/retriever-with/{index}.md",
            file_hash=digest,
            file_size=len(content.encode("utf-8")),
        )
        attempt = repo.claim_document(document.id, lease_owner="retriever-with-test")
        assert attempt is not None, f"文档无法认领：{document.id}"
        chunk = ChunkRecord(
            id=f"chk_rwv_{index}",
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
            content_hash=digest,
            search_text=tokenize_index(content),
        )
        committed = repo.commit_chunks(document.id, attempt=attempt, chunks=[chunk])
        assert committed, f"文档写入失败：{document.id}"
    return kb.id


def _service(tmp_path: Path) -> tuple[KnowledgeService, str]:
    """装配无向量层的服务并灌入语料；返回 (service, kb_id)。"""
    service = KnowledgeService.local(
        tmp_path / "state.db",
        embedder=NoneEmbedder(),
        vector_store=None,
    )
    kb_id = _seed_corpus(service._repo, _CONTENTS)
    return service, kb_id


def test_retriever_with_shares_dependencies_and_replaces_settings(
    tmp_path: Path,
) -> None:
    """新实例共享同一 repo/embedder/vector，仅 `_settings` 不同。"""
    service, _kb_id = _service(tmp_path)

    custom = service.retriever_with(RetrieverSettings(max_limit=1))

    assert isinstance(custom, Retriever)
    assert custom is not service._retriever
    # 依赖对象必须共享：不新建 embedding，也不新建向量库客户端。
    assert custom._repo is service._repo
    assert custom._embedder is service._embedder
    assert custom._vector is service._vector
    # 只有参数被替换。
    assert custom._settings is not service._retriever._settings
    assert custom._settings.max_limit == 1
    assert custom._settings != service._retriever._settings
    # 产品实例的默认参数不受影响。
    assert service._retriever._settings.max_limit == RetrieverSettings().max_limit


def test_retriever_with_changes_result_count(tmp_path: Path) -> None:
    """不同 settings 跑同一 query，结果条数确实不同。"""
    service, kb_id = _service(tmp_path)

    default_result = service.search(
        QUERY, workspace_ids=[WORKSPACE], kb_ids=[kb_id], mode="keyword"
    )
    custom = service.retriever_with(RetrieverSettings(max_limit=1))
    limited_result = custom.search(
        QUERY, workspace_ids=[WORKSPACE], kb_ids=[kb_id], mode="keyword"
    )

    assert len(default_result.hits) > 1, "语料不足，未能体现条数差异"
    assert len(limited_result.hits) == 1
    assert len(default_result.hits) != len(limited_result.hits)
    # 共享同一份索引：受限结果必须是默认结果的子集。
    default_ids = {hit.chunk_id for hit in default_result.hits}
    assert {hit.chunk_id for hit in limited_result.hits} <= default_ids


def test_retriever_with_does_not_change_product_search(tmp_path: Path) -> None:
    """调用 `retriever_with` 后，产品默认 `search()` 行为不变。"""
    service, kb_id = _service(tmp_path)

    before = service.search(QUERY, workspace_ids=[WORKSPACE], kb_ids=[kb_id])
    service.retriever_with(RetrieverSettings(max_limit=1))
    after = service.search(QUERY, workspace_ids=[WORKSPACE], kb_ids=[kb_id])

    assert before.mode_used == after.mode_used
    assert before.hits, "默认检索应有命中"
    assert [hit.chunk_id for hit in after.hits] == [hit.chunk_id for hit in before.hits]
    assert after.hits == before.hits
    # 产品检索器仍是原实例、默认参数未变。
    assert service._retriever._settings.max_limit == RetrieverSettings().max_limit
