"""Seed mock zhixu.turn traces and user_feedback scores into the local Langfuse."""
from __future__ import annotations

import argparse
import hashlib
import os
import random

QUESTIONS = [
    "知序的记忆系统是怎么工作的？",
    "怎么给知识库导入 PDF？",
    "解释一下工具调用的生命周期",
    "如何配置多个模型 provider？",
    "帮我总结这个项目的架构",
    "怎么开启 WebUI 的远程访问？",
    "Trace 和 Session 有什么区别？",
    "写一个 pytest 用例测记忆检索",
]

ANSWERS = [
    "知序把记忆拆成情节、事实与索引三层，按需检索后再注入上下文。",
    "在知识库页面选择导入，支持 PDF/Word/Markdown，解析后自动分块并建立向量索引。",
    "模型发起 tool_call 后由 AgentRunner 执行工具，结果回填到下一轮模型请求。",
    "在设置 → 模型连接中添加 provider 与 API Key，再在模型配置里指定默认模型。",
    "知序由 AgentLoop、Provider、Channel 与工具注册表组成，WebUI 通过 WebSocket 多路复用协议接入。",
    "启动网关时绑定 0.0.0.0 并在防火墙放行端口，同时确保 tokenIssueSecret 足够强。",
    "Trace 是一次端到端请求，Session 是按会话聚合的一组 trace。",
    "可以用临时会话构造上下文，再断言检索结果包含预期记忆。",
]

COMMENTS_UP = ["回答准确", "结构清晰", "正好解决了问题", ""]
COMMENTS_DOWN = ["答非所问", "漏掉了关键步骤", "引用了错误的信息"]


def user_id(index: int) -> str:
    salt = os.environ.get("LANGFUSE_USER_SALT", "zhixu")
    return hashlib.sha256(f"mock-user-{index % 5}:{salt}".encode()).hexdigest()[:16]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=24)
    parser.add_argument("--up-ratio", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=20261120)
    args = parser.parse_args()
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        print("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are required")
        return 2

    random.seed(args.seed)
    from langfuse import Langfuse

    lf = Langfuse()
    up = 0
    down = 0
    for index in range(args.count):
        question = QUESTIONS[index % len(QUESTIONS)]
        answer = ANSWERS[index % len(ANSWERS)]
        with lf.start_as_current_span(
            name="zhixu.turn",
            input={"message": question},
            metadata={"source": "mock", "channel": "websocket"},
        ) as span:
            lf.update_current_trace(
                session_id=f"mock:session-{index % 4}",
                user_id=user_id(index),
                tags=["zhixu", "mock"],
            )
            trace_id = lf.get_current_trace_id()
            with lf.start_as_current_generation(
                name="chat",
                model="mock-model",
                input=[{"role": "user", "content": question}],
                output={"role": "assistant", "content": answer},
                usage_details={
                    "input": random.randint(40, 180),
                    "output": random.randint(10, 80),
                },
            ):
                pass
            span.update(output={"message": answer})

        helpful = random.random() < args.up_ratio
        if helpful:
            up += 1
            comment = random.choice(COMMENTS_UP)
        else:
            down += 1
            comment = random.choice(COMMENTS_DOWN)
        lf.create_score(
            trace_id=trace_id,
            name="user_feedback",
            value=1 if helpful else 0,
            data_type="BOOLEAN",
            comment=comment or None,
        )

    lf.flush()
    print(f"seeded traces={args.count} up={up} down={down}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
