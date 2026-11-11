"""`knowledge_search` / `knowledge_read` 工具契约（知识中心 plan Task 17）。

口径来源：
- `.ai-runtime-artifacts/specs/2026-11-09-knowledge-center-spec.md` §9.1 / §9.2 / §14
- `.ai-runtime-artifacts/specs/2026-11-09-knowledge-retrieval-spec.md` §6 / §7 / §9.5

全部用 fake service，不碰真实 SQLite / Chroma / 网络。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from nanobot.agent.tools.context import ToolContext
from nanobot.agent.tools.knowledge_common import (
    _history_messages,
    render_search_result,
)
from nanobot.agent.tools.knowledge_read import KnowledgeReadTool
from nanobot.agent.tools.knowledge_search import KnowledgeSearchTool
from nanobot.bus.queue import MessageBus
from nanobot.knowledge.fusion import SearchHit, SearchResult
from nanobot.knowledge.graph.models import GraphEvidencePayload
from nanobot.knowledge.models import KnowledgeBase
from nanobot.knowledge.repository import StoredChunk
from nanobot.knowledge.retriever import KnowledgeSearchError

WORKSPACE_IDS = ["default", "ws1"]


# ---------------------------------------------------------------------------
# Fake service
# ---------------------------------------------------------------------------


def make_kb(
    kb_id: str = "kb_1",
    name: str = "产品手册",
    workspace_id: str = "ws1",
) -> KnowledgeBase:
    return KnowledgeBase(
        id=kb_id,
        name=name,
        description="",
        workspace_id=workspace_id,
        visibility="workspace",
        chunk_size=800,
        chunk_overlap=120,
        embedding_backend="none",
        embedding_model="",
        embedding_dimensions=0,
        is_enabled=True,
        created_at="t",
        updated_at="t",
    )


def make_chunk(index: int = 0, content: str | None = None, kb_id: str = "kb_1") -> StoredChunk:
    text = f"第{index}块正文" if content is None else content
    return StoredChunk(
        row_id=index + 1,
        id=f"chk_{index}",
        doc_id="doc_1",
        kb_id=kb_id,
        workspace_id="ws1",
        chunk_index=index,
        content=text,
        heading_path="第三章",
        page=12,
        locator=f"page=12,line={index + 1}",
        start_offset=0,
        end_offset=len(text),
        char_count=len(text),
        content_hash="h",
        is_enabled=True,
    )


def make_hit(
    index: int = 0,
    content: str = "部署失败怎么回滚？",
    *,
    score: float = 0.9,
    heading_path: str = "第三章",
    page: int | None = 12,
) -> SearchHit:
    return SearchHit(
        chunk_id=f"chk_{index}",
        doc_id="doc_1",
        kb_id="kb_1",
        chunk_index=index,
        heading_path=heading_path,
        page=page,
        locator=f"page=12,line={index + 1}",
        content=content,
        char_count=len(content),
        content_hash="h",
        score=score,
    )


class FakeDocument:
    def __init__(
        self,
        doc_id: str = "doc_1",
        kb_id: str = "kb_1",
        title: str = "部署指南",
    ) -> None:
        self.id = doc_id
        self.kb_id = kb_id
        self.title = title
        self.parse_status = "ready"
        self.chunk_count = 3
        self.source_path = "/tmp/a.md"


class FakeService:
    """最小服务替身：只实现工具用到的读路径。"""

    def __init__(
        self,
        *,
        kbs: list[KnowledgeBase] | None = None,
        chunks: list[StoredChunk] | None = None,
        result: SearchResult | None = None,
        search_error: Exception | None = None,
        documents: dict[str, FakeDocument] | None = None,
    ) -> None:
        self.kbs = [make_kb()] if kbs is None else kbs
        self.chunks = {f"chk_{i}": make_chunk(i) for i in range(3)} if chunks is None else {
            chunk.id: chunk for chunk in chunks
        }
        self.result = result
        self.search_error = search_error
        self.documents = documents if documents is not None else {"doc_1": FakeDocument()}
        self.search_calls: list[dict[str, Any]] = []
        self.list_kbs_calls: list[dict[str, Any]] = []

    # --- 读路径 ---
    def list_kbs(self, *, workspace_ids: list[str]) -> list[KnowledgeBase]:
        self.list_kbs_calls.append({"workspace_ids": list(workspace_ids)})
        return list(self.kbs)

    def get_document(self, doc_id: str) -> FakeDocument | None:
        return self.documents.get(doc_id)

    def get_chunk(self, chunk_id: str) -> StoredChunk | None:
        return self.chunks.get(chunk_id)

    def list_chunks(
        self, doc_id: str, *, offset: int = 0, limit: int = 20
    ) -> list[StoredChunk]:
        ordered = sorted(self.chunks.values(), key=lambda chunk: chunk.chunk_index)
        return ordered[offset : offset + limit]

    def search(self, query: str, **kwargs: Any) -> SearchResult:
        self.search_calls.append({"query": query, **kwargs})
        if self.search_error is not None:
            raise self.search_error
        if self.result is not None:
            return self.result
        return SearchResult(hits=(make_hit(),), mode_used="hybrid", routes=("k", "v"))


class QueryAwareService(FakeService):
    """按查询返回不同命中数，用于验证改写命中与回退。"""

    def __init__(
        self,
        *,
        original_hits: int = 1,
        rewritten_hits: int = 1,
    ) -> None:
        super().__init__(result=SearchResult(hits=(), mode_used="hybrid"))
        self.original_hits = original_hits
        self.rewritten_hits = rewritten_hits

    def search(self, query: str, **kwargs: Any) -> SearchResult:
        self.search_calls.append({"query": query, **kwargs})
        hits = self.original_hits if query == "原始问题" else self.rewritten_hits
        return SearchResult(
            hits=tuple(make_hit(index) for index in range(hits)),
            mode_used="hybrid",
            routes=("vector", "keyword") if hits else (),
        )


def _search_tool(service: Any) -> KnowledgeSearchTool:
    return KnowledgeSearchTool(lambda: service, lambda key: WORKSPACE_IDS)


def _read_tool(service: Any) -> KnowledgeReadTool:
    return KnowledgeReadTool(lambda: service, lambda key: WORKSPACE_IDS)


def _run(tool: Any, **kwargs: Any) -> Any:
    return asyncio.run(tool.execute(**kwargs))


# ---------------------------------------------------------------------------
# 参数 schema
# ---------------------------------------------------------------------------


def test_search_tool_schema_declares_contract() -> None:
    tool = _search_tool(FakeService())
    assert tool.name == "knowledge_search"
    params = tool.parameters
    assert params["type"] == "object"
    assert set(params["properties"]) == {
        "query",
        "kb",
        "mode",
        "limit",
        "rewrite_query",
        "intent",
        "image_description",
    }
    assert params["required"] == ["query"]
    assert params["properties"]["limit"]["minimum"] == 1
    assert params["properties"]["limit"]["maximum"] == 20
    assert params["properties"]["rewrite_query"]["type"] == "string"
    assert "不可信数据" in tool.description
    assert "优先调用本工具" in tool.description
    assert "grep" in tool.description
    assert "rewrite_query" in tool.description


def test_read_tool_schema_declares_contract() -> None:
    tool = _read_tool(FakeService())
    assert tool.name == "knowledge_read"
    params = tool.parameters
    assert set(params["properties"]) == {"handle", "context", "offset", "limit"}
    assert params["required"] == ["handle"]
    assert params["properties"]["context"]["maximum"] == 3
    assert params["properties"]["limit"]["maximum"] == 20
    assert "不可信数据" in tool.description


def test_search_uses_rewrite_query_string() -> None:
    service = QueryAwareService()
    result = _run(
        _search_tool(service),
        query="原始问题",
        rewrite_query="升级失败如何回滚",
    )
    assert [call["query"] for call in service.search_calls] == ["升级失败如何回滚"]
    assert "检索查询使用改写" in str(result)
    assert "升级失败如何回滚" in str(result)


def test_search_uses_original_query_when_rewrite_query_is_blank() -> None:
    service = QueryAwareService()
    _run(_search_tool(service), query="原始问题", rewrite_query="   ")
    assert [call["query"] for call in service.search_calls] == ["原始问题"]


def test_search_does_not_rewrite_without_rewrite_query() -> None:
    service = QueryAwareService()
    _run(_search_tool(service), query="原始问题")
    assert [call["query"] for call in service.search_calls] == ["原始问题"]


def test_search_falls_back_to_original_when_rewrite_has_no_hits() -> None:
    service = QueryAwareService(original_hits=1, rewritten_hits=0)
    result = _run(
        _search_tool(service),
        query="原始问题",
        rewrite_query="改写查询",
    )
    assert [call["query"] for call in service.search_calls] == ["改写查询", "原始问题"]
    assert "已回退原查询" in str(result)


def test_intent_gates_retrieval_for_non_kb_intents() -> None:
    service = QueryAwareService()
    result = _run(_search_tool(service), query="你好", intent="chitchat")
    assert service.search_calls == []
    assert "不检索知识库" in str(result)


def test_search_ignores_rewrite_and_intent_when_rewrite_disabled() -> None:
    service = QueryAwareService()
    tool = KnowledgeSearchTool(
        lambda: service,
        lambda key: WORKSPACE_IDS,
        None,
        lambda: False,
    )
    result = _run(
        tool,
        query="原始问题",
        rewrite_query="升级失败如何回滚",
        intent="chitchat",
    )
    # 对齐 WeKnora enable_rewrite=false：RewriteQuery 保持原句，意图门控也跳过。
    assert [call["query"] for call in service.search_calls] == ["原始问题"]
    assert "不检索知识库" not in str(result)


def test_intent_kb_search_empty_and_unknown_all_retrieve() -> None:
    service = QueryAwareService()
    _run(_search_tool(service), query="回滚", intent="kb_search")
    _run(_search_tool(service), query="回滚", intent="")
    _run(_search_tool(service), query="回滚", intent="unknown_tag")
    assert len(service.search_calls) == 3


def test_image_description_is_accepted_and_ignored() -> None:
    service = QueryAwareService()
    _run(_search_tool(service), query="回滚", image_description="一张截图")
    assert len(service.search_calls) == 1


# ---------------------------------------------------------------------------
# search：历史引用注入（§9.1 ③）
# ---------------------------------------------------------------------------


def _history_tool(service: Any, chunk_ids: Any) -> KnowledgeSearchTool:
    return KnowledgeSearchTool(
        lambda: service,
        lambda key: WORKSPACE_IDS,
        lambda: chunk_ids,
    )


def test_search_injects_history_chunks_as_extra_context() -> None:
    service = FakeService(result=SearchResult(hits=(make_hit(),), mode_used="hybrid"))
    tool = _history_tool(service, ["chk_1", "chk_missing"])
    _run(tool, query="回滚")

    extra = service.search_calls[0]["extra_context"]
    assert [hit.chunk_id for hit in extra] == ["chk_1"]


def test_search_skips_history_chunks_outside_scope() -> None:
    service = FakeService(result=SearchResult(hits=(make_hit(),), mode_used="hybrid"))
    service.chunks["chk_7"] = make_chunk(7, kb_id="kb_other")
    _run(_history_tool(service, ["chk_7"]), query="回滚")

    assert service.search_calls[0]["extra_context"] == []


def test_search_skips_disabled_history_chunks() -> None:
    service = FakeService(result=SearchResult(hits=(make_hit(),), mode_used="hybrid"))
    service.chunks["chk_2"] = replace(service.chunks["chk_2"], is_enabled=False)
    _run(_history_tool(service, ["chk_2"]), query="回滚")

    assert service.search_calls[0]["extra_context"] == []


def test_search_survives_history_provider_failure() -> None:
    service = FakeService(result=SearchResult(hits=(make_hit(),), mode_used="hybrid"))

    def boom() -> list[str]:
        raise RuntimeError("history unavailable")

    tool = KnowledgeSearchTool(
        lambda: service,
        lambda key: WORKSPACE_IDS,
        boom,
    )
    result = _run(tool, query="回滚")

    assert not result.is_error
    assert service.search_calls[0]["extra_context"] == []
    assert "回滚" in str(result)


def test_limit_out_of_range_rejected_by_schema() -> None:
    tool = _search_tool(FakeService())
    assert tool.validate_params({"query": "回滚", "limit": 21})
    assert tool.validate_params({"query": "回滚", "limit": 0})
    assert not tool.validate_params({"query": "回滚", "limit": 20})


# ---------------------------------------------------------------------------
# search：输出格式、转义、预算
# ---------------------------------------------------------------------------


def test_search_escapes_content_and_metadata() -> None:
    hit = make_hit(
        content='<script>alert("x")</script> & 部署失败',
        heading_path='第三章 > "3.2" & <b>',
    )
    service = FakeService(
        result=SearchResult(
            hits=(hit,),
            mode_used="keyword",
            degraded=("vector_unavailable",),
            routes=("k",),
        )
    )
    output = _run(_search_tool(service), query="回滚")

    assert "<script>" not in output
    assert "&lt;script&gt;" in output
    assert "&quot;x&quot;" in output
    assert "&amp;" in output
    # 属性与正文都必须转义
    assert '"3.2"' not in output
    assert output.count("&lt;b&gt;") == 1


def test_search_reports_mode_routes_and_degraded() -> None:
    service = FakeService(
        result=SearchResult(
            hits=(make_hit(),),
            mode_used="keyword",
            degraded=("vector_unavailable",),
            routes=("k",),
        )
    )
    output = _run(_search_tool(service), query="回滚", mode="hybrid")

    assert 'mode="hybrid"' in output
    assert 'mode_used="keyword"' in output
    assert 'routes="k"' in output
    assert 'degraded="vector_unavailable"' in output
    assert 'rerank="none"' in output
    # 降级必须有人话解释
    assert "降级" in output
    # score 只是相对排序分，不是置信度
    assert "未精排" in output
    assert "相对排序" in output
    assert 'score="0.900"' in output
    assert "chunk://chk_0" in output
    assert "doc://doc_1" in output
    assert 'title="部署指南"' in output
    assert 'kb="产品手册"' in output
    assert 'heading="第三章"' in output
    assert 'page="12"' in output
    assert "不可信数据" in output


def test_search_score_is_three_decimals_and_ordered() -> None:
    hits = tuple(
        make_hit(i, f"内容{i}", score=score)
        for i, score in enumerate((0.9123, 0.5, 0.12345))
    )
    service = FakeService(result=SearchResult(hits=hits, mode_used="hybrid"))
    output = _run(_search_tool(service), query="回滚")

    assert 'score="0.912"' in output
    assert 'score="0.500"' in output
    assert 'score="0.123"' in output
    assert output.index('rank="1"') < output.index('rank="2"') < output.index('rank="3"')


def test_search_reports_rerank_cut_notice() -> None:
    service = FakeService(
        result=SearchResult(
            hits=(make_hit(),),
            mode_used="hybrid",
            rerank="ok",
            candidates_in=40,
            candidates_kept=8,
        )
    )
    output = _run(_search_tool(service), query="回滚")

    # §7 RecordRetrievalCut：必须让模型知道结果只是候选子集。
    assert 'kind="rerank_cut"' in output
    assert "检索从 40 条候选中保留了 8 条结果" in output


def test_search_omits_rerank_cut_when_nothing_was_cut() -> None:
    service = FakeService(
        result=SearchResult(
            hits=(make_hit(),),
            mode_used="hybrid",
            rerank="ok",
            candidates_in=3,
            candidates_kept=3,
        )
    )
    output = _run(_search_tool(service), query="回滚")

    assert 'kind="rerank_cut"' not in output


def test_search_truncates_item_and_total_budget() -> None:
    hits = tuple(
        make_hit(
            i,
            "字" * 2000,
            score=1.0 - i * 0.01,
            heading_path="",
            page=None,
        )
        for i in range(10)
    )
    service = FakeService(result=SearchResult(hits=hits, mode_used="hybrid"))
    output = _run(_search_tool(service), query="回滚")

    assert len(output) <= 6000 + 200
    assert "<omitted count=" in output
    assert 'reason="budget"' in output
    # §9.3 输出预算 = 6000 × 4/5：显式告诉模型有内容被省略。
    assert 'kind="budget"' in output
    assert "4800 字符" in output
    # 单条正文 ≤1000 字符 + 截断提示
    assert "字" * 1001 not in output
    assert "已截断" in output
    # 头尾完整
    assert "<knowledge_results" in output
    assert output.rstrip().endswith("</knowledge_results>")


# ---------------------------------------------------------------------------
# search：四态语义
# ---------------------------------------------------------------------------


def test_search_failure_says_not_executed() -> None:
    service = FakeService(search_error=KnowledgeSearchError("boom"))
    result = _run(_search_tool(service), query="回滚")

    assert result.is_error
    assert "没有执行检索" in result
    assert "boom" in result


def test_search_empty_result_is_not_failure() -> None:
    service = FakeService(
        result=SearchResult(hits=(), mode_used="keyword", reason="no_candidates")
    )
    result = _run(_search_tool(service), query="回滚")

    assert not result.is_error
    assert "没有检索到" in result
    assert "no_candidates" in result
    assert "mode_used=keyword" in result


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("no_candidates", "没有检索到"),
        ("filtered", "过滤"),
        ("no_scope", "没有可见知识库"),
        ("empty_query_tokens", "分词"),
    ],
)
def test_search_empty_reasons_are_distinct(reason: str, expected: str) -> None:
    service = FakeService(
        result=SearchResult(
            hits=(),
            mode_used="keyword",
            degraded=("empty_query_tokens",),
            reason=reason,
        )
    )
    result = _run(_search_tool(service), query="回滚")

    assert not result.is_error
    assert expected in result
    assert reason in result
    assert "degraded=empty_query_tokens" in result


def test_search_empty_messages_differ_from_each_other() -> None:
    texts = {}
    for reason in ("no_candidates", "filtered", "no_scope", "empty_query_tokens"):
        service = FakeService(
            result=SearchResult(hits=(), mode_used="keyword", reason=reason)
        )
        texts[reason] = str(_run(_search_tool(service), query="回滚"))
    assert len(set(texts.values())) == 4


# ---------------------------------------------------------------------------
# search：kb 解析
# ---------------------------------------------------------------------------


def test_search_passes_workspace_scope_and_defaults() -> None:
    service = FakeService()
    _run(_search_tool(service), query="回滚")

    call = service.search_calls[0]
    assert call["query"] == "回滚"
    assert call["workspace_ids"] == WORKSPACE_IDS
    assert call["kb_ids"] is None
    assert call["mode"] == "hybrid"
    assert call["limit"] == 8


def test_search_resolves_kb_name_and_uri() -> None:
    service = FakeService()
    _run(_search_tool(service), query="回滚", kb="产品手册", limit=3)
    _run(_search_tool(service), query="回滚", kb="kb://kb_1")

    assert service.search_calls[0]["kb_ids"] == ["kb_1"]
    assert service.search_calls[0]["limit"] == 3
    assert service.search_calls[1]["kb_ids"] == ["kb_1"]


def test_search_duplicate_kb_name_is_parameter_error() -> None:
    kbs = [
        make_kb("kb_1", "手册", workspace_id="ws1"),
        make_kb("kb_2", "手册", workspace_id="ws1"),
    ]
    service = FakeService(kbs=kbs)
    result = _run(_search_tool(service), query="回滚", kb="手册")

    assert result.is_error
    assert "kb://" in result
    assert "同名" in result
    assert service.search_calls == []


def test_search_prefers_current_workspace_for_duplicate_name() -> None:
    kbs = [
        make_kb("kb_1", "手册", workspace_id="default"),
        make_kb("kb_2", "手册", workspace_id="ws1"),
    ]
    service = FakeService(kbs=kbs)
    _run(_search_tool(service), query="回滚", kb="手册")

    # "ws1" 是当前工作区，优先命中
    assert service.search_calls[0]["kb_ids"] == ["kb_2"]


def test_search_unknown_kb_is_parameter_error() -> None:
    service = FakeService()
    result = _run(_search_tool(service), query="回滚", kb="不存在的库")

    assert result.is_error
    assert "参数错误" in result
    assert service.search_calls == []


def test_search_kb_uri_out_of_scope_is_parameter_error() -> None:
    service = FakeService()
    result = _run(_search_tool(service), query="回滚", kb="kb://kb_other")

    assert result.is_error
    assert "作用域" in result
    assert service.search_calls == []


def test_search_invalid_mode_is_parameter_error() -> None:
    service = FakeService(search_error=ValueError("unsupported knowledge search mode"))
    result = _run(_search_tool(service), query="回滚", mode="fuzzy")

    assert result.is_error
    assert "参数错误" in result


# ---------------------------------------------------------------------------
# 无知识库 / 未启用：友好提示而非报错
# ---------------------------------------------------------------------------


def test_search_without_visible_kb_is_friendly() -> None:
    result = _run(_search_tool(FakeService(kbs=[])), query="回滚")

    assert not result.is_error
    assert "当前没有可用知识库" in result


def test_search_without_service_is_friendly() -> None:
    result = _run(_search_tool(None), query="回滚")

    assert not result.is_error
    assert "当前没有可用知识库" in result


def test_read_without_service_is_friendly() -> None:
    result = _run(_read_tool(None), handle="chunk://chk_1")

    assert not result.is_error
    assert "当前没有可用知识库" in result


# ---------------------------------------------------------------------------
# enabled 门控
# ---------------------------------------------------------------------------


def _tool_context(attributes: dict[str, Any]) -> ToolContext:
    return ToolContext(config=MagicMock(), workspace="/tmp", attributes=attributes)


def test_enabled_requires_service_and_flag() -> None:
    service = FakeService()
    assert KnowledgeSearchTool.enabled(_tool_context({"knowledge_service": service}))
    assert KnowledgeReadTool.enabled(_tool_context({"knowledge_service": service}))
    assert not KnowledgeSearchTool.enabled(_tool_context({}))
    assert not KnowledgeSearchTool.enabled(
        _tool_context({"knowledge_service": service, "knowledge_enabled": False})
    )


def test_create_accepts_object_and_callable_service() -> None:
    service = FakeService()
    direct = KnowledgeSearchTool.create(_tool_context({"knowledge_service": service}))
    lazy = KnowledgeSearchTool.create(
        _tool_context(
            {
                "knowledge_service": lambda: service,
                "knowledge_workspace_ids": lambda key: WORKSPACE_IDS,
                "knowledge_enabled": True,
            }
        )
    )
    assert service.search_calls == []
    for tool in (direct, lazy):
        asyncio.run(tool.execute(query="回滚"))
    assert direct._service() is service
    assert lazy._service() is service
    assert len(service.search_calls) == 2


# ---------------------------------------------------------------------------
# read：chunk 扩读
# ---------------------------------------------------------------------------


def test_read_chunk_returns_neighbors_in_order() -> None:
    output = _run(_read_tool(FakeService()), handle="chunk://chk_1", context=1)

    assert "第0块正文" in output and "第1块正文" in output and "第2块正文" in output
    assert output.index("第0块正文") < output.index("第1块正文") < output.index("第2块正文")
    assert 'index="1"' in output
    assert 'page="12"' in output
    assert 'title="部署指南"' in output


def test_read_chunk_without_context_returns_single_chunk() -> None:
    output = _run(_read_tool(FakeService()), handle="chunk://chk_1", context=0)

    assert "第1块正文" in output
    assert "第0块正文" not in output
    assert "第2块正文" not in output


def test_read_chunk_escapes_content() -> None:
    chunks = [make_chunk(0, "<img src=x onerror=alert(1)>")]
    output = _run(_read_tool(FakeService(chunks=chunks)), handle="chunk://chk_0", context=0)

    assert "<img" not in output
    assert "&lt;img" in output


# ---------------------------------------------------------------------------
# read：doc 扩读
# ---------------------------------------------------------------------------


def test_read_document_returns_metadata_and_range() -> None:
    output = _run(_read_tool(FakeService()), handle="doc://doc_1", offset=1, limit=2)

    assert 'title="部署指南"' in output
    assert 'status="ready"' in output
    assert 'chunks="3"' in output
    assert "第1块正文" in output and "第2块正文" in output
    assert "第0块正文" not in output


def test_read_document_respects_char_budget() -> None:
    chunks = [make_chunk(i, "字" * 3000) for i in range(10)]
    output = _run(_read_tool(FakeService(chunks=chunks)), handle="doc://doc_1", limit=10)

    assert len(output) <= 8000 + 200
    assert 'reason="budget"' in output


# ---------------------------------------------------------------------------
# read：句柄安全
# ---------------------------------------------------------------------------


def test_read_rejects_hallucinated_chunk_handle() -> None:
    result = _run(_read_tool(FakeService()), handle="chunk://chk_missing", context=1)

    assert result.is_error
    assert "不存在" in result


def test_read_rejects_hallucinated_document_handle() -> None:
    result = _run(_read_tool(FakeService()), handle="doc://doc_missing")

    assert result.is_error
    assert "不存在" in result


def test_read_rejects_out_of_scope_document() -> None:
    service = FakeService(kbs=[], documents={"doc_1": FakeDocument(kb_id="kb_hidden")})
    result = _run(_read_tool(service), handle="doc://doc_1")

    assert result.is_error
    assert "作用域" in result


def test_read_rejects_out_of_scope_chunk() -> None:
    chunk = make_chunk(1, kb_id="kb_hidden")
    service = FakeService(kbs=[], chunks=[chunk])
    result = _run(_read_tool(service), handle="chunk://chk_1", context=1)

    assert result.is_error
    assert "作用域" in result


@pytest.mark.parametrize("handle", ["chk_0", "http://example.com/x", "chunk://", "kb://kb_1"])
def test_read_rejects_malformed_handle(handle: str) -> None:
    result = _run(_read_tool(FakeService()), handle=handle)

    assert result.is_error
    assert "chunk://" in result and "doc://" in result


# ---------------------------------------------------------------------------
# AgentLoop 注入
# ---------------------------------------------------------------------------


def _make_loop(tmp_path: Path, **overrides: Any) -> Any:
    from nanobot.agent.loop import AgentLoop
    from nanobot.providers.base import LLMProvider

    provider = MagicMock(spec=LLMProvider)
    provider.get_default_model.return_value = "test-model"
    provider.generation = SimpleNamespace(
        max_tokens=4096, temperature=0.1, reasoning_effort=None
    )
    provider.estimate_prompt_tokens = MagicMock(return_value=(10_000, "test"))
    kwargs: dict[str, Any] = dict(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        context_window_tokens=128_000,
    )
    kwargs.update(overrides)
    return AgentLoop(**kwargs)


def _capture_tool_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    from nanobot.agent.tools.loader import ToolLoader

    captured: dict[str, Any] = {}
    original = ToolLoader.load

    def load(self: ToolLoader, ctx: ToolContext, registry: Any) -> Any:
        captured["ctx"] = ctx
        return original(self, ctx, registry)

    monkeypatch.setattr(ToolLoader, "load", load)
    return captured


def test_loop_injects_knowledge_attributes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nanobot.config.schema import KnowledgeConfig, KnowledgeEmbeddingConfig

    captured = _capture_tool_context(monkeypatch)
    config = KnowledgeConfig(
        database_path=str(tmp_path / "knowledge" / "state.db"),
        embedding=KnowledgeEmbeddingConfig(backend="none"),
    )
    loop = _make_loop(tmp_path, knowledge_config=config)
    try:
        attrs = captured["ctx"].attributes
        assert "knowledge_service" in attrs
        resolver = attrs["knowledge_workspace_ids"]
        assert resolver.__self__ is loop
        assert resolver.__func__ is loop._memory_workspace_ids_for_key.__func__
        assert attrs["knowledge_enabled"] is True
        assert callable(attrs["knowledge_graph_enabled"])
        assert attrs["knowledge_graph_enabled"]() is False
        assert callable(attrs["knowledge_rewrite_enabled"])
        assert attrs["knowledge_rewrite_enabled"]() is True
        # 惰性：注册期不构造真实服务
        assert loop._knowledge_service_instance is None
        assert callable(attrs["knowledge_service"])
        service = attrs["knowledge_service"]()
        assert service is loop._knowledge_service
        assert loop._knowledge_service_instance is not None
        assert loop.tools.get("knowledge_search") is not None
        assert loop.tools.get("knowledge_read") is not None
        assert loop.tools.get("knowledge_graph") is None
    finally:
        asyncio.run(loop.aclose())


def test_loop_registers_graph_tool_when_graph_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nanobot.config.schema import (
        KnowledgeConfig,
        KnowledgeEmbeddingConfig,
        KnowledgeGraphConfig,
    )

    captured = _capture_tool_context(monkeypatch)
    config = KnowledgeConfig(
        database_path=str(tmp_path / "knowledge" / "state.db"),
        embedding=KnowledgeEmbeddingConfig(backend="none"),
        graph=KnowledgeGraphConfig(enabled=True),
    )
    loop = _make_loop(tmp_path, knowledge_config=config)
    try:
        attrs = captured["ctx"].attributes
        assert attrs["knowledge_graph_enabled"]() is True
        assert loop.tools.get("knowledge_graph") is not None
    finally:
        asyncio.run(loop.aclose())


def test_loop_rewrite_flag_follows_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nanobot.config.schema import (
        KnowledgeConfig,
        KnowledgeEmbeddingConfig,
        KnowledgeRetrievalConfig,
    )

    captured = _capture_tool_context(monkeypatch)
    config = KnowledgeConfig(
        database_path=str(tmp_path / "knowledge" / "state.db"),
        embedding=KnowledgeEmbeddingConfig(backend="none"),
        retrieval=KnowledgeRetrievalConfig(enable_rewrite=False),
    )
    loop = _make_loop(tmp_path, knowledge_config=config)
    try:
        attrs = captured["ctx"].attributes
        assert attrs["knowledge_rewrite_enabled"]() is False
        assert loop.tools.get("knowledge_search") is not None
    finally:
        asyncio.run(loop.aclose())


def test_loop_without_knowledge_config_keeps_tools_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_tool_context(monkeypatch)
    loop = _make_loop(tmp_path)
    try:
        attrs = captured["ctx"].attributes
        assert attrs["knowledge_service"]() is None
        assert attrs["knowledge_enabled"] is False
        assert attrs["knowledge_graph_enabled"]() is False
        assert not KnowledgeSearchTool.enabled(captured["ctx"])
        assert not KnowledgeReadTool.enabled(captured["ctx"])
        assert loop._knowledge_service_instance is None
        assert loop.tools.get("knowledge_graph") is None
    finally:
        asyncio.run(loop.aclose())


def test_loop_disabled_knowledge_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nanobot.config.schema import KnowledgeConfig

    captured = _capture_tool_context(monkeypatch)
    loop = _make_loop(tmp_path, knowledge_config=KnowledgeConfig(enabled=False))
    try:
        attrs = captured["ctx"].attributes
        assert attrs["knowledge_enabled"] is False
        assert attrs["knowledge_service"]() is None
        assert not KnowledgeSearchTool.enabled(captured["ctx"])
    finally:
        asyncio.run(loop.aclose())


def test_history_messages_supports_snapshot_api():
    class Sessions:
        def read_session_snapshot(self, key):
            assert key == "websocket:1"
            return SimpleNamespace(messages=[{"content": "see chunk://chk_1"}])

    messages = _history_messages(Sessions(), "websocket:1")
    assert messages == [{"content": "see chunk://chk_1"}]


def test_history_messages_supports_legacy_read_dict():
    class Sessions:
        def read(self, key):
            return {"messages": [{"content": "chunk://chk_2"}]}

    assert _history_messages(Sessions(), "k") == [{"content": "chunk://chk_2"}]


def test_render_includes_graph_path_when_evidence_present():
    hit = SearchHit(
        chunk_id="c1",
        doc_id="d1",
        kb_id="kb",
        chunk_index=0,
        heading_path="",
        page=None,
        locator="",
        content="x",
        char_count=1,
        score=0.9,
        graph_evidence=GraphEvidencePayload(
            seeds=("A",),
            path=(("A", "依赖", "B"),),
            hop=1,
            entity_ids=("e1", "e2"),
        ),
    )
    text = render_search_result(
        SearchResult(
            hits=(hit,), mode_used="hybrid", routes=("vector", "graph")
        ),
        mode_requested="hybrid",
    )
    assert 'graph_path="A —依赖→ B"' in text
