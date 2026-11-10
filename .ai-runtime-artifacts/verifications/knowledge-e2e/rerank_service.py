"""本地 cohere 兼容精排服务：验证 HttpReranker 的真实 HTTP 路径。

用途：`HttpReranker` 之前只有单测（httpx mock）覆盖，从没连过真实 socket。
本脚本起一个最小 HTTP 服务，实现 cohere 的 `POST /rerank` 契约，内部用本地
cross-encoder 打分，然后由 `HttpReranker` 客户端真实调用一遍。

用法：
    python rerank_service.py --port 8801 --model BAAI/bge-reranker-base
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_MODEL = None
_MODEL_NAME = ""
_LOCK = threading.Lock()


def _load_model(name: str):
    global _MODEL, _MODEL_NAME
    with _LOCK:
        if _MODEL is None:
            from sentence_transformers import CrossEncoder

            _MODEL = CrossEncoder(name, device="cpu")
            _MODEL_NAME = name
        return _MODEL


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A002
        print(f"[rerank-service] {fmt % args}", flush=True)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        if not self.path.rstrip("/").endswith("/rerank"):
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
            query = str(payload.get("query") or "")
            documents = [str(item) for item in (payload.get("documents") or [])]
        except Exception as exc:  # noqa: BLE001
            self.send_error(400, f"bad payload: {exc}")
            return
        if not documents:
            self._reply({"results": []})
            return

        model = _load_model(_MODEL_NAME)
        scores = model.predict([(query, doc) for doc in documents])
        results = [
            {"index": index, "relevance_score": float(score)}
            for index, score in enumerate(scores)
        ]
        results.sort(key=lambda item: -item["relevance_score"])
        self._reply({"results": results})

    def _reply(self, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8801)
    parser.add_argument("--model", default="BAAI/bge-reranker-base")
    args = parser.parse_args()

    global _MODEL_NAME
    _MODEL_NAME = args.model
    _load_model(args.model)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[rerank-service] listening on http://127.0.0.1:{args.port}/rerank", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
