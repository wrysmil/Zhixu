"""真实 Neo4j + 真实 MiniMax 的知识图谱闭环脚本（可重复运行）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nanobot.config.loader import load_config
from nanobot.knowledge.service import get_knowledge_service, reset_knowledge_service

ROOT = Path(__file__).resolve().parent
KB_NAME = "Graph E2E"
SAMPLES = {
    ROOT / "sample-rollout.md": (
        "# 灰度发布流程\n\n"
        "灰度发布流程先把新版本发布到 5% 流量，由值班二号监控 web-api 网关错误率。\n"
        "当错误率超过 1% 时，值班二号负责回滚，并通知 SRE 团队。\n"
        "发布平台统一编排整个灰度发布流程，值班二号在回滚后补齐故障复盘。\n"
    ),
    ROOT / "sample-oncall.md": (
        "# 值班职责\n\n"
        "值班二号是发布值班人员，负责回滚，并通知 SRE 团队。\n"
        "值班二号还需要在故障恢复后补齐故障复盘。\n"
    ),
    ROOT / "sample-link.md": (
        "# 责任关系\n\n"
        "灰度发布流程由值班二号负责。\n"
    ),
}


def main(config_path: str) -> int:
    config = load_config(Path(config_path))
    reset_knowledge_service()
    service = get_knowledge_service(config.knowledge)

    existing = [kb for kb in service.list_kbs(workspace_ids=["default"]) if kb.name == KB_NAME]
    for kb in existing:
        service.delete_kb(kb.id)
    kb = service.create_kb(name=KB_NAME, workspace_id="default", description="graph e2e")

    graph = service.graph
    if graph is None:
        print(json.dumps({"error": "graph service missing"}))
        return 1

    docs: dict[str, object] = {}
    for path, content in SAMPLES.items():
        path.write_text(content, encoding="utf-8")
        outcome = service.add_file(path, kb_id=kb.id, workspace_id="default", force=True)
        doc = outcome.document
        if not service.ingest_now(doc.id):
            print(json.dumps({"error": "ingest claim failed", "doc": doc.id}))
            return 1
        doc = service.get_document(doc.id)
        docs[path.name] = doc
        print(
            json.dumps(
                {"stage": "ingest", "docId": doc.id, "title": doc.title,
                 "status": doc.parse_status},
                ensure_ascii=False,
            )
        )
        graph.enqueue_document(doc.id, force=True)
        graph.process_document(doc.id)
        state = graph.repo.get_state(doc.id)
        print(
            json.dumps(
                {
                    "stage": "extract",
                    "docId": doc.id,
                    "status": state.status if state else None,
                    "entities": state.entity_count if state else None,
                    "relations": state.relation_count if state else None,
                    "error": state.error_code if state else None,
                },
                ensure_ascii=False,
            )
        )
        if state is None or state.status != "ready":
            return 1

    # 受控关系文档：保证跨块最短路径可稳定验证（其余文档均为真实抽取）。
    link_doc = docs.get("sample-link.md")
    if link_doc is not None:
        chunks = service.list_chunks(link_doc.id, limit=10)
        if chunks:
            touched, _ = graph.repo.replace_document_graph(
                link_doc.id,
                kb.id,
                entities=[("灰度发布流程", "概念", ""), ("值班二号", "人物", "")],
                relations=[("灰度发布流程", "值班二号", "负责", "", 9.0)],
                mentions=[
                    ("灰度发布流程", chunks[0].id),
                    ("值班二号", chunks[0].id),
                ],
            )
            graph._sync.sync_entities(kb.id, list(touched))
            print(
                json.dumps(
                    {"stage": "controlled-relation", "docId": link_doc.id,
                     "entities": sorted(touched)},
                    ensure_ascii=False,
                )
            )

    stats = graph.stats(kb.id)
    entities = graph.list_entities(kb.id, limit=10)
    print(json.dumps({"stage": "stats", "stats": stats, "entities": entities}, ensure_ascii=False))

    result = service.search(
        "灰度发布流程", workspace_ids=["default"], kb_ids=[kb.id], limit=5
    )
    print(
        json.dumps(
            {
                "stage": "search",
                "routes": list(result.routes),
                "degraded": list(result.degraded),
                "mode": result.mode_used,
                "hits": [
                    {
                        "chunkId": hit.chunk_id,
                        "score": hit.score,
                        "graph": (
                            {
                                "seeds": list(hit.graph_evidence.seeds),
                                "path": [list(step) for step in hit.graph_evidence.path],
                                "hop": hit.graph_evidence.hop,
                            }
                            if hit.graph_evidence
                            else None
                        ),
                    }
                    for hit in result.hits
                ],
            },
            ensure_ascii=False,
        )
    )

    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(
        config.knowledge.graph.uri,
        auth=(config.knowledge.graph.username, config.knowledge.graph.password),
    )
    try:
        with driver.session(database=config.knowledge.graph.database) as session:
            nodes = session.run(
                "MATCH (e:Entity {kb_id:$kb}) RETURN count(e) AS n", kb=kb.id
            ).single()["n"]
            rels = session.run(
                "MATCH (:Entity {kb_id:$kb})-[r:RELATED]->(:Entity {kb_id:$kb}) "
                "RETURN count(r) AS n",
                kb=kb.id,
            ).single()["n"]
    finally:
        driver.close()
    print(json.dumps({"stage": "neo4j", "kbId": kb.id, "nodes": nodes, "relations": rels}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "config.json")))
