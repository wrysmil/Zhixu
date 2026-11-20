---
artifact: implementation-plan
route: superpowers:writing-plans
skills:
  - writing-plans
skills_evidence:
  - D:/studyspace/project/Zhixu/.claude/skills/writing-plans/SKILL.md
dispatch: n/a
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - harness-kit/artifact-templates/plan.harness-overlay.md
  - .ai-runtime-artifacts/specs/2026-11-13-trajectory-tracing-spec.md
  - .ai-runtime-artifacts/contracts/2026-11-13-trajectory-tracing-contract.md
  - docs/configuration.md
created_at: 2026-11-20
status: draft
approved: false
---

# 知序 × Langfuse 评估闭环实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让知序每一轮对话成为一条带 session/user 的 Langfuse trace，并打通 Scores、LLM-as-a-Judge、Human Annotation、Datasets 四条评估链路。

**Architecture:** 在 `AgentLoop._run_turn` 的 turn 边界开一个 Langfuse root span（fail-open），既有 `langfuse.openai` 包装会把每次模型调用自动挂成子 generation；trace id 经 outbound metadata → WebSocket → transcript 透传到前端，消息级反馈/入队/存数据集通过既有 WebUI mutation 通道回到网关，再由网关用 Langfuse 公共 API 写 score / annotation queue item / dataset item；数据集实验用独立脚本 + `DatasetClient.run_experiment` 跑知序 agent。

**Tech Stack:** Python 3.11 / asyncio / loguru / httpx；Langfuse Python SDK 3.15（可选依赖，已有 extra）；Langfuse Server v3（本地 127.0.0.1:3000）；React 18 + TypeScript + Vite；pytest / Vitest。

---

## 0. 现状与证据

- 现有接入只有 OpenAI 客户端替换：`nanobot/providers/openai_compat_provider.py:605-624`，每次模型调用产生一条独立 trace（`OpenAI-generation`），无 turn 级 trace、无 session/user、无 trace id 回传。
- 本地 Langfuse 已部署：`docker/langfuse/docker-compose.yml`，服务端 3.174.1；SDK 3.15.0 已装（`.venv`）。
- turn 边界：`nanobot/agent/loop.py:2943` `_run_turn`，已有 `try/finally` 与 trajectory 收尾（`:3000-3007`），是最小侵入的接入点。
- 已有 turn 元数据：`TurnContext`（`loop.py:186-231`），含 `session_key`、`turn_id`、`original_user_text`、`request_context`、`final_content`。
- 既有 hook 体系：`nanobot/agent/hook.py:66-151`；但 trace 需覆盖 hook 之外的全 turn，故选 `_run_turn` 直接包裹。
- 出站组装：`_prepare_outbound`（`loop.py:3116-3141`）→ `_assemble_outbound`（`:2518-2548`），可在此写入 metadata。
- WebSocket 出站字段白名单：`nanobot/channels/websocket/runtime.py:1259-1301`。
- transcript 投影白名单：`nanobot/webui/transcript.py:2462-2470`（assistant 分支）。
- WebUI mutation 通道：`ws_http.py:161-249`（allowlist）、`:548-565`、`:513-546`、`:1575-1576`（路由示例）、`:1806-1822`（handler 示例）；鉴权 `check_api_token`（`:484-487`）。
- 前端消息结构与动作：`webui/src/lib/types.ts:75-138`（UIMessage）、`:1407-1428`（message 帧）；`webui/src/components/MessageBubble.tsx:476-483, 536-599`；无点赞/点踩。
- i18n：10 个 locale，`webui/src/i18n/config.ts:3-14`；parity 测试 `webui/src/tests/i18n-dock-parity.test.ts`。
- SDK 能力已核实：`start_as_current_span`、`update_current_trace`、`get_current_trace_id`、`create_score`、`api.annotation_queues.create_queue/create_queue_item`、`create_dataset_item`、`DatasetClient.run_experiment`。

## 1. 目标（验收口径）

| 能力 | Definition of Done |
| --- | --- |
| Turn trace | 一轮对话 = 1 条 trace（name=`zhixu.turn`），含该轮全部 generation 子节点；`session_id`=知序 session key；`user_id`=哈希；trace 级 input/output 为当轮用户消息与最终回答 |
| Scores | 助手消息可点赞/点踩，网关写入 `user_feedback`（BOOLEAN，source=API）；同一 trace 重复提交为幂等覆盖；Langfuse「评分」页可见 |
| LLM-as-a-Judge | Langfuse 中配置的 evaluator 能对 `zhixu.turn` 自动打分，产生 source=EVAL 的 score；验证脚本能轮询到该 score |
| Human Annotation | 助教/用户可一键把某条 trace 加入 `zhixu-review` 标注队列；UI 中可按 `human_quality`（NUMERIC 1–5）与 `human_failure_type`（CATEGORICAL）打分 |
| Datasets | 可把一轮对话存为 `zhixu-evals` 数据集条目（input/expected_output/source_trace_id）；`scripts/langfuse_experiment.py` 能在数据集上跑知序并生成 dataset run + 逐条 score |

## 2. 非目标（v1）

- 不做采样率、离线回放、跨 workspace 独立 Langfuse 项目。
- 不做自动脱敏（仅用户 id 哈希；prompt/output 原样上报，文档明示敏感数据边界）。
- 不把 Langfuse 原生 evaluator 搬进知序；LLM 评审用 Langfuse 侧配置。
- 不为子 agent 单独开 trace（其调用会作为当前 task 上下文内的子 span，若 SDK 上下文隔离则独立成 trace，v1 接受）。
- 不新增依赖：网关侧只用 httpx 调公共 API；agent 侧复用可选 `langfuse` extra。

## 3. 架构

```
WebUI（消息操作：赞/踩、入标注、存数据集）
   │  authenticated mutation (WS /api 通道)
   ▼
Gateway (ws_http / inbound_commands)
   │  httpx + LANGFUSE_PUBLIC/SECRET/BASE_URL
   ├──► POST /api/public/scores                (user_feedback)
   ├──► POST /api/public/annotation-queues/{id}/items
   └──► POST /api/public/dataset-items

AgentLoop._run_turn
   │  start_turn_trace()  —— SDK start_as_current_span(name="zhixu.turn")
   │       ├─ provider 调用（langfuse.openai 包装）→ 子 generation
   │       └─ update_current_trace(session_id, user_id, tags)
   │  finish_turn_trace(output=final_content, status)
   └─ trace_id → TurnContext → outbound.metadata → WS frame → transcript → UIMessage

scripts/langfuse_experiment.py
   └─ DatasetClient.run_experiment(task=知序 agent, evaluators=[exact_match, ...])
```

## 4. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | 无 | 已有；缺失时 tracing 全链路 no-op |
| `LANGFUSE_USER_SALT` | `zhixu` | user id 哈希盐，变更会改变 user 分组 |
| `LANGFUSE_ANNOTATION_QUEUE` | `zhixu-review` | 标注队列名（按名解析 id，进程内缓存） |
| `LANGFUSE_DATASET` | `zhixu-evals` | 默认数据集名 |
| `LANGFUSE_FEEDBACK_SCORE` | `user_feedback` | 反馈 score 名 |

## 5. 文件清单

**新建**

- `nanobot/observability/__init__.py`
- `nanobot/observability/langfuse_tracing.py` — turn span 生命周期（fail-open）
- `nanobot/observability/langfuse_ingest.py` — score / annotation / dataset HTTP 客户端（fail-open）
- `scripts/langfuse_bootstrap.py` — 幂等创建评分配置、标注队列、数据集
- `scripts/langfuse_experiment.py` — 数据集实验运行器
- `scripts/langfuse_wait_for_score.py` — 轮询校验脚本（验收用）
- `tests/observability/test_langfuse_tracing.py`
- `tests/observability/test_langfuse_ingest.py`
- `tests/webui/test_message_feedback.py`
- `webui/src/tests/message-feedback.test.tsx`

**修改**

- `nanobot/agent/loop.py` — `TurnContext` 新字段；`_run_turn` 包裹；`_prepare_outbound` 写 trace_id
- `nanobot/channels/websocket/runtime.py` — 出站帧带 `trace_id`
- `nanobot/webui/transcript.py` — 投影保留 `traceId`
- `nanobot/webui/ws_http.py` — mutation allowlist + 三个动作路由（feedback / annotation / dataset）
- `nanobot/webui/inbound_commands.py` — 三个 mutation handler
- `webui/src/lib/types.ts` — `UIMessage.traceId`、message 帧 `trace_id`
- `webui/src/hooks/useZhixuStream.ts` — 接收 `trace_id` 写入消息
- `webui/src/lib/api.ts` — `sendMessageFeedback` / `addToAnnotationQueue` / `saveToDataset`
- `webui/src/components/MessageBubble.tsx` — 助手消息动作按钮
- `webui/src/i18n/locales/<10 locales>/common.json` — 新文案
- `docs/configuration.md`、`docs/guides/configure-langfuse-observability.md`、`docker/langfuse/README.md`

## 6. 执行顺序

`Task 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10 → 11`，顺序执行（`dispatch: n/a`）。Task 5 与 7 是后续三条评估链路的公共基础。

---

### Task 1: 先验证 context 嵌套（spike）

**Files:**
- Create: `.ai-runtime-artifacts/verifications/2026-11-20-langfuse-nesting-spike.py`

- [ ] **Step 1: 写验证脚本**

```python
"""在 langfuse span 内调用知序 provider，验证 generation 会挂到同一 trace。"""
import asyncio, json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx

BASE, PK, SK = os.environ["LANGFUSE_BASE_URL"], os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        body = {"id": "c1", "object": "chat.completion", "created": int(time.time()), "model": "mock",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "pong"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}}
        data = json.dumps(body).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def log_message(self, *a): pass

async def main():
    from langfuse import get_client
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider
    lf = get_client()
    provider = OpenAICompatProvider(api_key="k", api_base="http://127.0.0.1:18099/v1", default_model="mock", provider_name="custom")
    with lf.start_as_current_span(name="zhixu.turn", input={"message": "ping"}):
        lf.update_current_trace(session_id="sess-spike", user_id="user-spike")
        trace_id = lf.get_current_trace_id()
        for _ in range(2):
            await provider.chat(messages=[{"role": "user", "content": "ping"}], max_tokens=8)
    lf.flush()
    return trace_id

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 18099), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    trace_id = asyncio.run(main())
    traces = httpx.get(f"{BASE}/api/public/traces?limit=5", auth=(PK, SK), timeout=10).json()["data"]
    mine = [t for t in traces if t["id"] == trace_id]
    assert mine, traces
    obs = httpx.get(f"{BASE}/api/public/observations?traceId={trace_id}&limit=20", auth=(PK, SK), timeout=10).json()["data"]
    gens = [o for o in obs if o["type"] == "GENERATION"]
    print("trace:", trace_id, "observations:", len(obs), "generations:", len(gens))
    assert len(gens) == 2, obs
    print("PASS")
    server.shutdown()
```

- [ ] **Step 2: 运行并确认**

Run:
```bash
LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
  .venv/Scripts/python.exe .ai-runtime-artifacts/verifications/2026-11-20-langfuse-nesting-spike.py
```
Expected: `trace: <id> observations: 2 generations: 2` 且 `PASS`。若 generation 未嵌套，停止后续实现并改走 `trace_context` 手工传参方案。

- [ ] **Step 3: 提交验证脚本**

```bash
git add .ai-runtime-artifacts/verifications/2026-11-20-langfuse-nesting-spike.py
git commit -m "test(observability): 验证 langfuse span 内 provider 调用可嵌套"
```

---

### Task 2: turn tracer 核心模块

**Files:**
- Create: `nanobot/observability/__init__.py`（空文件）
- Create: `nanobot/observability/langfuse_tracing.py`
- Test: `tests/observability/test_langfuse_tracing.py`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from nanobot.observability import langfuse_tracing as lt

class FakeSpan:
    def __init__(self): self.updates = []
    def update(self, **kw): self.updates.append(kw)

class FakeManager:
    def __init__(self): self.span = FakeSpan(); self.exited = None
    def __enter__(self): return self.span
    def __exit__(self, *exc): self.exited = exc; return False

class FakeClient:
    def __init__(self): self.manager = FakeManager(); self.started = None; self.trace_updates = {}
    def start_as_current_span(self, **kw): self.started = kw; return self.manager
    def update_current_trace(self, **kw): self.trace_updates.update(kw)
    def get_current_trace_id(self): return "a" * 32

def test_noop_without_credentials(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    handle = lt.start_turn_trace(session_key="s", channel="cli", sender_id="u", turn_id="t", user_input="hi")
    assert handle.trace_id is None
    lt.finish_turn_trace(handle, output="ok", status="succeeded")

def test_trace_lifecycle(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    fake = FakeClient()
    lt.set_client_factory(lambda: fake)
    try:
        handle = lt.start_turn_trace(session_key="sess", channel="websocket", sender_id="u1", turn_id="t1", user_input="hello")
        assert handle.trace_id == "a" * 32
        assert fake.started["name"] == "zhixu.turn"
        assert fake.trace_updates["session_id"] == "sess"
        assert fake.trace_updates["user_id"] == lt.hashed_user_id("websocket", "u1")
        lt.finish_turn_trace(handle, output="answer", status="failed", error="boom")
        assert fake.manager.exited is not None
        assert fake.manager.span.updates[-1]["level"] == "ERROR"
        assert fake.manager.span.updates[-1]["output"] == {"message": "answer"}
    finally:
        lt.set_client_factory(None)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/observability/test_langfuse_tracing.py -v`
Expected: FAIL（`ModuleNotFoundError: nanobot.observability`）。

- [ ] **Step 3: 实现模块**

`nanobot/observability/langfuse_tracing.py`:

```python
"""Turn-level Langfuse tracing. Fail-open: any error only disables tracing."""
from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

TRACE_NAME = "zhixu.turn"
ClientFactory = Callable[[], Any]
_client_factory: ClientFactory | None = None


def set_client_factory(factory: ClientFactory | None) -> None:
    global _client_factory
    _client_factory = factory


def tracing_enabled() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


def _get_client() -> Any | None:
    if not tracing_enabled():
        return None
    try:
        if _client_factory is not None:
            return _client_factory()
        from langfuse import get_client

        return get_client()
    except Exception:
        logger.exception("Langfuse init failed; turn tracing disabled")
        return None


def hashed_user_id(channel: str, sender_id: str) -> str:
    salt = os.environ.get("LANGFUSE_USER_SALT", "zhixu")
    return hashlib.sha256(f"{channel}:{sender_id}:{salt}".encode()).hexdigest()[:16]


@dataclass(slots=True)
class TurnTraceHandle:
    trace_id: str | None = None
    _client: Any = None
    _manager: Any = None
    _span: Any = None
    closed: bool = field(default=False)


def start_turn_trace(
    *,
    session_key: str,
    channel: str,
    sender_id: str,
    turn_id: str | None,
    user_input: str | None,
) -> TurnTraceHandle:
    client = _get_client()
    if client is None:
        return TurnTraceHandle()
    manager = None
    try:
        manager = client.start_as_current_span(
            name=TRACE_NAME,
            input={"message": (user_input or "")[:20000]},
            metadata={"channel": channel, "turn_id": turn_id},
        )
        span = manager.__enter__()
        client.update_current_trace(
            name=TRACE_NAME,
            session_id=session_key,
            user_id=hashed_user_id(channel, sender_id),
            tags=["zhixu", channel],
        )
        return TurnTraceHandle(
            trace_id=client.get_current_trace_id(),
            _client=client,
            _manager=manager,
            _span=span,
        )
    except Exception:
        logger.exception("Langfuse turn trace start failed")
        try:
            if manager is not None:
                manager.__exit__(None, None, None)
        except Exception:
            pass
        return TurnTraceHandle()


def finish_turn_trace(
    handle: TurnTraceHandle,
    *,
    output: str | None,
    status: str,
    error: str | None = None,
) -> None:
    if handle.closed or handle._manager is None:
        return
    handle.closed = True
    try:
        if handle._span is not None:
            handle._span.update(
                output={"message": (output or "")[:20000]},
                metadata={"status": status},
                level="ERROR" if status in {"failed", "cancelled"} else "DEFAULT",
                status_message=error,
            )
    except Exception:
        logger.exception("Langfuse turn span update failed")
    try:
        handle._manager.__exit__(None, None, None)
    except Exception:
        logger.exception("Langfuse turn span close failed")
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/observability/test_langfuse_tracing.py -v`
Expected: 2 passed。

- [ ] **Step 5: 提交**

```bash
git add nanobot/observability tests/observability
git commit -m "feat(observability): 增加 fail-open 的 Langfuse turn tracer"
```

---

### Task 3: 接入 turn 生命周期与出站元数据

**Files:**
- Modify: `nanobot/agent/loop.py:186-231`（`TurnContext` 新字段）
- Modify: `nanobot/agent/loop.py:2943-3009`（`_run_turn` 开/闭 span）
- Modify: `nanobot/agent/loop.py:2518-2548`（`_assemble_outbound` 增加 `trace_id` 入参）
- Modify: `nanobot/agent/loop.py:3116-3144`（`_prepare_outbound` 传参）
- Test: `tests/agent/test_langfuse_outbound_metadata.py`

- [ ] **Step 1: 写失败测试（出站 metadata）**

```python
from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage


def test_assemble_outbound_carries_trace_id():
    loop = object.__new__(AgentLoop)
    msg = InboundMessage(channel="websocket", chat_id="c1", sender_id="u1", content="hi")
    out = loop._assemble_outbound(msg, "answer", "stop", False, trace_id="b" * 32)
    assert out is not None
    assert out.metadata["trace_id"] == "b" * 32
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/agent/test_langfuse_outbound_metadata.py -v`
Expected: FAIL（`TurnContext` 无 `langfuse_trace_id`）。

- [ ] **Step 3: 修改 TurnContext 与 `_run_turn`**

`TurnContext` 在 `outbound` 字段附近新增：

```python
    langfuse_trace_id: str | None = None
```

`_run_turn` 顶部（trajectory 初始化之后）新增：

```python
        from nanobot.observability.langfuse_tracing import finish_turn_trace, start_turn_trace

        langfuse_trace = start_turn_trace(
            session_key=ctx.session_key,
            channel=ctx.delivery.route.channel,
            sender_id=(
                ctx.request_context.sender_id
                if ctx.request_context is not None
                else "unknown"
            ),
            turn_id=ctx.turn_id,
            user_input=ctx.original_user_text,
        )
        ctx.langfuse_trace_id = langfuse_trace.trace_id
```

在 `_run_turn` 既有 `finally` 块末尾（trajectory `finish` 之后）新增：

```python
            finish_turn_trace(
                langfuse_trace,
                output=ctx.final_content,
                status=trace_status,
                error=ctx.failure_error_kind,
            )
```

`_assemble_outbound` 签名末尾新增 `trace_id: str | None = None`，并在 `meta` 组装处（`meta["latency_ms"]` 附近）新增：

```python
        if trace_id:
            meta["trace_id"] = trace_id
```

`_prepare_outbound` 调用 `_assemble_outbound(...)` 时传入：

```python
            trace_id=ctx.langfuse_trace_id,
```

- [ ] **Step 4: 运行测试**

Run: `pytest tests/agent/test_langfuse_outbound_metadata.py tests/observability -v`
Expected: passed。

- [ ] **Step 5: 端到端冒烟（复用 Task 1 脚本思路）**

用真实 Langfuse + mock provider 走 `Nanobot.run(ephemeral=True)` 一轮，查询 `/api/public/traces`：应出现 1 条 `zhixu.turn`，`sessionId` 为传入 key，且包含 ≥1 个 GENERATION。

- [ ] **Step 6: 提交**

```bash
git add nanobot/agent/loop.py tests/agent/test_langfuse_outbound_metadata.py
git commit -m "feat(agent): 每轮对话生成一条 Langfuse turn trace"
```

---

### Task 4: trace_id 透传到 WebUI（实时 + 刷新后）

**Files:**
- Modify: `nanobot/channels/websocket/runtime.py:1259-1301`
- Modify: `nanobot/webui/transcript.py:2462-2470`
- Modify: `webui/src/lib/types.ts:75-138`、`:1407-1428`
- Modify: `webui/src/hooks/useZhixuStream.ts`（`ev.event === "message"` 分支，约 `:841`、`:901`）
- Test: `tests/channels/websocket/test_trace_id_frame.py`（按现有 ws 测试目录命名）
- Test: `tests/webui/test_transcript_trace_id.py`

- [ ] **Step 1: 写 Python 失败测试**

```python
def test_projected_message_includes_trace_id():
    rec = {"event": "message", "text": "ok", "turn_id": "t1", "turn_phase": "answer", "trace_id": "c" * 32}
    messages = project_thread([rec])
    assistant = [m for m in messages if m["role"] == "assistant"][-1]
    assert assistant["traceId"] == "c" * 32
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/webui/test_transcript_trace_id.py -v`
Expected: FAIL（`KeyError: 'traceId'`）。

- [ ] **Step 3: 后端透传**

`runtime.py` `send_projected_message` 在 `turn_id` 之后新增：

```python
        trace_id = msg.metadata.get("trace_id")
        if isinstance(trace_id, str) and trace_id:
            payload["trace_id"] = trace_id
```

`transcript.py` assistant 分支（`extra.update(_turn_fields(...))` 之前）新增：

```python
            trace_id = rec.get("trace_id")
            if isinstance(trace_id, str) and trace_id:
                extra["traceId"] = trace_id
```

- [ ] **Step 4: 前端类型与解析**

`types.ts`：

```ts
// UIMessage
  traceId?: string;

// message 事件
      trace_id?: string;
```

`useZhixuStream.ts` 在创建/更新 assistant 消息处带上：

```ts
...(ev.trace_id ? { traceId: ev.trace_id } : {}),
```

- [ ] **Step 5: 测试**

Run:
```bash
pytest tests/webui/test_transcript_trace_id.py tests/channels/websocket -v
cd webui && bun run test
```
Expected: passed（前端用例在 Task 6 补齐，此步先保证现有套件不回归）。

- [ ] **Step 6: 提交**

```bash
git add nanobot/channels/websocket/runtime.py nanobot/webui/transcript.py webui/src/lib/types.ts webui/src/hooks/useZhixuStream.ts tests/webui/test_transcript_trace_id.py
git commit -m "feat(webui): 助手消息携带 Langfuse trace id 并持久化"
```

---

### Task 5: 评分与入队/数据集的后端能力

**Files:**
- Create: `nanobot/observability/langfuse_ingest.py`
- Modify: `nanobot/webui/inbound_commands.py`（mutation handlers，参照 `:1620-1720` 现有动作分发）
- Modify: `nanobot/webui/ws_http.py:161-249`（allowlist）、`:548-565`、`:1575-1576`、`:1806-1822`（HTTP 路由与 handler）
- Test: `tests/observability/test_langfuse_ingest.py`
- Test: `tests/webui/test_message_feedback.py`

- [ ] **Step 1: 写失败测试（ingest 客户端）**

```python
import pytest
from nanobot.observability import langfuse_ingest as ing

class Resp:
    status_code = 200
    def raise_for_status(self): pass

@pytest.mark.asyncio
async def test_create_feedback_score(monkeypatch):
    captured = {}
    class FakeClient:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, json=None, auth=None):
            captured.update(url=url, json=json, auth=auth); return Resp()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "http://lf")
    monkeypatch.setattr(ing.httpx, "AsyncClient", FakeClient)
    ok = await ing.create_feedback_score(trace_id="d" * 32, helpful=True)
    assert ok is True
    assert captured["url"] == "http://lf/api/public/scores"
    assert captured["json"] == {
        "id": "zhixu-feedback-" + "d" * 32,
        "traceId": "d" * 32,
        "name": "user_feedback",
        "value": 1,
        "dataType": "BOOLEAN",
    }
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/observability/test_langfuse_ingest.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 3: 实现 `langfuse_ingest.py`**

```python
"""Fail-open HTTP client for Langfuse public ingestion APIs."""
from __future__ import annotations

import os
from typing import Any

import httpx
from loguru import logger


def _base_url() -> str | None:
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    return os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").rstrip("/")


def _auth() -> tuple[str, str]:
    return os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]


async def _post(path: str, payload: dict[str, Any]) -> bool:
    base = _base_url()
    if base is None:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{base}{path}", json=payload, auth=_auth())
            resp.raise_for_status()
        return True
    except Exception:
        logger.exception("Langfuse ingestion failed: {}", path)
        return False


async def create_feedback_score(*, trace_id: str, helpful: bool, comment: str | None = None) -> bool:
    payload: dict[str, Any] = {
        "id": f"zhixu-feedback-{trace_id}",
        "traceId": trace_id,
        "name": os.environ.get("LANGFUSE_FEEDBACK_SCORE", "user_feedback"),
        "value": 1 if helpful else 0,
        "dataType": "BOOLEAN",
    }
    if comment:
        payload["comment"] = comment[:500]
    return await _post("/api/public/scores", payload)


async def add_trace_to_queue(*, queue_id: str, trace_id: str) -> bool:
    return await _post(
        f"/api/public/annotation-queues/{queue_id}/items",
        {"objectId": trace_id, "objectType": "TRACE", "status": "PENDING"},
    )


async def create_dataset_item(
    *,
    dataset_name: str,
    trace_id: str,
    input_text: str,
    expected_output: str,
    metadata: dict[str, Any] | None = None,
) -> bool:
    return await _post(
        "/api/public/dataset-items",
        {
            "datasetName": dataset_name,
            "input": input_text,
            "expectedOutput": expected_output,
            "sourceTraceId": trace_id,
            "metadata": metadata or {},
        },
    )


async def fetch_trace_io(trace_id: str) -> tuple[str, str] | None:
    """Return (input message, output message) recorded on the trace."""
    base = _base_url()
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/public/traces/{trace_id}", auth=_auth())
            resp.raise_for_status()
            data = resp.json()
        raw_in = data.get("input")
        raw_out = data.get("output")
        text_in = raw_in.get("message") if isinstance(raw_in, dict) else raw_in
        text_out = raw_out.get("message") if isinstance(raw_out, dict) else raw_out
        return (str(text_in or ""), str(text_out or ""))
    except Exception:
        logger.exception("Langfuse trace lookup failed")
        return None


async def resolve_queue_id(name: str) -> str | None:
    base = _base_url()
    if base is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{base}/api/public/annotation-queues", params={"name": name}, auth=_auth())
            resp.raise_for_status()
            for item in resp.json().get("data", []):
                if item.get("name") == name:
                    return item.get("id")
    except Exception:
        logger.exception("Langfuse annotation queue lookup failed")
    return None
```

- [ ] **Step 4: 接入 mutation 动作**

`ws_http.py` allowlist（`_WEBUI_MUTATION_PATHS`）新增：

```python
    "feedback.submit": "/api/webui/feedback",
    "annotation.add": "/api/webui/annotation",
    "dataset.add": "/api/webui/dataset-item",
```

HTTP 路由区新增：

```python
        if got == "/api/webui/feedback":
            return await self._handle_webui_feedback(request)
        if got == "/api/webui/annotation":
            return await self._handle_webui_annotation(request)
        if got == "/api/webui/dataset-item":
            return await self._handle_webui_dataset_item(request)
```

`inbound_commands.py`（与 `sidebar.update` 同分发处）新增：

```python
        if action == "feedback.submit":
            return await self._feedback(payload)
        if action == "annotation.add":
            return await self._annotation(payload)
        if action == "dataset.add":
            return await self._dataset(payload)
```

Handler（放在 `ws_http.py` handler 区，`_mutation_payload` 与 `check_api_token` 语义与 sidebar 示例一致）：

```python
    async def _handle_webui_feedback(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = _mutation_payload(request) or {}
        trace_id = payload.get("trace_id")
        helpful = payload.get("helpful")
        comment = payload.get("comment")
        if not isinstance(trace_id, str) or len(trace_id) != 32:
            return _http_error(400, "invalid trace_id")
        if not isinstance(helpful, bool):
            return _http_error(400, "helpful must be boolean")
        if comment is not None and not isinstance(comment, str):
            return _http_error(400, "comment must be string")
        ok = await create_feedback_score(trace_id=trace_id, helpful=helpful, comment=comment)
        return _http_json_response({"ok": ok})
```

annotation / dataset handler 同构：annotation 校验 `trace_id`，解析 `LANGFUSE_ANNOTATION_QUEUE` 队列 id 后 `add_trace_to_queue`；dataset 接收 `trace_id/input_text/expected_output`，非空校验后调用 `create_dataset_item`。

- [ ] **Step 5: 测试**

Run:
```bash
pytest tests/observability/test_langfuse_ingest.py tests/webui/test_message_feedback.py -v
```
Expected: passed（handler 用 monkeypatch 替换 `create_feedback_score` 断言参数）。

- [ ] **Step 6: 提交**

```bash
git add nanobot/observability/langfuse_ingest.py nanobot/webui/ws_http.py nanobot/webui/inbound_commands.py tests/observability/test_langfuse_ingest.py tests/webui/test_message_feedback.py
git commit -m "feat(webui): 反馈/标注/数据集 mutation 接入 Langfuse API"
```

---

### Task 6: 助手消息点赞/点踩 UI

**Files:**
- Modify: `webui/src/components/MessageBubble.tsx:476-483, 536-599`（通过 `useClient()` 直接发 mutation）
- Modify: `webui/src/i18n/locales/<code>/common.json`（10 个）
- Test: `webui/src/tests/message-feedback.test.tsx`

- [ ] **Step 1: 写失败测试**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { MessageBubble } from "../components/MessageBubble";

const requestMutation = vi.fn().mockResolvedValue({ ok: true });
vi.mock("@/providers/ClientProvider", () => ({
  useClient: () => ({ client: { requestMutation } }),
}));

it("assistant message with trace id sends feedback", async () => {
  render(
    <MessageBubble
      message={{ id: "m1", role: "assistant", content: "hi", traceId: "e".repeat(32), createdAt: 0 }}
      onForkFromHere={() => {}}
    />,
  );
  fireEvent.click(screen.getByLabelText("有帮助"));
  expect(requestMutation).toHaveBeenCalledWith("feedback.submit", {
    trace_id: "e".repeat(32),
    helpful: true,
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd webui && bun run test -- message-feedback`
Expected: FAIL（组件无按钮 / API 不存在）。

- [ ] **Step 3: 实现 API 与按钮**

`MessageBubble.tsx` 顶部引入 `useClient()`（`@/providers/ClientProvider`，`ThreadShell` 已在用同一 hook），在 footer 动作区（`:536-599`）新增，仅当 `message.role === "assistant" && message.traceId && !message.isStreaming`：

```tsx
<div className="flex items-center gap-1">
  <button
    type="button"
    aria-label={t("message.feedback.helpful")}
    onClick={() => void submitFeedback(true)}
  >
    <ThumbsUp className="h-3.5 w-3.5" />
  </button>
  <button
    type="button"
    aria-label={t("message.feedback.unhelpful")}
    onClick={() => void submitFeedback(false)}
  >
    <ThumbsDown className="h-3.5 w-3.5" />
  </button>
</div>
```

`submitFeedback` 用本地 state 记录 `sent`，调用 `await client.requestMutation("feedback.submit", { trace_id: message.traceId, helpful })`，失败时 toast `message.feedback.failed`，成功时 `message.feedback.saved`。

- [ ] **Step 4: 补 i18n（10 个 locale）**

键：`message.feedback.helpful|unhelpful|saved|failed|annotation|dataset`。

| locale | helpful | unhelpful | saved | failed |
| --- | --- | --- | --- | --- |
| en | Helpful | Not helpful | Feedback saved | Feedback failed |
| zh-CN | 有帮助 | 没帮助 | 已记录反馈 | 反馈失败 |
| zh-TW | 有幫助 | 沒幫助 | 已記錄回饋 | 回饋失敗 |
| ja | 役に立った | 役に立たなかった | フィードバックを記録しました | フィードバックに失敗しました |
| ko | 도움됨 | 도움 안 됨 | 피드백이 기록되었습니다 | 피드백 실패 |
| fr | Utile | Pas utile | Retour enregistré | Échec du retour |
| es | Útil | Poco útil | Comentario guardado | Error al guardar el comentario |
| pt-BR | Útil | Não útil | Feedback registrado | Falha ao registrar feedback |
| vi | Hữu ích | Không hữu ích | Đã ghi nhận phản hồi | Gửi phản hồi thất bại |
| id | Membantu | Tidak membantu | Umpan balik tersimpan | Gagal menyimpan umpan balik |

- [ ] **Step 5: 运行测试**

Run: `cd webui && bun run test -- message-feedback i18n`
Expected: passed（parity 测试要求 10 个 locale 键一致）。

- [ ] **Step 6: 提交**

```bash
git add webui/src/components/MessageBubble.tsx webui/src/i18n webui/src/tests/message-feedback.test.tsx
git commit -m "feat(webui): 助手消息支持点赞/点踩并上报 Langfuse score"
```

---

### Task 7: 评分配置 / 标注队列 / 数据集引导脚本

**Files:**
- Create: `scripts/langfuse_bootstrap.py`
- Create: `tests/observability/test_langfuse_bootstrap_plan.py`

- [ ] **Step 1: 实现幂等 bootstrap**

```python
"""Idempotently create Zhixu score configs, annotation queue, and dataset."""
from __future__ import annotations

import os
import sys

import httpx

BASE = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
AUTH = (os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
QUEUE = os.environ.get("LANGFUSE_ANNOTATION_QUEUE", "zhixu-review")
DATASET = os.environ.get("LANGFUSE_DATASET", "zhixu-evals")

SCORE_CONFIGS = [
    {"name": "user_feedback", "dataType": "BOOLEAN"},
    {"name": "judge_quality", "dataType": "NUMERIC", "minValue": 0, "maxValue": 1},
    {"name": "human_quality", "dataType": "NUMERIC", "minValue": 1, "maxValue": 5},
    {"name": "human_failure_type", "dataType": "CATEGORICAL",
     "categories": [{"label": "retrieval"}, {"label": "model"}, {"label": "tool"}, {"label": "other"}]},
]


def get(path: str, **params) -> httpx.Response:
    return httpx.get(f"{BASE}{path}", params=params, auth=AUTH, timeout=10)


def post(path: str, body: dict) -> httpx.Response:
    return httpx.post(f"{BASE}{path}", json=body, auth=AUTH, timeout=10)


def ensure_score_config(cfg: dict) -> None:
    existing = get("/api/public/score-configs", name=cfg["name"], limit=50).json().get("data", [])
    if any(item.get("name") == cfg["name"] for item in existing):
        print("score-config exists:", cfg["name"])
        return
    r = post("/api/public/score-configs", cfg)
    r.raise_for_status()
    print("score-config created:", cfg["name"])


def ensure_queue() -> str:
    queues = get("/api/public/annotation-queues", name=QUEUE, limit=50).json().get("data", [])
    for item in queues:
        if item.get("name") == QUEUE:
            print("queue exists:", item["id"])
            return item["id"]
    r = post("/api/public/annotation-queues", {"name": QUEUE, "description": "Zhixu 人工复核队列"})
    r.raise_for_status()
    print("queue created:", r.json()["id"])
    return r.json()["id"]


def ensure_dataset() -> None:
    if get(f"/api/public/datasets/{DATASET}").status_code == 200:
        print("dataset exists:", DATASET)
        return
    r = post("/api/public/datasets", {"name": DATASET, "description": "知序评估数据集"})
    r.raise_for_status()
    print("dataset created:", DATASET)


if __name__ == "__main__":
    for cfg in SCORE_CONFIGS:
        ensure_score_config(cfg)
    queue_id = ensure_queue()
    ensure_dataset()
    print("LANGFUSE_ANNOTATION_QUEUE_ID=" + queue_id)
```

- [ ] **Step 2: 对本地实例执行**

Run:
```bash
LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
  .venv/Scripts/python.exe scripts/langfuse_bootstrap.py
```
Expected: 4 个 score-config、`zhixu-review` 队列、`zhixu-evals` 数据集均输出 created/exists；重复执行输出 exists。

- [ ] **Step 3: 补单元测试**

`tests/observability/test_langfuse_bootstrap_plan.py`：monkeypatch `httpx.get/post`，断言首次创建、二次跳过；用 `importlib` 加载脚本模块执行函数。

- [ ] **Step 4: 提交**

```bash
git add scripts/langfuse_bootstrap.py tests/observability/test_langfuse_bootstrap_plan.py
git commit -m "feat(observability): 增加 Langfuse 评分配置/队列/数据集 bootstrap"
```

---

### Task 8: LLM-as-a-Judge 配置与验收

**Files:**
- Create: `scripts/langfuse_wait_for_score.py`
- Modify: `docs/guides/configure-langfuse-observability.md`

- [ ] **Step 1: 写轮询脚本**

```python
"""Wait until a score with the given name/source appears (for acceptance checks)."""
import os, sys, time
import httpx

BASE = os.environ.get("LANGFUSE_BASE_URL", "http://127.0.0.1:3000").rstrip("/")
AUTH = (os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"])
name = sys.argv[1] if len(sys.argv) > 1 else "judge_quality"
source = sys.argv[2] if len(sys.argv) > 2 else "EVAL"
deadline = time.time() + 120
while time.time() < deadline:
    data = httpx.get(f"{BASE}/api/public/scores", params={"name": name, "limit": 10}, auth=AUTH, timeout=10).json().get("data", [])
    hits = [s for s in data if str(s.get("source")) == source]
    if hits:
        print("found:", hits[0]["id"], hits[0].get("traceId"), hits[0].get("value"))
        sys.exit(0)
    time.sleep(5)
print("timeout")
sys.exit(1)
```

- [ ] **Step 2: 文档写明原生 evaluator 配置**

在 `docs/guides/configure-langfuse-observability.md` 增加小节：

- 前置：Langfuse → 设置 → 模型连接，添加一个 OpenAI 兼容连接（baseURL + key）。
- 在「评估 → 模型评审」新建 evaluator：目标 `trace`、过滤 `name = zhixu.turn`、评分配置 `judge_quality`（NUMERIC 0–1）、提示词要求“根据 trace 输入/输出给出 0–1 质量分并简短说明”。
- 保存并开启 Live evaluation。

- [ ] **Step 3: 验收**

在知序 WebUI 发一轮真实对话，然后运行：
```bash
LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
  .venv/Scripts/python.exe scripts/langfuse_wait_for_score.py judge_quality EVAL
```
Expected: 120s 内输出 `found: <score id> <trace id> <value>`。

- [ ] **Step 4: 提交**

```bash
git add scripts/langfuse_wait_for_score.py docs/guides/configure-langfuse-observability.md
git commit -m "docs(observability): 补充 LLM-as-a-Judge 配置与验收脚本"
```

---

### Task 9: Human Annotation 入队动作

**Files:**
- Modify: `webui/src/components/MessageBubble.tsx`（动作菜单，`client.requestMutation("annotation.add", ...)`）
- Modify: `webui/src/i18n/locales/<code>/common.json`（`message.feedback.annotation`）
- Modify: `nanobot/webui/ws_http.py`（Task 5 已建 handler，本任务补 resolve queue）
- Test: `webui/src/tests/message-feedback.test.tsx`（新增入队用例）

- [ ] **Step 1: 后端 handler**

```python
    async def _handle_webui_annotation(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = _mutation_payload(request) or {}
        trace_id = payload.get("trace_id")
        if not isinstance(trace_id, str) or len(trace_id) != 32:
            return _http_error(400, "invalid trace_id")
        queue_id = await resolve_queue_id(os.environ.get("LANGFUSE_ANNOTATION_QUEUE", "zhixu-review"))
        if queue_id is None:
            return _http_error(503, "annotation queue unavailable")
        ok = await add_trace_to_queue(queue_id=queue_id, trace_id=trace_id)
        return _http_json_response({"ok": ok})
```

- [ ] **Step 2: 前端动作**

`MessageBubble.tsx`：在点赞/点踩旁加菜单项，点击时调用 `client.requestMutation("annotation.add", { trace_id: message.traceId })`，文案 `t("message.feedback.annotation")`（10 locale 表见 Task 6 键集；补充：en `Add to review queue`、zh-CN `加入人工标注`、zh-TW `加入人工標註`、ja `アノテーションに追加`、ko `주석 대기열에 추가`、fr `Ajouter à la file d'annotation`、es `Añadir a la cola de anotación`、pt-BR `Adicionar à fila de anotação`、vi `Thêm vào hàng đợi chú thích`、id `Tambahkan ke antrean anotasi`）。

- [ ] **Step 3: 测试与验收**

Run: `cd webui && bun run test -- message-feedback`
Expected: passed。

验收：点某条 trace 的「加入人工标注」→ Langfuse「人工标注」→ `zhixu-review` 队列出现该 trace；在队列内用 `human_quality` 打分并提交。

- [ ] **Step 4: 提交**

```bash
git add webui/src/components/MessageBubble.tsx webui/src/i18n webui/src/tests/message-feedback.test.tsx nanobot/webui/ws_http.py
git commit -m "feat(webui): 支持将 trace 加入 Langfuse 人工标注队列"
```

---

### Task 10: Datasets 写入与实验运行器

**Files:**
- Modify: `webui/src/components/MessageBubble.tsx`（动作菜单，`client.requestMutation("dataset.add", ...)`）
- Modify: `webui/src/i18n/locales/<code>/common.json`（`message.feedback.dataset`）
- Modify: `nanobot/webui/ws_http.py`（dataset handler）
- Create: `scripts/langfuse_experiment.py`
- Test: `tests/observability/test_langfuse_experiment_evaluators.py`

- [ ] **Step 1: 数据集写入动作**

后端 handler（同 Task 5 结构）：

```python
    async def _handle_webui_dataset_item(self, request: WsRequest) -> Response:
        if not self.check_api_token(request):
            return _http_error(401, "Unauthorized")
        payload = _mutation_payload(request) or {}
        trace_id = payload.get("trace_id")
        if not isinstance(trace_id, str) or len(trace_id) != 32:
            return _http_error(400, "invalid trace_id")
        trace_io = await fetch_trace_io(trace_id)
        if trace_io is None:
            return _http_error(502, "trace not available")
        input_text = payload.get("input_text") or trace_io[0]
        expected_output = payload.get("expected_output") or trace_io[1]
        if not str(input_text).strip() or not str(expected_output).strip():
            return _http_error(400, "input/output empty")
        ok = await create_dataset_item(
            dataset_name=os.environ.get("LANGFUSE_DATASET", "zhixu-evals"),
            trace_id=trace_id,
            input_text=str(input_text),
            expected_output=str(expected_output),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )
        return _http_json_response({"ok": ok})
```

前端菜单项调用 `client.requestMutation("dataset.add", { trace_id: message.traceId })`（input/output 由网关从 Langfuse trace 读取，见 `fetch_trace_io`）；按钮文案 `message.feedback.dataset`（en `Save to dataset`、zh-CN `存入数据集`、zh-TW `存入資料集`、ja `データセットに保存`、ko `데이터셋에 저장`、fr `Enregistrer dans le jeu de données`、es `Guardar en el conjunto de datos`、pt-BR `Salvar no conjunto de dados`、vi `Lưu vào bộ dữ liệu`、id `Simpan ke kumpulan data`）。

- [ ] **Step 2: 实验运行器**

```python
"""Run a Zhixu agent over a Langfuse dataset and record a dataset run."""
from __future__ import annotations

import argparse
import asyncio
import os

from langfuse import Langfuse
from langfuse.experiment import Evaluation
from nanobot.nanobot import Nanobot


async def task(*, item, **_: object) -> str:
    async with Nanobot.from_config() as bot:
        result = await bot.run(
            str(item.input),
            session_key=f"langfuse-exp:{item.id}",
            channel="langfuse-experiment",
            chat_id=item.id,
            sender_id="experiment",
            ephemeral=True,
        )
    return result.content


def exact_match(*, input, output, expected_output, metadata=None, **_: object) -> Evaluation:
    value = (
        1.0
        if expected_output is not None and str(output).strip() == str(expected_output).strip()
        else 0.0
    )
    return Evaluation(name="exact_match", value=value)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default=os.environ.get("LANGFUSE_DATASET", "zhixu-evals"))
    parser.add_argument("--run-name", default=None)
    args = parser.parse_args()
    lf = Langfuse()
    dataset = lf.get_dataset(args.dataset)
    result = dataset.run_experiment(
        name="zhixu-baseline",
        run_name=args.run_name,
        task=task,
        evaluators=[exact_match],
    )
    print(result.format())
    lf.flush()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 单元测试 evaluator**

```python
from scripts.langfuse_experiment import exact_match


def test_exact_match():
    assert exact_match(input="q", output="pong", expected_output="pong", metadata=None).value == 1.0
    assert exact_match(input="q", output="ping", expected_output="pong", metadata=None).value == 0.0
```

Run: `pytest tests/observability/test_langfuse_experiment_evaluators.py -v`
Expected: passed。

- [ ] **Step 4: 端到端验收**

1. 在知序里对一条成功回答点「存入数据集」；
2. Langfuse「数据集」→ `zhixu-evals` 出现条目，且 `source_trace_id` 指向原 trace；
3. 运行：
```bash
LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
  .venv/Scripts/python.exe scripts/langfuse_experiment.py --run-name baseline-$(date +%H%M)
```
Expected: 控制台输出 dataset run 摘要；Langfuse 数据集页可见 run，且每条有 `exact_match` score。

- [ ] **Step 5: 提交**

```bash
git add webui/src/components/MessageBubble.tsx webui/src/i18n scripts/langfuse_experiment.py tests/observability/test_langfuse_experiment_evaluators.py nanobot/webui/ws_http.py
git commit -m "feat(eval): 数据集写入与知序数据集实验运行器"
```

---

### Task 11: 文档、隐私说明与总回归

**Files:**
- Modify: `docs/configuration.md`（Langfuse 小节）
- Modify: `docs/guides/configure-langfuse-observability.md`
- Modify: `docker/langfuse/README.md`
- Create: `.ai-runtime-artifacts/verifications/2026-11-20-langfuse-eval-integration-verification.md`

- [ ] **Step 1: 文档补充**

- trace 结构：`zhixu.turn` + 子 generation；`session_id`/`user_id` 来源与哈希方式；`LANGFUSE_USER_SALT`。
- 四条链路操作步骤（点赞、评审器、标注队列、数据集与实验命令）。
- 隐私：trace 含完整 prompt/output；user id 默认哈希；生产建议独立项目。
- 故障行为：Langfuse 不可达时 turn 正常完成，反馈返回 `{"ok": false}`。

- [ ] **Step 2: 总回归清单**

Run:
```bash
pytest tests/observability tests/webui tests/agent -q
cd webui && bun run test
cd webui && bun run build
```
Expected: 全绿。

- [ ] **Step 3: 端到端验收（写 verification 产物）**

逐项记录证据：trace 结构截图/API、feedback score API 结果、judge EVAL score、annotation queue 条目、dataset run 对比页。写入 `.ai-runtime-artifacts/verifications/2026-11-20-langfuse-eval-integration-verification.md`。

- [ ] **Step 4: 提交**

```bash
git add docs docker/langfuse/README.md .ai-runtime-artifacts/verifications/2026-11-20-langfuse-eval-integration-verification.md
git commit -m "docs(observability): 记录 Langfuse 评估闭环接入与验收"
```

---

## 7. 风险与开放问题

| 风险 | 影响 | 处置 |
| --- | --- | --- |
| SDK 嵌套依赖当前 context（Task 1） | trace 拆散 | Task 1 先行验证；失败则用 `trace_context` 显式传 trace id |
| Langfuse 服务端 3.174 与 SDK 3.15 API 差异（score-config / annotation / dataset） | bootstrap 失败 | Task 7 对真实实例执行；失败时按 4xx 响应调整字段（实现期确认） |
| transcript 字段新增影响旧会话 | 前端空白字段 | 可选字段，投影缺省不输出 |
| i18n parity 测试 | CI 失败 | Task 6/9/10 同步 10 locale |
| trace 体量：完整 prompt/output | 隐私/存储 | 文档明示；后续可加截断/脱敏 |
| 反馈接口被滥用 | 脏数据 | 仅登录 token 可调；trace_id 格式校验；幂等 id |
| 实验运行消耗真实模型额度 | 成本 | 默认小数据集；运行前打印条目数并要求 `--yes`（实现时补） |

## 8. 自检

- **覆盖**：Turn trace → Task 1–3；Scores → Task 5–7；LLM-as-a-Judge → Task 8；Human Annotation → Task 9（评分配置在 Task 7）；Datasets → Task 10；文档/回归 → Task 11。
- **占位符**：无 TBD/TODO；未知项集中在「风险与开放问题」并给出验证任务。
- **类型一致**：`TurnTraceHandle`、`langfuse_trace_id`、`trace_id`（wire）、`traceId`（前端 DTO）命名贯穿全篇；mutation 动作 `feedback.submit / annotation.add / dataset.add` 前后端一致。

## Next

- 计划确认 → 说「开始实现」或「执行」
- 需要调整 → 直接说修改意见（例如把 LLM-as-a-Judge 改成知序内自建评审、或先只做 Scores）
- 想拆分并行 → 说明后可另写 `*-dispatch.md` 再「并行执行」
