"""复杂文档图谱：6 份互相引用的文档 → 真实抽取 → Neo4j → 子图边验证。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from nanobot.config.loader import load_config
from nanobot.knowledge.service import get_knowledge_service, reset_knowledge_service

ROOT = Path(__file__).resolve().parent
KB_NAME = "Graph Complex"
DOCS = {
    "release-process.md": (
        "# 灰度发布流程\n\n"
        "发布平台负责编排灰度发布。灰度发布先放量 5% 给 web-api 网关，值班二号监控错误率。\n"
        "错误率超过 1% 时，值班二号执行回滚，并通知 SRE 团队。发布平台记录发布批次。\n"
    ),
    "oncall.md": (
        "# 值班手册\n\n"
        "值班二号负责发布期值守，接收 Prometheus 告警。发现 web-api 网关错误率异常时，\n"
        "值班二号执行回滚，通知 SRE 团队，并在故障恢复后补齐故障复盘。\n"
    ),
    "observability.md": (
        "# 监控与告警\n\n"
        "Prometheus 采集 web-api 网关指标，告警平台把错误率告警推送给值班二号。\n"
        "错误率阈值由 SRE 团队维护。\n"
    ),
    "postmortem.md": (
        "# 故障复盘\n\n"
        "故障复盘由 SRE 团队组织，值班二号提供时间线。复盘结论会更新 runbook 和错误率阈值。\n"
    ),
    "payment.md": (
        "# 支付链路\n\n"
        "支付服务调用订单服务，订单服务写入订单数据库。支付服务通过消息队列异步通知对账任务。\n"
        "对账任务每日核对订单数据库与支付流水。\n"
    ),
    "payment-runbook.md": (
        "# 支付值班\n\n"
        "支付服务故障时由值班一号回滚，并通知 SRE 团队。支付链路依赖订单服务和消息队列。\n"
        "对账任务异常时检查订单数据库。\n"
    ),
}


def main(config_path: str) -> int:
    config = load_config(Path(config_path))
    reset_knowledge_service()
    service = get_knowledge_service(config.knowledge)
    for kb in service.list_kbs(workspace_ids=["default"]):
        if kb.name == KB_NAME:
            service.delete_kb(kb.id)
    kb = service.create_kb(name=KB_NAME, workspace_id="default", description="complex graph")
    graph = service.graph
    if graph is None:
        print(json.dumps({"error": "graph disabled"}))
        return 1

    docs = {}
    for name, content in DOCS.items():
        path = ROOT / "complex" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        outcome = service.add_file(path, kb_id=kb.id, workspace_id="default", force=True)
        doc = outcome.document
        assert service.ingest_now(doc.id)
        graph.enqueue_document(doc.id, force=True)
        graph.process_document(doc.id)
        state = graph.repo.get_state(doc.id)
        docs[name] = doc.id
        print(
            json.dumps(
                {
                    "doc": name,
                    "id": doc.id,
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
    by_name = {entity["name"]: entity["id"] for entity in entities}
    for center in ("灰度发布流程", "值班二号", "支付服务", "SRE 团队", "订单服务"):
        entity_id = by_name.get(center)
        if not entity_id:
            print(json.dumps({"center": center, "missing": True}, ensure_ascii=False))
            continue
        detail = graph.get_entity(entity_id, ["default"])
        sub = graph.subgraph(entity_id, ["default"], hops=1, limit=50)
        print(
            json.dumps(
                {
                    "center": center,
                    "neighbors": len(detail["neighbors"]) if detail else 0,
                    "nodes": len(sub["nodes"]),
                    "edges": len(sub["edges"]),
                    "edgeSample": sub["edges"][:6],
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "config.json")))
