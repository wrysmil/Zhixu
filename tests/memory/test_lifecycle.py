"""Tests for MemoryLifecycle: 核心视图导出、core-note 迁移与保存。"""

from __future__ import annotations

import uuid

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.filters import content_hash_legacy
from nanobot.memory.lifecycle import (
    CORE_NOTE_PLACEHOLDER,
    CORE_NOTE_REVIEW_MARK,
    CORE_NOTE_SOURCE,
    CORE_VIEW_BEGIN,
    CORE_VIEW_END,
    MEMORY_MD_MAX_CHARS,
    MemoryLifecycle,
    _iso8601_now,
    core_note_id,
    read_core_note,
    select_core_facts,
)
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_ws(tmp_path):
    """Temporary workspace path."""
    return tmp_path / "ws"


@pytest.fixture
def services(tmp_ws):
    """MemoryServices for the temp workspace."""
    tmp_ws.mkdir(parents=True)
    db = MemoryDatabase(tmp_ws)
    db.init_schema()
    return MemoryServices(workspace_id="test-ws", database=db)


@pytest.fixture
def lifecycle(services):
    """Fresh MemoryLifecycle instance per test."""
    MemoryLifecycle._instances.clear()
    MemoryLifecycle._last_refresh_iso.clear()
    MemoryLifecycle._last_refresh_trigger.clear()
    lc = MemoryLifecycle("test-ws", services)
    lc.memory_dir.mkdir(parents=True, exist_ok=True)
    return lc


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _mem(
    content: str,
    mtype: MemoryType,
    importance: float = 0.5,
    scope: str = "user",
    **overrides,
) -> Memory:
    now = _iso8601_now()
    base = dict(
        id=str(uuid.uuid4()),
        content=content,
        created_at=now,
        updated_at=now,
        type=mtype,
        priority=MemoryPriority.LONG_TERM,
        source="manual",
        importance_score=importance,
        tags=[],
        subject="",
        predicate="",
        scope=scope,
        workspace_id="test-ws",
    )
    base.update(overrides)
    return Memory(**base)


def _seed(services: MemoryServices, *memories: Memory) -> None:
    with services.database.connect() as conn:
        for m in memories:
            add_memory(conn, m)


# ---------------------------------------------------------------------------
# content_hash_legacy
# ---------------------------------------------------------------------------


class TestContentHashLegacy:
    def test_same_content_same_hash(self):
        assert content_hash_legacy("用户偏好 Python") == content_hash_legacy("用户偏好 Python")

    def test_different_content_different_hash(self):
        assert content_hash_legacy("内容A") != content_hash_legacy("内容B")

    def test_whitespace_stripped(self):
        assert content_hash_legacy("内容") == content_hash_legacy("  内容  ")

    def test_returns_sha1_hex(self):
        h = content_hash_legacy("test")
        assert len(h) == 40
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# select_core_facts（核心视图选择规则）
# ---------------------------------------------------------------------------


class TestSelectCoreFacts:
    def test_scope_is_not_a_filter(self, services):
        """``scope`` 是可见性轴，不是「是否关于用户」的标记。

        回归防护：D01-D13 之后 ``Memory.scope`` 默认 ``global``、抽取链路从不写
        ``user``，若仍按 ``scope="user"`` 过滤，核心视图恒为空——注入侧
        ``_load_core_hits`` 一起变成死代码。这条用例钉住「不再按 scope 过滤」。
        """
        _seed(
            services,
            _mem("用户记忆", MemoryType.FACT, scope="user"),
            _mem("Agent记忆", MemoryType.FACT, scope="agent"),
            _mem("Global记忆", MemoryType.FACT, scope="global"),
        )
        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws")
        assert sorted(m.content for m in facts) == ["Agent记忆", "Global记忆", "用户记忆"]

    def test_manual_notes_never_count_as_facts(self, services):
        """手工笔记（core-note / user-note）不是「自动派生事实」。"""
        from nanobot.memory.lifecycle import core_note_id, save_core_note, save_user_note

        _seed(services, _mem("普通事实", MemoryType.FACT, importance=0.9))
        with services.database.connect() as conn:
            save_core_note(conn, workspace_id="test-ws", content="手工笔记正文")
            save_user_note(conn, workspace_id="test-ws", content="画像手写正文")
            facts = select_core_facts(conn, workspace_id="test-ws", include_note=False)

        ids = {m.id for m in facts}
        assert core_note_id("test-ws") not in ids
        assert [m.content for m in facts] == ["普通事实"]

    def test_min_importance_threshold(self, services):
        _seed(
            services,
            _mem("高重要性", MemoryType.FACT, importance=0.8),
            _mem("低重要性", MemoryType.FACT, importance=0.3),
        )
        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws")
        assert [m.content for m in facts] == ["高重要性"]

    def test_superseded_and_expired_excluded(self, services):
        replacement = _mem("替代后的事实", MemoryType.FACT)
        _seed(
            services,
            replacement,
            _mem("当前事实", MemoryType.FACT),
            _mem("已被替代", MemoryType.FACT, superseded_by=replacement.id),
            _mem("已过期", MemoryType.FACT, expires_at="2000-01-01T00:00:00Z"),
            _mem("未来过期", MemoryType.FACT, expires_at="2999-01-01T00:00:00Z"),
        )
        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws")
        assert sorted(m.content for m in facts) == ["当前事实", "替代后的事实", "未来过期"]

    def test_duplicate_content_collapsed(self, services):
        _seed(
            services,
            _mem("相同内容", MemoryType.FACT, importance=0.3),
            _mem("相同内容", MemoryType.FACT, importance=0.9),
        )
        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws")
        assert len(facts) == 1

    def test_limit_applies(self, services):
        _seed(services, *(_mem(f"事实{i}", MemoryType.FACT, importance=0.9) for i in range(5)))
        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws", limit=2)
        assert len(facts) == 2


# ---------------------------------------------------------------------------
# refresh_memory_md_sync（导出）
# ---------------------------------------------------------------------------


class TestRefreshMemoryMdSync:
    def test_empty_database_exports_empty_core_view(self, lifecycle):
        result = lifecycle.refresh_memory_md_sync("test-ws")

        assert result["status"] == "ok"
        content = lifecycle.memory_file.read_text(encoding="utf-8")
        assert content.startswith("# 核心记忆")
        assert CORE_VIEW_BEGIN in content and CORE_VIEW_END in content
        assert CORE_NOTE_PLACEHOLDER in content

    def test_second_refresh_is_skipped_without_rewrite(self, lifecycle, services):
        _seed(services, _mem("测试内容", MemoryType.FACT))
        assert lifecycle.refresh_memory_md_sync("test-ws")["status"] == "ok"

        result = lifecycle.refresh_memory_md_sync("test-ws")

        assert result == {"status": "skipped", "reason": "no_change"}

    def test_all_current_valid_facts_are_written(self, lifecycle, services):
        """导出不按 scope 收窄：真实库里 scope 恒为 global（见 select_core_facts）。"""
        _seed(
            services,
            _mem("用户记忆", MemoryType.FACT, scope="user"),
            _mem("全局记忆", MemoryType.FACT, scope="global"),
        )
        lifecycle.refresh_memory_md_sync("test-ws")

        content = lifecycle.memory_file.read_text(encoding="utf-8")
        assert "用户记忆" in content
        assert "全局记忆" in content

    def test_grouped_by_type(self, lifecycle, services):
        _seed(
            services,
            _mem("不要删除文件", MemoryType.RULE, importance=0.9),
            _mem("偏好中文回答", MemoryType.PREFERENCE, importance=0.8),
            _mem("用户用 Windows", MemoryType.FACT, importance=0.7),
        )
        lifecycle.refresh_memory_md_sync("test-ws")

        content = lifecycle.memory_file.read_text(encoding="utf-8")
        assert "## 规则\n- 不要删除文件" in content
        assert "## 偏好\n- 偏好中文回答" in content
        assert "## 事实\n- 用户用 Windows" in content

    def test_updates_last_refresh_stats(self, lifecycle, services):
        _seed(services, _mem("测试", MemoryType.FACT))
        lifecycle.refresh_memory_md_sync("test-ws")

        assert "test-ws" in MemoryLifecycle._last_refresh_iso
        assert MemoryLifecycle._last_refresh_trigger["test-ws"] == "manual"

    def test_export_stays_under_char_limit(self, lifecycle, services):
        _seed(
            services,
            *(
                _mem("很长的记忆内容" * 100 + str(i), MemoryType.FACT, importance=0.9)
                for i in range(6)
            ),
        )
        lifecycle.refresh_memory_md_sync("test-ws")

        content = lifecycle.memory_file.read_text(encoding="utf-8")
        assert len(content) <= MEMORY_MD_MAX_CHARS
        assert "已省略" in content


# ---------------------------------------------------------------------------
# write_memory_md + core-note
# ---------------------------------------------------------------------------


class TestCoreNote:
    def test_manual_write_stores_core_note(self, lifecycle, services):
        _seed(services, _mem("自动事实", MemoryType.FACT))

        lifecycle.write_memory_md("## 手工笔记\n我更喜欢简短回答")

        with services.database.connect() as conn:
            note = read_core_note(conn, "test-ws")
        assert note is not None
        assert note.content == "我更喜欢简短回答"
        assert note.source == CORE_NOTE_SOURCE
        assert note.scope == "user"
        assert note.metadata["needs_review"] is False

    def test_core_note_updates_in_place(self, lifecycle, services):
        lifecycle.write_memory_md("第一版笔记")
        lifecycle.write_memory_md("第二版笔记")

        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws")
            notes = [m for m in facts if m.id == core_note_id("test-ws")]
        assert len(notes) == 1
        assert notes[0].content == "第二版笔记"

    def test_core_note_separated_from_auto_view(self, lifecycle, services):
        _seed(services, _mem("自动事实", MemoryType.FACT))
        lifecycle.write_memory_md("手工笔记正文")

        content = lifecycle.memory_file.read_text(encoding="utf-8")
        note_start = content.index("zhixu:core-note:begin")
        assert content.index("自动事实") < note_start
        assert content.index("手工笔记正文") > note_start

    def test_core_note_injected_only_after_review(self, lifecycle, services):
        with services.database.connect() as conn:
            from nanobot.memory.lifecycle import save_core_note

            save_core_note(conn, workspace_id="test-ws", content="待核对旧稿", needs_review=True)

        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws")
            assert all(not m.content.startswith("待核对") for m in facts)
            exported = select_core_facts(conn, workspace_id="test-ws", include_note=False)
            assert exported == []

    def test_clearing_manual_region_deletes_note(self, lifecycle, services):
        lifecycle.write_memory_md("先写一条笔记")
        content = lifecycle.memory_file.read_text(encoding="utf-8")
        cleared = content.replace("先写一条笔记", CORE_NOTE_PLACEHOLDER)

        lifecycle.write_memory_md(cleared)

        with services.database.connect() as conn:
            assert read_core_note(conn, "test-ws") is None

    def test_saving_export_keeps_existing_note(self, lifecycle, services):
        _seed(services, _mem("自动事实", MemoryType.FACT))
        lifecycle.write_memory_md("手工笔记正文")
        exported = lifecycle.memory_file.read_text(encoding="utf-8")

        lifecycle.write_memory_md(exported)

        with services.database.connect() as conn:
            note = read_core_note(conn, "test-ws")
        assert note is not None and note.content == "手工笔记正文"

    def test_oversize_write_rejected(self, lifecycle):
        with pytest.raises(ValueError):
            lifecycle.write_memory_md("x" * (MEMORY_MD_MAX_CHARS + 1))

    def test_non_string_rejected(self, lifecycle):
        with pytest.raises(ValueError):
            lifecycle.write_memory_md(123)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 旧稿迁移
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    def test_legacy_handwritten_kept_as_unconfirmed_note(self, lifecycle, services):
        _seed(services, _mem("自动事实", MemoryType.FACT))
        lifecycle.memory_file.write_text("我以前手写的记忆：周三不开会\n", encoding="utf-8")

        lifecycle.refresh_memory_md_sync("test-ws")

        with services.database.connect() as conn:
            note = read_core_note(conn, "test-ws")
        assert note is not None
        assert note.metadata["needs_review"] is True
        content = lifecycle.memory_file.read_text(encoding="utf-8")
        assert CORE_NOTE_REVIEW_MARK in content
        assert "我以前手写的记忆" in content

    def test_generated_export_not_copied_into_note(self, lifecycle, services):
        _seed(
            services,
            _mem("用户用 Windows", MemoryType.FACT, importance=0.8),
            _mem("偏好中文回答", MemoryType.PREFERENCE, importance=0.7),
        )
        lifecycle.memory_file.write_text(
            "# 核心记忆\n\n## 偏好\n- 偏好中文回答\n\n## 事实\n- 用户用 Windows\n",
            encoding="utf-8",
        )

        lifecycle.refresh_memory_md_sync("test-ws")

        with services.database.connect() as conn:
            assert read_core_note(conn, "test-ws") is None
        content = lifecycle.memory_file.read_text(encoding="utf-8")
        assert CORE_NOTE_PLACEHOLDER in content

    def test_template_content_not_migrated(self, lifecycle):
        from nanobot.utils.helpers import load_bundled_template

        template = load_bundled_template("memory/MEMORY.md")
        assert template
        lifecycle.memory_file.write_text(template, encoding="utf-8")

        lifecycle.refresh_memory_md_sync("test-ws")

        with lifecycle.services.database.connect() as conn:
            assert read_core_note(conn, "test-ws") is None


# ---------------------------------------------------------------------------
# _safe_write_with_backup
# ---------------------------------------------------------------------------


class TestSafeWriteWithBackup:
    def test_write_creates_file(self, lifecycle, tmp_path):
        path = tmp_path / "test.md"
        lifecycle._safe_write_with_backup(path, "hello world")
        assert path.read_text() == "hello world"

    def test_backup_created(self, lifecycle, tmp_path):
        path = tmp_path / "test.md"
        path.write_text("original")
        lifecycle._safe_write_with_backup(path, "modified")
        backup = path.with_suffix(".md.bak")
        assert backup.read_text() == "original"
        assert path.read_text() == "modified"


# ---------------------------------------------------------------------------
# for_workspace singleton
# ---------------------------------------------------------------------------


class TestForWorkspace:
    def test_same_instance_on_repeated_calls(self, services):
        MemoryLifecycle._instances.clear()
        lc1 = MemoryLifecycle.for_workspace("ws-a", services)
        lc2 = MemoryLifecycle.for_workspace("ws-a", services)
        assert lc1 is lc2

    def test_different_workspaces_different_instances(self, tmp_ws, services):
        MemoryLifecycle._instances.clear()
        services_b = MemoryServices.for_workspace("ws-b", tmp_ws)
        lc1 = MemoryLifecycle.for_workspace("ws-a", services)
        lc2 = MemoryLifecycle.for_workspace("ws-b", services_b)
        assert lc1 is not lc2
