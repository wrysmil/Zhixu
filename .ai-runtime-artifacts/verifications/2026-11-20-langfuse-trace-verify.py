"""End-to-end check: Zhixu OpenAI-compatible provider -> local Langfuse trace.

Starts a mock /v1/chat/completions server, drives OpenAICompatProvider with
LANGFUSE_* env vars pointing at the local self-hosted stack, then queries the
Langfuse public API to confirm trace/observation ingestion.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

BASE_URL = os.environ["LANGFUSE_BASE_URL"]
PK = os.environ["LANGFUSE_PUBLIC_KEY"]
SK = os.environ["LANGFUSE_SECRET_KEY"]
MOCK_PORT = 18099


class MockHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = {
            "id": "chatcmpl-mock-1",
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
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:
        pass


async def run_provider() -> None:
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    provider = OpenAICompatProvider(
        api_key="mock-key",
        api_base=f"http://127.0.0.1:{MOCK_PORT}/v1",
        default_model="mock-model",
        provider_name="custom",
    )
    resp = await provider.chat(
        messages=[{"role": "user", "content": "ping from zhixu-langfuse-verify"}],
        max_tokens=16,
        temperature=0,
    )
    print(f"provider content={resp.content!r} finish={resp.finish_reason}")
    if resp.content != "pong":
        raise SystemExit(f"FAIL: mock provider call returned {resp.content!r}")


def flush_langfuse() -> None:
    from langfuse import get_client

    get_client().flush()


def get_json(path: str) -> dict:
    resp = httpx.get(f"{BASE_URL}{path}", auth=(PK, SK), timeout=10)
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        asyncio.run(run_provider())
        flush_langfuse()
    finally:
        server.shutdown()

    deadline = time.time() + 60
    traces: list[dict] = []
    while time.time() < deadline:
        traces = get_json("/api/public/traces?limit=5").get("data", [])
        if traces:
            break
        time.sleep(3)

    if not traces:
        print("FAIL: no trace ingested within 60s")
        return 1

    print("traces:")
    for trace in traces:
        print(f"  id={trace.get('id')} name={trace.get('name')} ts={trace.get('timestamp')}")

    observations = get_json("/api/public/observations?limit=10").get("data", [])
    print(f"observations={len(observations)}")
    for obs in observations[:5]:
        print(
            f"  type={obs.get('type')} name={obs.get('name')} "
            f"model={obs.get('model')} traceId={obs.get('traceId')}"
        )

    generation = any(str(obs.get("type", "")).upper() == "GENERATION" for obs in observations)
    if not generation:
        print("FAIL: trace ingested but no GENERATION observation")
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
