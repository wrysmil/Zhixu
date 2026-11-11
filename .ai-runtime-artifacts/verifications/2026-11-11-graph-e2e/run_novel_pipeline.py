"""公版短篇小说《孔乙己》→ 真实抽取 → 选连线最多的实体做子图。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nanobot.config.loader import load_config
from nanobot.knowledge.service import get_knowledge_service, reset_knowledge_service

ROOT = Path(__file__).resolve().parent
KB_NAME = "Graph Novel"
NOVEL = ROOT / "novel-kongyiji.md"


def main(config_path: str) -> int:
    config = load_config(Path(config_path))
    reset_knowledge_service()
    service = get_knowledge_service(config.knowledge)
    for kb in service.list_kbs(workspace_ids=["default"]):
        if kb.name == KB_NAME:
            service.delete_kb(kb.id)
    kb = service.create_kb(name=KB_NAME, workspace_id="default", description="孔乙己")
    graph = service.graph
    if graph is None:
        return 1

    outcome = service.add_file(NOVEL, kb_id=kb.id, workspace_id="default", force=True)
    doc = outcome.document
    assert service.ingest_now(doc.id)
    graph.enqueue_document(doc.id, force=True)
    graph.process_document(doc.id)
    state = graph.repo.get_state(doc.id)
    print(
        json.dumps(
            {
                "doc": doc.title,
                "status": state.status if state else None,
                "entities": state.entity_count if state else None,
                "relations": state.relation_count if state else None,
            },
            ensure_ascii=False,
        )
    )
    stats = graph.stats(kb.id)
    print(json.dumps({"stats": stats}, ensure_ascii=False))

    entities = graph.list_entities(kb.id, limit=100)["entities"]
    print(
        json.dumps(
            {
                "entities": [
                    {"name": e["name"], "type": e["type"], "mentions": e["mentionCount"]}
                    for e in entities[:20]
                ]
            },
            ensure_ascii=False,
        )
    )

    best = None
    for entity in entities[:8]:
        sub = graph.subgraph(entity["id"], ["default"], hops=1, limit=50)
        summary = {
            "name": entity["name"],
            "id": entity["id"],
            "nodes": len(sub["nodes"]),
            "edges": len(sub["edges"]),
            "sample": sub["edges"][:5],
        }
        print(json.dumps(summary, ensure_ascii=False))
        if best is None or summary["edges"] > best["edges"]:
            best = summary
    if best:
        (ROOT / "novel-center.json").write_text(
            json.dumps(best, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps({"center": best}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "config.json")))
