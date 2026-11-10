"""doc2query 实机探针：真实 HTTP + 真实 SQLite/Chroma + 本地 embedding。

覆盖链路：
    入库 → 逐块调 chat 模型生成问题 → 问题写 FTS 与向量（id = <chunk>-qN）
    → 关键词/语义检索命中问题行 → 回填父块正文。

跑法（无需外部 key；内置 OpenAI 兼容 mock）：
    python doc2query_probe.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, r"D:\studyspace\project\Zhixu")

from nanobot.config.schema import (  # noqa: E402
    KnowledgeConfig,
    KnowledgeEmbeddingConfig,
    KnowledgeQuestionGenerationConfig,
    KnowledgeRerankConfig,
)
from nanobot.knowledge.service import (  # noqa: E402
    get_knowledge_service,
    reset_knowledge_service,
)

PORT = 8802
OUT = Path(
    r"D:\studyspace\project\Zhixu\.ai-runtime-artifacts"
    r"\verifications\knowledge-e2e\doc2query-probe.json"
)

DOC = """# 生产运维手册

## 回滚

线上版本出问题时的回滚流程：先冻结流量，确认没有新的写入进入，再执行 kubectl rollout undo 恢复上一版本。
回滚完成后必须核对监控指标：错误率回到基线、核心接口 P99 延迟恢复正常、队列积压开始下降。
如果回滚后指标仍然异常，说明问题不在版本本身，应立即升级到值班负责人并保留现场日志与堆现场快照。
所有回滚操作都要在事件群里同步时间点、操作人和最终版本号，便于事后复盘追溯。

## 退款

订单完成后可在订单详情页申请开票，发票类型支持电子普通发票与增值税专用发票。
退款需要联系客服提交工单，客服核对订单与支付渠道后发起退款，一般三个工作日内到账。
如果超过三个工作日仍未到账，请提供订单号与支付流水号，由财务侧向支付渠道发起查询。
充值类虚拟商品一经使用不支持退款，未使用的部分可以在购买后七天内申请原路退回。

## 发布

发布流程：先在预发环境完成回归，再灰度放量 10%，观察 30 分钟无异常后全量发布。
灰度期间重点观察错误率、延迟、CPU 与内存水位，任一指标超过阈值立即停止放量并回滚。
全量发布后保持值班观察一小时，确认没有新的告警再关闭发布单。
"""


def _questions_for(content: str) -> list[str]:
    """按片段关键词返回问题，模拟真实模型的分块理解。"""
    if "回滚" in content:
        return [
            "线上版本出问题了怎么回滚？",
            "回滚之后怎么确认恢复成功？",
            "冻结流量和回滚的先后顺序是什么？",
        ]
    if "退款" in content:
        return ["订单完成以后怎么申请退款？", "退款一般多久能到账？"]
    return ["发布时灰度放量观察多久？", "发布流程有哪些步骤？"]


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server 协议
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        prompt = payload["messages"][0]["content"]
        content = prompt.rsplit("片段：", 1)[-1]
        questions = _questions_for(content)
        body = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "\n".join(
                                f"{index + 1}. {question}"
                                for index, question in enumerate(questions)
                            )
                        }
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    workdir = Path(tempfile.mkdtemp(prefix="zhixu-doc2query-"))
    config = KnowledgeConfig(
        database_path=str(workdir / "state.db"),
        vector_path=str(workdir / "chromadb"),
        embedding=KnowledgeEmbeddingConfig(
            backend="local",
            model="BAAI/bge-small-zh-v1.5",
            dimensions=512,
            batch_size=8,
            timeout_seconds=60.0,
        ),
        question_generation=KnowledgeQuestionGenerationConfig(
            enabled=True,
            base_url=f"http://127.0.0.1:{PORT}/v1",
            model="mock-chat",
            question_count=3,
        ),
        rerank=KnowledgeRerankConfig(
            enabled=True,
            backend="local",
            model="BAAI/bge-reranker-base",
            device="cpu",
        ),
    )
    reset_knowledge_service()
    service = get_knowledge_service(config)
    kb = service.create_kb(name="ops", workspace_id="ws1", description="运维手册")

    doc_path = workdir / "ops.md"
    doc_path.write_text(DOC, encoding="utf-8")
    outcome = service.add_file(doc_path, kb_id=kb.id, workspace_id="ws1")
    started = time.time()
    assert service.ingest_now(outcome.id), "ingest_now 应当认领成功"
    elapsed = time.time() - started

    document = service.get_document(outcome.id)
    assert document is not None and document.parse_status == "ready"
    chunks = service.list_chunks(outcome.id, limit=100)

    repo = service._repo  # noqa: SLF001 - 探针需要直读问题表
    question_hits = repo.search_questions_fts(
        "回滚", workspace_ids=["ws1"], kb_ids=[kb.id], limit=10
    )
    with service._db.connect() as conn:  # noqa: SLF001
        question_rows = conn.execute(
            "SELECT id, chunk_id, question FROM chunk_questions ORDER BY id"
        ).fetchall()

    keyword = service.search("怎么回滚", workspace_ids=["ws1"], kb_ids=[kb.id], limit=3)
    semantic = service.search(
        "线上版本出问题了怎么回滚",
        workspace_ids=["ws1"],
        kb_ids=[kb.id],
        mode="semantic",
        limit=3,
    )

    assert question_rows, "应当写入问题行"
    assert all(str(row["chunk_id"]) in {chunk.id for chunk in chunks} for row in question_rows)
    assert question_hits and question_hits[0].chunk_id in {chunk.id for chunk in chunks}
    assert keyword.hits and "回滚" in keyword.hits[0].content
    assert semantic.hits, "语义路应当命中问题向量"
    assert semantic.hits[0].chunk_id in {chunk.id for chunk in chunks}
    assert not semantic.hits[0].chunk_id.endswith("-q0"), "命中必须回填父块"
    assert semantic.rerank in ("ok", "threshold_degraded", "fallback_top1"), (
        f"本地 cross-encoder 应当生效，实际 {semantic.rerank}"
    )

    payload = {
        "elapsed_s": round(elapsed, 2),
        "chunks": len(chunks),
        "questions": len(question_rows),
        "question_ids": [str(row["id"]) for row in question_rows],
        "question_fts_hits": len(question_hits),
        "keyword": {
            "mode": keyword.mode_used,
            "routes": list(keyword.routes),
            "top_chunk": keyword.hits[0].chunk_id,
            "top_content": keyword.hits[0].content[:40],
        },
        "semantic": {
            "mode": semantic.mode_used,
            "routes": list(semantic.routes),
            "rerank": semantic.rerank,
            "candidates_in": semantic.candidates_in,
            "candidates_kept": semantic.candidates_kept,
            "top_chunk": semantic.hits[0].chunk_id,
            "top_score": round(semantic.hits[0].score, 4),
        },
        "document": {
            "status": document.parse_status,
            "vector_status": document.vector_status,
            "index_version": document.index_version,
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    server.shutdown()
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
