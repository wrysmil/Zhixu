"""Spike: provider calls inside our turn span must nest into one Langfuse trace."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

BASE = os.environ["LANGFUSE_BASE_URL"]
PK = os.environ["LANGFUSE_PUBLIC_KEY"]
SK = os.environ["LANGFUSE_SECRET_KEY"]
MOCK_PORT = 18099


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = {
            "id": "chatcmpl-spike",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "pong"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        }
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


async def run() -> str:
    from nanobot.observability.langfuse_tracing import finish_turn_trace, start_turn_trace
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    handle = start_turn_trace(
        session_key="spike:sess-1",
        channel="websocket",
        sender_id="user-spike",
        turn_id="turn-spike",
        user_input="ping twice",
    )
    try:
        provider = OpenAICompatProvider(
            api_key="mock-key",
            api_base=f"http://127.0.0.1:{MOCK_PORT}/v1",
            default_model="mock-model",
            provider_name="custom",
        )
        for _ in range(2):
            response = await provider.chat(
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=8,
            )
            assert response.content == "pong"
    finally:
        finish_turn_trace(handle, output="pong pong", status="succeeded")

    from langfuse import get_client

    get_client().flush()
    return handle.trace_id or ""


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        trace_id = asyncio.run(run())
    finally:
        server.shutdown()

    if not trace_id:
        print("FAIL: no trace id")
        return 1

    deadline = time.time() + 60
    trace = None
    observations: list[dict] = []
    while time.time() < deadline:
        traces = httpx.get(
            f"{BASE}/api/public/traces?limit=10", auth=(PK, SK), timeout=10
        ).json()["data"]
        trace = next((item for item in traces if item["id"] == trace_id), None)
        if trace is not None:
            observations = httpx.get(
                f"{BASE}/api/public/observations",
                params={"traceId": trace_id, "limit": 20},
                auth=(PK, SK),
                timeout=10,
            ).json()["data"]
            if sum(1 for item in observations if item["type"] == "GENERATION") >= 2:
                break
        time.sleep(3)

    print("trace:", trace_id, "name:", trace.get("name") if trace else None)
    print("sessionId:", trace.get("sessionId") if trace else None)
    print("userId:", trace.get("userId") if trace else None)
    print("observations:", len(observations))
    generations = [item for item in observations if item["type"] == "GENERATION"]
    print("generations:", len(generations))

    ok = bool(
        trace
        and trace.get("name") == "zhixu.turn"
        and trace.get("sessionId") == "spike:sess-1"
        and trace.get("userId")
        and len(generations) == 2
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
