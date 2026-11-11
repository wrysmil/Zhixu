"""`knowledge_graph` 工具契约：实体/关系/证据渲染、截断与失败语义。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from nanobot.agent.tools.knowledge_graph import KnowledgeGraphTool
from nanobot.knowledge.graph.models import GraphEntity, GraphHealth, GraphRelation
from nanobot.knowledge.graph.service import GraphQueryResult
from nanobot.knowledge.models import KnowledgeBase
from nanobot.knowledge.repository import StoredChunk


def make_kb(kb_id: str = "kb_1", name: str = "产品手册") -> KnowledgeBase:
    return KnowledgeBase(
        id=kb_id,
        name=name,
        description="",
        workspace_id="ws1",
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


def make_chunk(chunk_id: str = "chk_1", content: str = "正文") -> StoredChunk:
    return StoredChunk(
        row_id=1,
        id=chunk_id,
        doc_id="doc_1",
        kb_id="kb_1",
        workspace_id="ws1",
        chunk_index=0,
        content=content,
        heading_path="第三章",
        page=12,
        locator="",
        start_offset=0,
        end_offset=len(content),
        char_count=len(content),
        content_hash="h",
        is_enabled=True,
    )


def make_entity(entity_id: str, name: str, *, description: str = "") -> GraphEntity:
    return GraphEntity(
        id=entity_id,
        kb_id="kb_1",
        name=name,
        name_norm=name.lower(),
        type="concept",
        description=description,
        mention_count=1,
    )


class FakeGraph:
    def __init__(
        self,
        result: GraphQueryResult | Exception | None = None,
        *,
        enabled: bool = True,
        health: GraphHealth | None = None,
    ) -> None:
        self.enabled = enabled
        self.config = SimpleNamespace(
            expansion=SimpleNamespace(
                seed_limit=5, max_entities=20, timeout_seconds=0.8
            )
        )
        self.result = result if result is not None else GraphQueryResult()
        self._health = health or GraphHealth("ready")
        self.calls: list[dict[str, Any]] = []

    def health(self) -> GraphHealth:
        return self._health

    def query_related(self, kb_id: str, query: str, **kwargs: Any) -> GraphQueryResult:
        self.calls.append({"kb_id": kb_id, "query": query, **kwargs})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FakeService:
    def __init__(
        self,
        graph: FakeGraph | None = None,
        *,
        kbs: list[KnowledgeBase] | None = None,
    ) -> None:
        self.graph = graph
        self.kbs = kbs or [make_kb()]

    def list_kbs(self, *, workspace_ids: list[str]) -> list[KnowledgeBase]:
        return list(self.kbs)

    def get_document(self, doc_id: str) -> SimpleNamespace | None:
        if doc_id != "doc_1":
            return None
        return SimpleNamespace(title="网关手册")


def _tool(service: Any) -> KnowledgeGraphTool:
    return KnowledgeGraphTool(lambda: service, lambda key: ["default", "ws1"])


def _run(tool: KnowledgeGraphTool, **kwargs: Any) -> Any:
    return asyncio.run(tool.execute(**kwargs))


def test_schema_declares_contract() -> None:
    tool = _tool(FakeService(FakeGraph()))
    assert tool.name == "knowledge_graph"
    params = tool.parameters
    assert set(params["properties"]) == {"query", "kb", "hops", "limit"}
    assert params["required"] == ["query"]
    assert params["properties"]["hops"]["minimum"] == 1
    assert params["properties"]["hops"]["maximum"] == 2
    assert params["properties"]["limit"]["maximum"] == 20
    assert "不可信数据" in tool.description
    assert "knowledge_search" in tool.description


def test_enabled_requires_graph_flag() -> None:
    def ctx(flag: bool) -> SimpleNamespace:
        return SimpleNamespace(
            attributes={
                "knowledge_service": lambda: None,
                "knowledge_graph_enabled": lambda: flag,
            }
        )

    assert not KnowledgeGraphTool.enabled(ctx(False))
    assert KnowledgeGraphTool.enabled(ctx(True))


def test_renders_entities_relations_and_evidence() -> None:
    seed = make_entity("e1", "网关")
    neighbor = make_entity("e2", "值班二号", description="<负责升级>")
    relation = GraphRelation("r1", "kb_1", "e1", "e2", "升级给", "", 8.0, 2)
    result = GraphQueryResult(
        seeds=(seed,),
        entities=(seed, neighbor),
        relations=(relation,),
        chunks=(make_chunk(),),
        relations_total=1,
        chunks_total=1,
    )
    graph = FakeGraph(result)
    tool = _tool(FakeService(graph))
    rendered = str(_run(tool, query="网关"))

    assert '<knowledge_graph query="网关" hops="1"' in rendered
    assert 'source="网关" type="升级给" target="值班二号"' in rendered
    assert 'name="网关" type="concept" mentions="1" seed="true"' in rendered
    assert 'name="值班二号" type="concept" mentions="1" seed="false"' in rendered
    assert "&lt;负责升级&gt;" in rendered
    assert 'chunk="chunk://chk_1"' in rendered
    assert "knowledge_read" in rendered
    assert graph.calls[0]["hops"] == 1
    assert graph.calls[0]["max_chunks"] == 30
    assert graph.calls[0]["display_chunks"] == 10
    assert graph.calls[0]["max_relations"] == 30


def test_truncation_is_visible() -> None:
    entity = make_entity("e1", "网关")
    result = GraphQueryResult(
        seeds=(entity,),
        entities=(entity,),
        relations=(),
        chunks=(),
        relations_total=142,
        chunks_total=40,
        truncated=True,
    )
    rendered = str(_run(_tool(FakeService(FakeGraph(result))), query="网关"))
    assert 'truncated="true"' in rendered
    assert "关系 0/142" in rendered
    assert "证据 0/40" in rendered
    assert "不要把当前列表当成完整邻域" in rendered


def test_no_seed_is_empty_not_failure() -> None:
    tool = _tool(FakeService(FakeGraph(GraphQueryResult())))
    result = _run(tool, query="不存在")
    assert not result.is_error
    assert "没有匹配到有关系的实体" in result


def test_backend_unavailable_is_failure() -> None:
    graph = FakeGraph(health=GraphHealth("degraded", "neo4j_unavailable"))
    result = _run(_tool(FakeService(graph)), query="网关")
    assert result.is_error
    assert "图谱后端不可用" in result
    assert "neo4j_unavailable" in result


def test_graph_disabled_is_explained() -> None:
    result = _run(_tool(FakeService(FakeGraph(enabled=False))), query="网关")
    assert not result.is_error
    assert "知识图谱未启用" in result


def test_query_failure_is_failure_not_empty() -> None:
    result = _run(
        _tool(FakeService(FakeGraph(RuntimeError("timeout")))), query="网关"
    )
    assert result.is_error
    assert "图谱查询失败" in result
    assert "不代表知识库里没有相关实体" in result


def test_partial_kb_failure_is_reported() -> None:
    seed = make_entity("e1", "网关")
    good = FakeGraph(
        GraphQueryResult(seeds=(seed,), entities=(seed,), chunks=(make_chunk(),))
    )

    class PartialGraph(FakeGraph):
        def query_related(self, kb_id: str, query: str, **kwargs: Any) -> GraphQueryResult:
            if kb_id == "kb_2":
                raise RuntimeError("down")
            return super().query_related(kb_id, query, **kwargs)

    service = FakeService(
        PartialGraph(good.result), kbs=[make_kb(), make_kb("kb_2", "坏库")]
    )
    rendered = str(_run(_tool(service), query="网关"))
    assert 'kind="partial"' in rendered
    assert "坏库: RuntimeError: down" in rendered


def test_out_of_scope_kb_is_parameter_error() -> None:
    result = _run(_tool(FakeService(FakeGraph())), query="网关", kb="kb://missing")
    assert result.is_error
    assert "不存在或不在当前作用域" in result
