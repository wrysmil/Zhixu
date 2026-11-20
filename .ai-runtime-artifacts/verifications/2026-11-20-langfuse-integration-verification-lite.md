# Langfuse 本地部署与知序接入验证（verification-lite）

- 日期：2026-11-20
- 范围：在本地 Docker 部署自托管 Langfuse v3，并把知序 OpenAI 兼容 provider 的调用上报到该实例
- 脚本：`.ai-runtime-artifacts/verifications/2026-11-20-langfuse-trace-verify.py`
- 未改动知序业务代码；复用仓库既有接入路径（`langfuse.openai.AsyncOpenAI` 包装）

## 部署

| 项 | 值 |
| --- | --- |
| Compose 文件 | `docker/langfuse/docker-compose.yml`（基于官方 v3.227.1 compose，端口全部绑定 127.0.0.1） |
| 密钥 | `docker/langfuse/.env`（已被仓库 `.gitignore` 的 `.env` 规则忽略） |
| 实际服务端版本 | Langfuse 3.174.1（镜像 tag `3`），与仓库 Python SDK 3.15.0 同属 3.x |
| Docker / Compose | 29.4.0 / v5.1.1，宿主内存 16 GB |

容器状态（`docker compose ps`）：

```
langfuse-clickhouse-1        Up (healthy)
langfuse-langfuse-web-1      Up
langfuse-langfuse-worker-1   Up
langfuse-minio-1             Up (healthy)
langfuse-postgres-1          Up (healthy)
langfuse-redis-1             Up (healthy)
```

## 验证证据

### 1. 健康检查

```
GET http://127.0.0.1:3000/api/public/health
200 {"status":"OK","version":"3.174.1"}
```

### 2. 首启初始化（org / project / keys）

```
GET /api/public/projects  (basic auth: pk-lf-*** / sk-lf-***)
{"data":[{"id":"zhixu","name":"zhixu","organization":{"id":"zhixu-local","name":"Zhixu Local"},"metadata":{}}]}
```

### 3. 知序设置页能力数据

以 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` 调用
`capability_settings_payload()`：

```
{'provider': 'langfuse', 'configured': True, 'base_url': 'http://127.0.0.1:3000'}
```

### 4. 端到端 trace（不消耗真实模型额度）

脚本用本地 mock `/v1/chat/completions` 驱动 `OpenAICompatProvider`，环境变量存在时
provider 选用 `langfuse.openai.AsyncOpenAI`；flush 后查询 Langfuse API：

```
provider content='pong' finish=stop
traces:
  id=65fafc55335fe8d9161fa0df4b3ccdc2 name=OpenAI-generation ts=2026-11-20T05:18:02.124Z
observations=1
  type=GENERATION name=OpenAI-generation model=mock-model traceId=65fafc55...
PASS
```

## 环境变量（已写入当前 Windows 用户级，重启桌面端/网关后生效）

| 变量 | 值 |
| --- | --- |
| `LANGFUSE_PUBLIC_KEY` | `.env` 的 `LANGFUSE_INIT_PROJECT_PUBLIC_KEY` |
| `LANGFUSE_SECRET_KEY` | `.env` 的 `LANGFUSE_INIT_PROJECT_SECRET_KEY` |
| `LANGFUSE_BASE_URL` | `http://127.0.0.1:3000` |

## 复跑命令

```bash
cd /d/studyspace/project/Zhixu
LANGFUSE_PUBLIC_KEY=... LANGFUSE_SECRET_KEY=... LANGFUSE_BASE_URL=http://127.0.0.1:3000 \
  .venv/Scripts/python.exe .ai-runtime-artifacts/verifications/2026-11-20-langfuse-trace-verify.py
```

## 2026-11-20 追加：真实问答 + 点赞闭环（Scores）

模型：`deepseek/deepseek-v4.1-flash`（provider `custom-command-ai`）。

| 检查 | 结果 |
| --- | --- |
| 流式回答携带 traceId | transcript `stream_end` 记录 `trace_id=0caffa2f6a7415fe17638814597bdb1a` |
| UI 按钮 | assistant 消息出现 “Helpful / Not helpful”（英文 locale），点击后 `pressed=true` 且显示 “Feedback saved” |
| Langfuse trace | `0caffa2f...`：name=`zhixu.turn`，含 GENERATION（deepseek/deepseek-v4.1-flash）+ SPAN |
| Langfuse score | `zhixu-feedback-0caffa2f...`，`value=1`，`dataType=BOOLEAN`，`source=API` |

实现要点：turn 开始时把 trace id 同时登记到内部 `ctx.turn_id` 与客户端 `webui_turn_id`；WebSocket 层在 `stream_end`/message 帧兜底回填，transcript 投影保留 `traceId`；前端在 assistant 消息 footer 渲染 👍/👎。

### 反馈持久化（刷新后保持）

- 写入：feedback mutation 成功后向 transcript 追加 `{"event":"feedback","trace_id":...,"turn_id":...,"helpful":bool}`（优先用 trace→session 映射，重启后回退到调用方 chat）。
- 投影：`replay_transcript_to_ui_messages` 把 feedback 事件回填到对应 assistant 消息的 `feedback: "up"|"down"`。
- 前端：`MessageBubble` 以 `message.feedback` 初始化按钮状态。
- 验证：整页重载并重新进入会话后，👍 `aria-pressed=true` 且显示 “Feedback saved”；transcript 含 feedback 事件，Python 投影结果为 `feedback: up`。

## 2026-11-20 追加：人工标注与 LLM 评审

### 评分配置与队列（scripts/langfuse_bootstrap.py）

| 对象 | 结果 |
| --- | --- |
| 评分配置 | `user_feedback`（BOOLEAN）、`judge_quality`（0–1）、`human_quality`（1–5）、`human_failure_type`（categorical） |
| 标注队列 | `zhixu-review`，绑定 `human_quality` + `human_failure_type` |

### 人工标注

- 知序消息动作“加入人工标注” → `annotation.add` mutation → `POST /api/public/annotation-queues/{id}/items`；`363214b4...` 已入队（`PENDING`）。
- 人工评分：`human_quality=5` 写入成功且 `source=ANNOTATION`（带 `queueId` + `configId`）；公共 API 的 `source` 只接受 `API` / `ANNOTATION`，`EVAL` 会被 400 拒绝。

### LLM-as-a-Judge

- Langfuse LLM 连接：`commandcode`（OpenAI adapter，Base URL `https://api.commandcode.ai/provider/v1`，自定义模型 `deepseek/deepseek-v4.1-flash`）。
- Evaluator：`Helpfulness` 模板 → score name `judge_quality`，目标 Observations（Type=GENERATION），变量映射 `query←Input`、`generation←Output`，采样 100%，实时生效。
- 实测：新对话产生 `judge_quality=0.95`（`source=EVAL`，含中文理由）；标题生成（空输出）也产生 0 分，建议后续在筛选里加 `Trace Name = zhixu.turn` 排除后台调用。
- 注意：`scripts/langfuse_judge.py` 的“脚本直写 EVAL”方案因公共 API 限制已废弃；EVAL 只能由原生 evaluator 产生。

## 已知边界

- 只覆盖 OpenAI 兼容客户端路径；原生 SDK provider（如 Anthropic/Responses-only 分支）不产生该包装 trace。
- 当前网关进程若在设置变量前已启动，需要重启才能读到 `LANGFUSE_*`。
- 数据保留在 Docker 命名卷 `langfuse_*`；`docker compose down -v` 会清空。
