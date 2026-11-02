"""写入决策表（spec §5.4 / 契约 §2.2）：``apply_memory_changes`` 的七条规则。

覆盖顺序：
1. 显式要求记住/纠正优先，短文本不被拦截，但仍校验来源与作用域
2. 完全相同内容+属性幂等，可追加支持证据
3. 同主体同属性 + 明确纠正 + 目标唯一 → 更新当前值并保存前后内容
4. 同主体同属性但未明确纠正 → 不自动覆盖，保留 uncertain 冲突候选
5. 多值属性/不同条件 → 并存
6. 助手建议/计划/未验证结果 → 不提升为用户当前事实
7. 明确遗忘 → 撤销当前候选并阻止同一旧证据键复活
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import (
    get_memory,
    is_evidence_suppressed,
    list_memories,
)
from nanobot.memory.writer import MemoryChange, apply_memory_changes


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


def _memory(
    content: str,
    *,
    subject: str = "用户",
    predicate: str = "偏好语言",
    workspace_id: str = "default",
    type: MemoryType = MemoryType.PREFERENCE,
    priority: MemoryPriority = MemoryPriority.LONG_TERM,
    source: str = "extraction",
    metadata: dict | None = None,
) -> Memory:
    return Memory(
        id="",
        content=content,
        type=type,
        priority=priority,
        source=source,
        subject=subject,
        predicate=predicate,
        workspace_id=workspace_id,
        created_at="2026-11-02T00:00:00+00:00",
        updated_at="2026-11-02T00:00:00+00:00",
        metadata=dict(metadata or {}),
    )


def _change(content: str | Memory, *, action: str = "add", **flags) -> MemoryChange:
    memory = content if isinstance(content, Memory) else _memory(content)
    return MemoryChange(memory=memory, action=action, **flags)


def _rows(db: MemoryDatabase) -> list[Memory]:
    with db.connect() as conn:
        return list_memories(conn)


class TestExplicitFirst:
    def test_explicit_short_content_bypasses_length_gate(self, db: MemoryDatabase):
        with db.connect() as conn:
            rejected = apply_memory_changes(conn, [_change("好")], evidence_key="b1")
            accepted = apply_memory_changes(
                conn, [_change("好", explicit=True)], evidence_key="b1-explicit"
            )

        assert rejected.outcomes[0].kind == "skipped"
        assert rejected.outcomes[0].reason == "content_too_short"
        assert accepted.outcomes[0].kind == "added"
        assert [row.content for row in _rows(db)] == ["好"]

    def test_explicit_still_requires_scope_and_source(self, db: MemoryDatabase):
        no_scope = MemoryChange(
            memory=_memory("用户偏好线性代数", workspace_id=""), explicit=True
        )
        no_source = MemoryChange(memory=_memory("用户偏好线性代数", source=""), explicit=True)
        with db.connect() as conn:
            result = apply_memory_changes(conn, [no_scope, no_source])

        assert [outcome.kind for outcome in result.outcomes] == ["skipped", "skipped"]
        assert [outcome.reason for outcome in result.outcomes] == [
            "missing_scope",
            "missing_source",
        ]
        assert _rows(db) == []


class TestIdempotentDuplicate:
    def test_identical_content_and_attributes_do_not_insert_again(self, db: MemoryDatabase):
        with db.connect() as conn:
            first = apply_memory_changes(conn, [_change("用户偏好中文回复")], evidence_key="b1")
            second = apply_memory_changes(
                conn, [_change("用户偏好中文回复")], evidence_key="b2"
            )

        rows = _rows(db)
        assert len(rows) == 1
        assert second.outcomes[0].kind == "unchanged"
        assert second.memory_ids == first.memory_ids
        keys = rows[0].metadata["evidence_keys"]
        assert len(keys) == 2
        assert keys[0].startswith("b1") and keys[1].startswith("b2")


class TestExplicitCorrection:
    def test_unique_target_is_updated_and_previous_content_kept(self, db: MemoryDatabase):
        with db.connect() as conn:
            apply_memory_changes(conn, [_change("用户偏好中文回复")], evidence_key="b1")
        target = _rows(db)[0]
        with db.connect() as conn:
            result = apply_memory_changes(
                conn,
                [_change("用户偏好英文回复", correction=True)],
                evidence_key="b2",
            )

        rows = _rows(db)
        assert len(rows) == 1
        assert result.outcomes[0].kind == "updated"
        assert result.memory_ids == (target.id,)
        assert rows[0].id == target.id
        assert rows[0].content == "用户偏好英文回复"
        assert rows[0].metadata["previous_content"] == "用户偏好中文回复"


class TestUncertainConflict:
    def test_same_attribute_without_explicit_correction_keeps_candidate(self, db: MemoryDatabase):
        with db.connect() as conn:
            apply_memory_changes(conn, [_change("用户偏好中文回复")], evidence_key="b1")
        old = _rows(db)[0]
        with db.connect() as conn:
            result = apply_memory_changes(
                conn, [_change("用户偏好英文回复")], evidence_key="b2"
            )

        rows = _rows(db)
        assert len(rows) == 2
        assert result.outcomes[0].kind == "conflict"
        assert result.outcomes[0].conflict_with == (old.id,)
        by_id = {row.id: row for row in rows}
        assert by_id[old.id].content == "用户偏好中文回复"
        candidate = by_id[result.outcomes[0].memory_id]
        assert candidate.content == "用户偏好英文回复"
        assert candidate.metadata["uncertain"] is True
        assert candidate.metadata["conflicts_with"] == [old.id]

    def test_unclear_conditions_do_not_auto_overwrite(self, db: MemoryDatabase):
        with db.connect() as conn:
            apply_memory_changes(conn, [_change("用户偏好中文回复")], evidence_key="b1")
            result = apply_memory_changes(
                conn,
                [_change("用户偏好英文回复", correction=True, conditions_clear=False)],
                evidence_key="b2",
            )

        assert result.outcomes[0].kind == "conflict"
        assert sorted(row.content for row in _rows(db)) == sorted(
            ["用户偏好英文回复", "用户偏好中文回复"]
        )


class TestMultiValueCoexistence:
    def test_different_predicate_coexists(self, db: MemoryDatabase):
        with db.connect() as conn:
            result = apply_memory_changes(
                conn,
                [
                    _change("用户偏好中文回复"),
                    _change(_memory("用户偏好深色主题", predicate="偏好主题")),
                ],
                evidence_key="b1",
            )

        assert [outcome.kind for outcome in result.outcomes] == ["added", "added"]
        assert len(_rows(db)) == 2

    def test_different_subject_coexists(self, db: MemoryDatabase):
        with db.connect() as conn:
            result = apply_memory_changes(
                conn,
                [
                    _change("项目 alpha 使用 pnpm"),
                    _change(_memory("项目 beta 使用 pnpm", subject="项目 beta")),
                ],
                evidence_key="b1",
            )

        assert [outcome.kind for outcome in result.outcomes] == ["added", "added"]
        assert len(_rows(db)) == 2


class TestAssistantNotPromoted:
    def test_assistant_suggestion_is_not_a_user_fact(self, db: MemoryDatabase):
        with db.connect() as conn:
            result = apply_memory_changes(
                conn,
                [_change("建议用户改用 pnpm 管理依赖", assistant_generated=True)],
                evidence_key="b1",
            )

        assert result.outcomes[0].kind == "not_promoted"
        assert _rows(db) == []

    def test_explicit_user_instruction_wins_over_assistant_flag(self, db: MemoryDatabase):
        with db.connect() as conn:
            result = apply_memory_changes(
                conn,
                [_change("用户偏好 pnpm", assistant_generated=True, explicit=True)],
                evidence_key="b1",
            )

        assert result.outcomes[0].kind == "added"
        assert len(_rows(db)) == 1


class TestForgetBlocksResurrection:
    def test_forget_expires_row_and_blocks_same_evidence(self, db: MemoryDatabase):
        with db.connect() as conn:
            apply_memory_changes(conn, [_change("用户喜欢咖啡")], evidence_key="batch-7")
        target = _rows(db)[0]
        with db.connect() as conn:
            forgotten = apply_memory_changes(
                conn,
                [MemoryChange(memory=_memory(""), action="forget", target_id=target.id)],
                evidence_key="batch-7",
            )

        assert forgotten.outcomes[0].kind == "forgotten"
        with db.connect() as conn:
            row = get_memory(conn, target.id)
            assert row is not None and row.expires_at
            assert is_evidence_suppressed(conn, workspace_id="default", evidence_key="batch-7")

        with db.connect() as conn:
            replay = apply_memory_changes(conn, [_change("用户喜欢咖啡")], evidence_key="batch-7")
        assert replay.outcomes[0].kind == "suppressed"
        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1

    def test_new_evidence_key_can_remember_again(self, db: MemoryDatabase):
        with db.connect() as conn:
            apply_memory_changes(conn, [_change("用户喜欢咖啡")], evidence_key="batch-7")
        target = _rows(db)[0]
        with db.connect() as conn:
            apply_memory_changes(
                conn,
                [MemoryChange(memory=_memory(""), action="forget", target_id=target.id)],
                evidence_key="batch-7",
            )
            again = apply_memory_changes(
                conn,
                [_change("用户喜欢咖啡", explicit=True)],
                evidence_key="user-explicit-again",
            )

        assert again.outcomes[0].kind == "added"
        with db.connect() as conn:
            rows = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        assert rows == 2

    def test_forget_target_must_match_scope(self, db: MemoryDatabase):
        with db.connect() as conn:
            apply_memory_changes(
                conn,
                [_change(_memory("用户喜欢咖啡", workspace_id="ws-a"))],
                evidence_key="b1",
            )
        target = _rows(db)[0]
        with db.connect() as conn:
            result = apply_memory_changes(
                conn,
                [
                    MemoryChange(
                        memory=_memory("", workspace_id="default"),
                        action="forget",
                        target_id=target.id,
                    )
                ],
                evidence_key="b1",
            )

        assert result.outcomes[0].kind == "skipped"
        assert result.outcomes[0].reason == "forget_target_scope_mismatch"


class TestUpsertByEvidence:
    def test_same_evidence_key_updates_same_row(self, db: MemoryDatabase):
        with db.connect() as conn:
            first = apply_memory_changes(
                conn, [_change("用户偏好中文回复", action="upsert_by_evidence")],
                evidence_key="rule-1",
            )
            second = apply_memory_changes(
                conn, [_change("用户偏好英文回复", action="upsert_by_evidence")],
                evidence_key="rule-1",
            )

        rows = _rows(db)
        assert len(rows) == 1
        assert first.memory_ids == second.memory_ids
        assert second.outcomes[0].kind == "updated"
        assert rows[0].content == "用户偏好英文回复"


class TestTransactionOwnership:
    def test_writer_does_not_commit(self, db: MemoryDatabase):
        with db.connect() as conn:
            apply_memory_changes(conn, [_change("用户偏好中文回复")], evidence_key="b1")
            assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
            conn.rollback()

        with db.connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
