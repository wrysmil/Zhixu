"""Regression guards for what automatic extraction must not save."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nanobot.memory.profile_extractor import ProfileExtractor

_USER_MESSAGE = (
    "纸舟客服新增了邮件接入。今天网关超时后的重投，又产生了两张工单。"
    "我们以前处理过类似问题，你能找回当时确认的方案，并说明这次需要先核对哪些证据吗？"
)


def _runtime(items: list[dict], experiences: list[dict] | None = None):
    class Provider:
        async def chat_with_retry(self, *, messages, **kwargs):
            payload = {
                "memories": items,
                "experiences": experiences or [],
            }
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    return SimpleNamespace(
        provider=Provider(),
        model="fake",
        generation=SimpleNamespace(
            temperature=0.0,
            max_tokens=2000,
            reasoning_effort=None,
        ),
    )


@pytest.mark.asyncio
async def test_recall_prompt_premises_are_not_extracted_as_profile_facts():
    items = [
        {
            "content": "纸舟客服已新增邮件接入",
            "type": "FACT",
            "evidence": "纸舟客服新增了邮件接入",
        },
        {
            "content": "纸舟客服在网关超时后的重投中再次产生了两张工单",
            "type": "ERROR",
            "evidence": "今天网关超时后的重投，又产生了两张工单",
        },
        {
            "content": "用户及其团队此前处理过纸舟客服工单重复类问题",
            "type": "FACT",
            "evidence": "我们以前处理过类似问题",
        },
        {
            "content": "用户处理类似问题时倾向于先找回此前确认的方案",
            "type": "PREFERENCE",
            "evidence": "你能找回当时确认的方案",
        },
        {
            "content": "处理重复工单类问题时，先检索历史事故中已经确认的解决方案",
            "type": "EXPERIENCE",
            "evidence": "你能找回当时确认的方案",
        },
        {
            "content": "先确认两张工单的全量标识、实际接入路径和历史修复是否生效",
            "type": "EXPERIENCE",
            "evidence": "并说明这次需要先核对哪些证据吗？",
        },
    ]

    result = await ProfileExtractor(_runtime(items)).extract(
        [{"role": "user", "content": _USER_MESSAGE}],
        "episode-1",
    )

    assert {item.content for item in result.items} == {
        "纸舟客服已新增邮件接入",
        "纸舟客服在网关超时后的重投中再次产生了两张工单",
    }


@pytest.mark.asyncio
async def test_experiences_require_current_batch_evidence():
    experiences = [
        {"content": "先检查日志再重试", "type": "EXPERIENCE", "evidence": "请检查日志"},
        {"content": "先检查健康状态再切换流量", "type": "EXPERIENCE"},
    ]
    result = await ProfileExtractor(_runtime([], experiences)).extract(
        [
            {"role": "user", "content": "请检查日志"},
            {"role": "assistant", "content": "先检查健康状态再切换流量"},
        ],
        "episode-2",
    )
    assert result.experiences == []
