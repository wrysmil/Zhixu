"""受控 provider：离线端到端用的脚本化 LLM，**只在 fixture 模式生效**。

为什么需要它：判分链路要能从「用例 JSON」一路跑到「verdict」而不碰网络。真实
provider 冒烟是另一件事（spec §验证：两者分开表述，不混称），本模块不承担。

行为约束：

- 只消费用例里声明的脚本，按顺序逐步返回；脚本用尽后重复最后一步。
- 每次调用都上报 usage，因此 Token 账本是**真实物理调用**而不是补零。
- 绝不在 fixture 模式下触网，也绝不因为脚本缺失就静默返回空回答——那会让
  「Agent 什么都没说」看起来像一次正常执行。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from nanobot.providers.base import LLMResponse

CONTROLLED_MODEL = "eval-model"


class ControlledScriptError(ValueError):
    """脚本本身不合法：这是用例配置错误，不是执行失败。"""


@dataclass
class ScriptStep:
    """一步脚本：可选文本 + 可选工具调用。"""

    text: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    #: 评分场景下直接返回的 JSON 字符串（Judge 用）。
    json_text: str | None = None


@dataclass
class ControlledProvider:
    """按脚本回放的 provider；接口对齐 ``chat_with_retry``。"""

    steps: Sequence[ScriptStep] = ()
    model: str = CONTROLLED_MODEL
    generation: Mapping[str, Any] = field(default_factory=dict)
    usage_records: list[Any] = field(default_factory=list)
    _cursor: int = 0

    def get_default_model(self) -> str:
        return self.model

    def set_llm_call_observer(self, observer: Any) -> None:
        self._observer = observer

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: Any = None,
        model: Any = None,
        **kwargs: Any,
    ) -> Any:
        if not self.steps:
            raise ControlledScriptError("CONTROLLED_SCRIPT_EMPTY: 没有可回放的脚本")
        index = min(self._cursor, len(self.steps) - 1)
        self._cursor += 1
        step = self.steps[index]
        response = _build_response(
            content=step.json_text if step.json_text is not None else step.text,
            tool_calls=list(step.tool_calls),
            index=index,
            model=str(model or self.model),
        )
        self._report_usage(response)
        return response

    def _report_usage(self, response: "Any") -> None:
        """上报一条真实形态的 usage 记录，让账本走真实观测路径。

        脚本模式的 token 数没有物理意义，但记录必须存在：否则 coverage 永远是
        unavailable，「物理调用是唯一用量源」这条路径根本没被测到。
        ``reported_tokens + estimated_tokens`` 必须恰好等于 total。
        """
        observer = getattr(self, "_observer", None)
        if observer is None:
            return
        import time

        from nanobot.llm_usage.models import LLMCallRecord, LLMUsage

        tokens = max(1, len(response.content or "") // 4)
        total = tokens * 2
        observer(
            LLMCallRecord(
                started_at_ms=int(time.monotonic() * 1000),
                duration_ms=1,
                provider="controlled",
                model=self.model,
                source="api",
                stream=False,
                finish_reason="tool_calls" if response.tool_calls else "stop",
                usage=LLMUsage(
                    input_tokens=tokens,
                    output_tokens=tokens,
                    total_tokens=total,
                    reported_tokens=total,
                ),
            )
        )


def _build_response(
    *, content: str, tool_calls: Sequence[Mapping[str, Any]], index: int, model: str
) -> "LLMResponse":
    """构造 SDK 真正的响应类型。

    不能自造一个只有 content/tool_calls 的轻量对象：runner 会读
    ``provider_compaction_applied`` 等字段，缺一个就是 AttributeError，
    失败原因还会被误当成「脚本回放失败」。
    """
    from nanobot.providers.base import LLMResponse, ToolCallRequest

    return LLMResponse(
        content=content,
        finish_reason="tool_calls" if tool_calls else "stop",
        tool_calls=[
            ToolCallRequest(
                id=f"call_{index}_{position}",
                name=str(call.get("name", "")),
                arguments=dict(call.get("arguments") or {}),
            )
            for position, call in enumerate(tool_calls)
        ],
        usage=None,
        provider_compaction_applied=False,
    )


def steps_from_script(script: Sequence[Mapping[str, Any]]) -> tuple[ScriptStep, ...]:
    """把用例里的 ``script`` 字段解析成脚本步；字段形状由 schemas.ScriptStep 校验。"""
    steps: list[ScriptStep] = []
    for index, raw in enumerate(script):
        calls = tuple(
            {"name": str(item["name"]), "arguments": dict(item.get("arguments") or {})}
            for item in raw.get("tool_calls") or ()
        )
        steps.append(
            ScriptStep(
                text=str(raw.get("text") or ""),
                tool_calls=calls,
                json_text=raw.get("json_text"),
            )
        )
        if not calls and not (raw.get("text") or raw.get("json_text")):
            raise ControlledScriptError(
                f"CONTROLLED_STEP_EMPTY: 第 {index + 1} 步既无文本也无工具调用"
            )
    return tuple(steps)


def judge_steps(
    dimension_ids: Sequence[str], *, scores: Sequence[int]
) -> tuple[ScriptStep, ...]:
    """构造一份合法评分输出，供 Judge 的离线用例回放。"""
    payload = {
        "dimensions": [
            {
                "id": dimension_id,
                "score": int(score),
                "reason": "受控脚本给出的评分",
                "evidence_refs": [],
            }
            for dimension_id, score in zip(dimension_ids, scores, strict=False)
        ]
    }
    return (ScriptStep(json_text=json.dumps(payload, ensure_ascii=False)),)


def dump_script(steps: Sequence[ScriptStep]) -> str:
    return json.dumps(
        [
            {
                "text": step.text,
                "json_text": step.json_text,
                "tool_calls": [
                    {"name": call["name"], "arguments": call["arguments"]}
                    for call in step.tool_calls
                ],
            }
            for step in steps
        ],
        ensure_ascii=False,
    )
