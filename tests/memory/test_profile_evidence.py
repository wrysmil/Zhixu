"""画像只能来自本批用户证据，不能从检索与助手复述循环提取。"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.models import Memory
from nanobot.memory.profile_extractor import ProfileExtractor
from nanobot.memory.repository import add_memory, list_memories
from nanobot.session.manager import Session


def _runtime(items, experiences=()):
    class Provider:
        async def chat_with_retry(self, *, messages, **kwargs):
            semantic = "语义记忆抽取" in messages[0]["content"]
            payload = ({"memories": items, "experiences": list(experiences)} if semantic
                       else {"summary": "用户询问此前提到的宠物，助手检索历史后回答。"})
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    return SimpleNamespace(provider=Provider(), model="fake", generation=SimpleNamespace(
        temperature=0.0, max_tokens=2000, reasoning_effort=None,
    ))


@pytest.mark.parametrize("evidence", [None, "用户养了一只名叫咪咪的猫", "还记得我养的猫吗", "猫"])
async def test_recall_does_not_reextract_old_profile(tmp_path, evidence):
    db = MemoryDatabase(tmp_path)
    db.init_schema()
    old = ["用户住在杭州", "用户从事后端开发工作", "用户偏好无糖咖啡", "用户养了一只名叫咪咪的猫"]
    with db.connect() as conn:
        for i, content in enumerate(old):
            add_memory(conn, Memory(id=f"old-{i}", content=content, created_at="2026-11-02"))
    items = [{"content": content + "。", "type": "FACT", "subject": "用户", "predicate": "背景", "evidence": evidence} for content in old]
    extractor = MemoryExtractor(db, _runtime(items))
    session = Session(key="recall-only", messages=[
        {"role": "user", "content": "还记得我养的猫吗"},
        {"role": "assistant", "tool_calls": [{"id": "c", "function": {"name": "memory_search", "arguments": '{"query":"猫"}'}}]},
        {"role": "tool", "tool_call_id": "c", "content": "；".join(old)},
        {"role": "assistant", "content": "记得，咪咪。你住杭州，做后端，咖啡不加糖。"},
    ])
    result = await extractor.extract_session(session)
    with db.connect() as conn:
        rows = list_memories(conn)
    assert {row.content for row in rows} == set(old)
    assert not any(row.metadata.get("conflicts_with") for row in rows)
    assert result.episode_ids  # 情节仍能记录此次检索与回答。


@pytest.mark.parametrize(("user", "evidence", "accepted"), [
    ("我住杭州，能推荐本地咖啡吗？", "我住杭州", True),
    ("我现在改喝无糖咖啡了", "我现在改喝无糖咖啡了", True),
    ("记得以后推荐咖啡不要加糖", "记得以后推荐咖啡不要加糖", True),
    ("记得我的猫叫什么吗", "我的猫", False),
    ("还记得我的猫吗？它现在三岁了。", "它现在三岁了", True),
    ("请回忆一下我住哪里", "我住哪里", False),
    ("你还记得我住哪里吗", "我住哪里", False),
    ("我住杭州", "我住上海", False),
    ("我住杭州", None, False),
])
async def test_user_evidence_boundary(user, evidence, accepted):
    item = {"content": "用户的事实", "type": "FACT", "evidence": evidence}
    result = await ProfileExtractor(_runtime([item])).extract(
        [{"role": "user", "content": user}, {"role": "assistant", "content": "我住上海"}], "e"
    )
    assert bool(result.items) is accepted


async def test_invalid_profile_does_not_drop_experiences():
    result = await ProfileExtractor(_runtime(
        [{"content": "用户喜欢咖啡", "type": "PREFERENCE"}],
        [{
            "content": "先检查服务健康状态，再重试请求",
            "type": "EXPERIENCE",
            "evidence": "检查结果显示服务健康状态异常，重试请求成功",
        }],
    )).extract([
        {"role": "user", "content": "检查失败原因"},
        {"role": "tool", "content": "检查结果显示服务健康状态异常，重试请求成功"},
    ], "e")
    assert result.items == []
    assert len(result.experiences) == 1


@pytest.mark.parametrize("type_name", ["FACT", "PREFERENCE", "RULE", None])
async def test_experiences_cannot_bypass_profile_evidence(tmp_path, type_name):
    db = MemoryDatabase(tmp_path)
    db.init_schema()
    item = {"content": "用户养了一只名叫咪咪的猫"}
    if type_name is not None:
        item["type"] = type_name
    extractor = MemoryExtractor(db, _runtime([], [item]))
    result = await extractor.extract_session(Session(key="experience-bypass", messages=[
        {"role": "user", "content": "还记得我的猫吗"},
        {"role": "assistant", "content": "咪咪。"},
    ]))
    assert result.memory_ids == []
    assert result.episode_ids
    with db.connect() as conn:
        assert list_memories(conn) == []


@pytest.mark.parametrize("type_name", ["EXPERIENCE", "SKILL", "ERROR"])
async def test_supported_experience_types_keep_existing_format(type_name):
    result = await ProfileExtractor(_runtime([], [{
        "content": "先检查健康状态，再重试请求",
        "type": type_name,
        "evidence": "检查结果显示服务健康状态异常，重试请求成功",
    }])).extract([
        {"role": "user", "content": "检查失败原因"},
        {"role": "tool", "content": "检查结果显示服务健康状态异常，重试请求成功"},
    ], "e")
    assert [item.type for item in result.experiences] == [type_name]
