"""MVP E2E：假 OpenAI 服务 + 真实 nanobot agent 两轮对话。

验证：
1. 第 1 轮 agent 调 memory_write 写项目规则，再调 experience_record 写经验；
2. 第 2 轮 system prompt 能读到 MEMORY.md 正文（而不是旧索引）；
3. SQLite episodes 里有 source=experience 的记录；
4. memories 表没有被这两个新工具写入同样内容（无双写）。

全程使用临时 HOME / workspace / config，不碰真实 ~/.nanobot。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REQUESTS: list[dict[str, Any]] = []
LOCK = threading.Lock()


def _system_text(body: dict[str, Any]) -> str:
    for msg in body.get("messages", []):
        if msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "\n".join(
                    str(part.get("text", "")) for part in content if isinstance(part, dict)
                )
    return ""


def _tool_result_texts(body: dict[str, Any]) -> list[str]:
    return [
        str(msg.get("content") or "")
        for msg in body.get("messages", [])
        if msg.get("role") == "tool"
    ]


def _tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"call_{uuid.uuid4().hex[:12]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def _decide(body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    system = _system_text(body)
    tool_results = _tool_result_texts(body)
    wrote_memory = any("已写入" in text for text in tool_results)
    recorded_experience = any("已记录经验" in text for text in tool_results)
    user_text = "\n".join(
        str(msg.get("content") or "")
        for msg in body.get("messages", [])
        if msg.get("role") == "user"
    )

    if "构建用 bun" in system and "用什么" in user_text:
        return "用 bun。", []
    if "构建用 bun" in system and not tool_results:
        return "用 bun。", []
    if not wrote_memory:
        return "", [
            _tool_call(
                "memory_write",
                {
                    "scope": "project",
                    "section": "项目约定",
                    "content": "- 构建用 bun",
                },
            )
        ]
    if not recorded_experience:
        return "", [
            _tool_call(
                "experience_record",
                {
                    "title": "bun 构建失败",
                    "situation": "bun run build 报模块缺失",
                    "action": "直接重跑",
                    "outcome": "failed",
                    "root_cause": "依赖未安装",
                    "solution": "bun install 后重跑",
                    "verification": "bun run build 通过",
                    "tags": ["bun", "build"],
                },
            )
        ]
    return "已完成。", []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        if not self.path.endswith("/chat/completions"):
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        with LOCK:
            REQUESTS.append(body)
        content, tool_calls = _decide(body)
        if body.get("stream"):
            self._stream(content, tool_calls, body.get("model", "fake-model"))
        else:
            self._json(content, tool_calls, body.get("model", "fake-model"))

    def _json(self, content: str, tool_calls: list[dict[str, Any]], model: str) -> None:
        message: dict[str, Any] = {"role": "assistant", "content": content or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        payload = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if tool_calls else "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, content: str, tool_calls: list[dict[str, Any]], model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def send(chunk: dict[str, Any]) -> None:
            self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        base = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
        }
        if tool_calls:
            call = tool_calls[0]
            send({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]})
            send(
                {
                    **base,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": call["id"],
                                        "type": "function",
                                        "function": call["function"],
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
            send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
        else:
            send({**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]})
            send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def _write_config(path: Path, workspace: Path, port: int) -> None:
    config = {
        "agents": {
            "defaults": {
                "workspace": str(workspace),
                "provider": "openai",
                "model": "fake-model",
                "modelPreset": None,
                "memoryEnabled": True,
                "memoryFileMode": True,
                "memorySearchBackend": "fts5",
                "memoryExtractIntervalMinutes": 9999,
                "dream": {"enabled": False},
                "consolidate": {"enabled": False},
            }
        },
        "providers": {
            "openai": {
                "apiKey": "test-key",
                "apiBase": f"http://127.0.0.1:{port}/v1",
                "apiType": "chat_completions",
            }
        },
        "channels": {"websocket": {"enabled": False}},
    }
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_agent(message: str, config: Path, workspace: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable,
        "-m",
        "nanobot",
        "agent",
        "-m",
        message,
        "-s",
        "mvp-e2e",
        "-c",
        str(config),
        "-w",
        str(workspace),
        "--no-markdown",
        "--no-logs",
    ]
    return subprocess.run(
        cmd,
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with tempfile.TemporaryDirectory(
        prefix="zhixu-mvp-e2e-", ignore_cleanup_errors=True
    ) as tmp:
        root = Path(tmp)
        home = root / "home"
        workspace = root / "workspace"
        home.mkdir()
        workspace.mkdir()
        config = root / "config.json"
        _write_config(config, workspace, port)

        first = _run_agent(
            "请记住：这个项目构建用 bun。然后记录一条经验：刚才构建失败是因为依赖没安装，bun install 后重跑通过。",
            config,
            workspace,
            home,
        )
        second = _run_agent("这个项目构建用什么？", config, workspace, home)

        print("--- turn 1 stdout ---")
        print(first.stdout[-2000:])
        print("--- turn 1 stderr tail ---")
        print(first.stderr[-1000:])
        print("--- turn 2 stdout ---")
        print(second.stdout[-1000:])

        failures: list[str] = []
        if first.returncode != 0:
            failures.append(f"turn1 exit={first.returncode}")
        if second.returncode != 0:
            failures.append(f"turn2 exit={second.returncode}")

        with LOCK:
            requests = list(REQUESTS)
        print(f"requests={len(requests)}")

        memory_md = list((home / ".nanobot" / "projects").glob("*/memory/MEMORY.md"))
        if not memory_md:
            failures.append("MEMORY.md not created")
        elif "构建用 bun" not in memory_md[0].read_text(encoding="utf-8"):
            failures.append("MEMORY.md missing rule")

        db_path = workspace / "memory" / "state.db"
        if not db_path.exists():
            failures.append("state.db not created")
        else:
            conn = sqlite3.connect(db_path)
            try:
                exp = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE source='experience' AND summary LIKE '%依赖未安装%'"
                ).fetchone()[0]
                mem = conn.execute(
                    "SELECT COUNT(*) FROM memories WHERE content LIKE '%构建用 bun%'"
                ).fetchone()[0]
            finally:
                conn.close()
            print(f"experience_rows={exp} memory_rows={mem}")
            if exp < 1:
                failures.append("experience row missing")
            if mem != 0:
                failures.append("long-term rule was also written to memories")

        last_system = _system_text(requests[-1]) if requests else ""
        print("--- last system prompt (memory part) ---")
        for line in last_system.splitlines():
            if "构建用" in line or "长期记忆" in line or "记忆索引" in line:
                print(line)
        if "构建用 bun" not in last_system:
            failures.append("second turn system prompt missing MEMORY.md content")
        if "# 记忆索引（目录，不是内容）" in last_system:
            failures.append("second turn still injected legacy index block")

        server.shutdown()
        if failures:
            print("FAIL:")
            for item in failures:
                print(" -", item)
            return 1
        print("PASS: memory_write -> next-turn injection; experience_record -> SQLite; no dual write")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
