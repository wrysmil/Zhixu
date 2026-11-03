"""抽取流水线落库时的写入决策（spec §5.4 / D08）。

覆盖：
- 同属性二次提取但未显式纠正 → 旧值保留，新值作为 uncertain 冲突候选并存
- 否定词不再决定冲突（不再有「不/没/无」终判）
- subject 或 predicate 缺失 → 无合并语义，新建
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.extractor import MemoryExtractor
from nanobot.memory.repository import list_memories, search_memories

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeGeneration:
    temperature = 0.2
    max_tokens = 1024
    reasoning_effort = None


class _FakeProvider:
    """按 prompt 内容分流返回 semantic / episode payload。"""

    def __init__(self, *, semantic: str, episode: str = '{"summary":"s"}') -> None:
        self.semantic = semantic
        self.episode = episode

    async def chat_with_retry(self, *, messages: list[dict[str, Any]], **_kwargs: Any):
        text = " ".join(
            m.get("content", "") for m in messages if isinstance(m.get("content"), str)
        )
        return _FakeResponse(self.semantic if "语义记忆抽取" in text else self.episode)


class _FakeRuntime:
    def __init__(self, provider: _FakeProvider) -> None:
        self.provider = provider
        self.model = "fake-model"
        self.generation = _FakeGeneration()


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _semantic(*memories: dict[str, Any]) -> str:
    return json.dumps({"memories": [dict(item, evidence=item["content"]) for item in memories], "experiences": []}, ensure_ascii=False)


def _make(db: MemoryDatabase, semantic: str) -> MemoryExtractor:
    return MemoryExtractor(db, _FakeRuntime(_FakeProvider(semantic=semantic)))


def _session(messages: list[dict[str, Any]]):
    from nanobot.session.manager import Session

    return Session(key="s1", messages=messages)


async def _extract(db: MemoryDatabase, semantic: str) -> Any:
    extractor = _make(db, semantic)
    # 写入决策测试的候选均由用户直接声明，避免无证据的假模型画像。
    payload = json.loads(semantic)
    facts = "；".join(
        item["content"]
        for item in [*payload.get("memories", []), *payload.get("experiences", [])]
    )
    return await extractor.extract_session(
        _session(
            [
                {"role": "user", "content": facts},
                {"role": "assistant", "content": "好的"},
            ]
        )
    )


def _rows(db: MemoryDatabase):
    with db.connect() as conn:
        return list_memories(conn, workspace_id="default")


# ---------------------------------------------------------------------------
# 合并
# ---------------------------------------------------------------------------


class TestWriteDecisionOnPersist:
    """D08：写入决策表取代旧「同 subject+predicate 自动覆盖 / 否定词终判」。"""

    async def test_same_attribute_without_explicit_correction_keeps_candidate(
        self, db: MemoryDatabase
    ):
        """自动模型候选未显式纠正 → 不覆盖旧值，另存带 uncertain 标记的候选。"""
        await _extract(
            db,
            _semantic(
                {
                    "content": "用户喜欢美咖啡",
                    "subject": "用户",
                    "predicate": "喜欢",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "importance": 0.6,
                }
            ),
        )
        assert len(_rows(db)) == 1

        await _extract(
            db,
            _semantic(
                {
                    "content": "用户偏好手冲咖啡的冲泡流程",
                    "subject": "用户",
                    "predicate": "喜欢",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                    "importance": 0.9,
                }
            ),
        )

        rows = _rows(db)
        assert len(rows) == 2, "条件/时间不清时不得自动覆盖，必须保留冲突候选"
        old = next(row for row in rows if row.content == "用户喜欢美咖啡")
        candidate = next(row for row in rows if row.content == "用户偏好手冲咖啡的冲泡流程")
        assert old.metadata.get("uncertain") is None
        assert candidate.metadata["uncertain"] is True
        assert candidate.metadata["conflicts_with"] == [old.id]

    async def test_conflicting_content_keeps_old_and_records_conflicts(self, db: MemoryDatabase):
        """否定词不再决定冲突：新值作为 uncertain 候选保留，旧值不被覆盖。"""
        await _extract(
            db,
            _semantic(
                {
                    "content": "用户不喜欢咖啡",
                    "subject": "用户",
                    "predicate": "喝",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                }
            ),
        )
        await _extract(
            db,
            _semantic(
                {
                    "content": "用户喜欢喝咖啡",
                    "subject": "用户",
                    "predicate": "喝",
                    "type": "PREFERENCE",
                    "priority": "long_term",
                }
            ),
        )

        rows = _rows(db)
        assert len(rows) == 2
        old = next(row for row in rows if row.content == "用户不喜欢咖啡")
        candidate = next(row for row in rows if row.content == "用户喜欢喝咖啡")
        assert old.metadata.get("conflicts_with") is None, "旧值不是冲突候选"
        assert candidate.metadata["conflicts_with"] == [old.id]
        assert candidate.metadata["uncertain"] is True


    async def test_missing_subject_falls_back_to_new_row(self, db: MemoryDatabase):
        """subject 为空 → 不具备合并语义，必须新建。"""
        await _extract(
            db,
            _semantic(
                {
                    "content": "用户喜欢美咖啡",
                    "subject": "用户",
                    "predicate": "喜欢",
                    "type": "PREFERENCE",
                }
            ),
        )
        await _extract(
            db,
            _semantic(
                {
                    "content": "没有任何主语的偏好陈述",
                    "subject": "",
                    "predicate": "",
                    "type": "PREFERENCE",
                }
            ),
        )

        assert len(_rows(db)) == 2, "空 subject/predicate 必须退化为新建"

    async def test_distinct_predicate_creates_new_row(self, db: MemoryDatabase):
        """subject 相同但 predicate 不同 → 视为不同画像，各自成行。"""
        await _extract(
            db,
            _semantic(
                {
                    "content": "用户喜欢美咖啡",
                    "subject": "用户",
                    "predicate": "喜欢",
                    "type": "PREFERENCE",
                }
            ),
        )
        await _extract(
            db,
            _semantic(
                {
                    "content": "用户讨厌嘈杂的环境",
                    "subject": "用户",
                    "predicate": "讨厌",
                    "type": "PREFERENCE",
                }
            ),
        )

        assert len(_rows(db)) == 2

    async def test_experiences_are_persisted_via_pipeline(self, db: MemoryDatabase):
        """双轨同源：experiences 与 memories 一样落 memories 表。"""
        await _extract(
            db,
            json.dumps(
                {
                    "memories": [
                        {
                            "content": "用户是前端工程师",
                            "subject": "用户",
                            "predicate": "是",
                            "type": "FACT",
                        }
                    ],
                    "experiences": [
                        {
                            "content": "用 uv 管理依赖可避免状态漂移",
                            "evidence": "用 uv 管理依赖可避免状态漂移",
                            "subject": "uv",
                            "predicate": "避免",
                            "type": "EXPERIENCE",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        )

        with db.connect() as conn:
            hits = search_memories(conn, "uv")
        assert any(m.type.value == "experience" for m in hits)
