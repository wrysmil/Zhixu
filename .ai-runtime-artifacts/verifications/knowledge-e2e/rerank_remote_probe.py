"""验证 HttpReranker 的真实 HTTP 路径（对接本地 cohere 兼容服务）。

跑法：
    1) python rerank_service.py --port 8801 &
    2) python rerank_remote_probe.py
"""

from __future__ import annotations

import json
import sys
import time

sys.path.insert(0, r"D:\studyspace\project\Zhixu")

from nanobot.knowledge.rerank import HttpReranker  # noqa: E402

BASE = "http://127.0.0.1:8801/rerank"


def main() -> int:
    reranker = HttpReranker(
        base_url="http://127.0.0.1:8801",
        model="BAAI/bge-reranker-base",
        api_key="local-test-key",
        batch_size=8,
        max_concurrency=2,
        max_request_chars=400,
        timeout_seconds=60.0,
    )
    documents = [
        "生产回滚流程：先冻结流量，再回滚版本，最后验证监控指标。",
        "退款与发票：订单完成后可在订单详情页申请开票。",
        "发布流程：灰度放量 10%，观察 30 分钟无异常后全量。",
        "回滚预案：异常时执行 kubectl rollout undo 恢复上一版本。",
    ]
    started = time.time()
    results = reranker.rerank("线上版本出问题了怎么回滚", documents)
    elapsed = time.time() - started

    print(f"results={len(results)} elapsed={elapsed:.2f}s")
    for item in results:
        print(f"  index={item.index} score={item.relevance_score:.4f} text={documents[item.index][:24]}")

    assert len(results) == len(documents), "结果条数必须与候选一致"
    assert {item.index for item in results} == set(range(len(documents))), "index 必须回指全局下标"
    assert results == sorted(results, key=lambda item: (-item.relevance_score, item.index)), "结果必须按分降序"
    top = results[0]
    assert top.index in (0, 3), f"top1 应当是回滚相关块，实际 {top.index}"

    payload = {
        "documents": len(documents),
        "elapsed_s": round(elapsed, 2),
        "top_index": top.index,
        "top_score": round(top.relevance_score, 4),
        "order": [item.index for item in results],
    }
    out = r"D:\studyspace\project\Zhixu\.ai-runtime-artifacts\verifications\knowledge-e2e\rerank-remote-probe.json"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
