"""Tests for ScratchpadWriter (plan §6 T0 路径)。"""

from pathlib import Path

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.repository import get_scratchpad
from nanobot.memory.scratchpad_writer import (
    MAX_ACTIVE_PROJECTS,
    ScratchpadWriter,
    _normalize_focus_entry,
)


@pytest.fixture
def db(tmp_path: Path) -> MemoryDatabase:
    database = MemoryDatabase(tmp_path)
    database.init_schema()
    return database


# ------------------------------------------------------------------
# ScratchpadWriter.__init__
# ------------------------------------------------------------------

class TestScratchpadWriterInit:
    def test_user_id_is_required(self, db):
        """FIX-1: 缺 user_id 抛出 TypeError（防止串数据到默认用户）。"""
        with pytest.raises(TypeError):
            ScratchpadWriter(db)  # type: ignore[call-arg]

    def test_custom_user_workspace(self, db):
        writer = ScratchpadWriter(db, user_id="alice", workspace_id="proj-x")
        assert writer.user_id == "alice"
        assert writer.workspace_id == "proj-x"


# ------------------------------------------------------------------
# FIX-1 Sec-C-α: 不同 session_key 写入独立 row
# ------------------------------------------------------------------


class TestScratchpadWriterPerSessionKey:
    """FIX-1: 不同 session_key 写入后应产生独立 scratchpad 行（用 chat_id 区分）。"""

    def test_update_focus_isolates_per_session_key(self, db):
        """两个不同 session_key 写 focus，scratchpad 表产生两行独立 row。"""
        import asyncio

        async def _run() -> None:
            writer_a = ScratchpadWriter(
                db,
                user_id=ScratchpadWriter.user_id_for_key("telegram:chat-alice"),
                workspace_id="ws",
            )
            writer_b = ScratchpadWriter(
                db,
                user_id=ScratchpadWriter.user_id_for_key("telegram:chat-bob"),
                workspace_id="ws",
            )
            await writer_a.update_focus("Alice 的任务")
            await writer_b.update_focus("Bob 的任务")

        asyncio.run(_run())

        with db.connect() as conn:
            alice_entry = get_scratchpad(conn, "chat-alice", "ws")
            bob_entry = get_scratchpad(conn, "chat-bob", "ws")
            count = conn.execute("SELECT COUNT(*) FROM scratchpad").fetchone()[0]

        assert count == 2
        assert alice_entry is not None
        assert bob_entry is not None
        assert alice_entry.current_focus == "Alice 的任务"
        assert bob_entry.current_focus == "Bob 的任务"
        assert alice_entry.user_id == "chat-alice"
        assert bob_entry.user_id == "chat-bob"

    def test_user_id_for_key_parses_channel_chat_id(self):
        """channel:chat_id 格式的 session_key 解析 user_id 为 chat_id。"""
        assert ScratchpadWriter.user_id_for_key("telegram:chat-123") == "chat-123"
        assert ScratchpadWriter.user_id_for_key("discord:user-abc") == "user-abc"

    def test_user_id_for_key_falls_back_for_unknown_format(self):
        """无 ':' 分隔符时回落到整体 session_key（向后兼容）。"""
        assert ScratchpadWriter.user_id_for_key("plain-session") == "plain-session"


# ------------------------------------------------------------------
# update_focus
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestUpdateFocus:
    async def test_update_focus_sets_current_focus(self, db):
        """update_focus 后 get_scratchpad 读到 current_focus。"""
        writer = ScratchpadWriter(db, user_id="default")
        await writer.update_focus(new_focus="Implement authentication")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Implement authentication"

    async def test_update_focus_truncates_long_focus(self, db):
        """new_focus 超过 200 字符时被截断。"""
        writer = ScratchpadWriter(db, user_id="default")
        long_focus = "x" * 500
        await writer.update_focus(new_focus=long_focus)

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert len(entry.current_focus) == 200
        assert entry.current_focus == "x" * 200

    async def test_update_focus_rolls_old_focus_to_active_projects(self, db):
        """旧 focus 入 active_projects，带日期前缀。"""
        writer = ScratchpadWriter(db, user_id="default")

        # 第一次更新
        await writer.update_focus(new_focus="First task")
        # 第二次更新
        await writer.update_focus(new_focus="Second task")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Second task"
        assert len(entry.active_projects) == 1
        # 验证带日期前缀格式：[MM-DD HH:MM]
        project = entry.active_projects[0]
        import re

        assert re.match(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] First task$", project)

    async def test_update_focus_same_focus_no_duplicate(self, db):
        """两次相同 focus 不产生重复 active_projects 条目。"""
        writer = ScratchpadWriter(db, user_id="default")

        await writer.update_focus(new_focus="Same task")
        await writer.update_focus(new_focus="Same task")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Same task"
        # 相同 focus 不归档，所以 active_projects 应为空
        assert entry.active_projects == []

    async def test_update_focus_empty_to_non_empty(self, db):
        """从空 focus 更新到非空，不产生 active_projects 条目（无旧 focus 可归档）。"""
        writer = ScratchpadWriter(db, user_id="default")

        await writer.update_focus(new_focus="First focus")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "First focus"
        assert entry.active_projects == []

    async def test_active_projects_max_5_entries(self, db):
        """active_projects 最多 5 条，超出时移除最旧的。"""
        writer = ScratchpadWriter(db, user_id="default")

        # 更新 7 次，产生 6 个 historical 项目
        for i in range(7):
            await writer.update_focus(new_focus=f"Task {i}")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Task 6"
        assert len(entry.active_projects) == MAX_ACTIVE_PROJECTS
        # 最旧的 Task 0 应被移除
        assert "Task 0" not in entry.active_projects[0]
        # 最新的 Task 5 应在列表中
        assert any("Task 5" in p for p in entry.active_projects)


# ------------------------------------------------------------------
# update_focus 归档去重（active_projects 只有 MAX_ACTIVE_PROJECTS 格）
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestUpdateFocusArchiveDedupe:
    """旧 focus 归一化后若已在 active_projects 中，则不再重复归档。

    背景：用户围绕同一件事连着追问多轮时，每轮 user 消息都不同 → 每轮都触发
    一次归档 → 5 轮之内 5 个格子被同一话题的碎片占满，挤掉真实历史任务。
    """

    def _seed(
        self,
        db,
        *,
        current_focus: str = "",
        active_projects: list[str] | None = None,
    ) -> None:
        """预置一条 scratchpad 行，用于构造「旧 focus 已在列表中」的场景。"""
        from nanobot.memory.models import ScratchpadEntry
        from nanobot.memory.repository import upsert_scratchpad

        entry = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            current_focus=current_focus,
            active_projects=list(active_projects or []),
        )
        with db.connect() as conn:
            upsert_scratchpad(conn, entry)

    async def test_duplicate_focus_does_not_grow_active_projects(self, db):
        """连续 3 次调用，归一化后命中列表已有条目的那次不使列表变长。"""
        self._seed(
            db,
            current_focus="  Review   the   auth  flow  ",
            active_projects=["[09-10 15:30] Review the auth flow"],
        )
        writer = ScratchpadWriter(db, user_id="default")

        # 第 1 次：old_focus 归一化后等于列表里的条目 → 不归档（长度仍为 1）
        await writer.update_focus(new_focus="Ship the release")
        with db.connect() as conn:
            after_first = get_scratchpad(conn, "default", "default")
        assert after_first is not None
        assert len(after_first.active_projects) == 1
        assert after_first.current_focus == "Ship the release"

        # 第 2 次：不同 focus → 正常归档
        await writer.update_focus(new_focus="Write the RFC")
        with db.connect() as conn:
            after_second = get_scratchpad(conn, "default", "default")
        assert after_second is not None
        assert len(after_second.active_projects) == 2

        # 第 3 次：仍是不同 focus → 正常归档，长度继续增长
        await writer.update_focus(new_focus="Fix the flaky test")
        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")
        assert entry is not None
        assert len(entry.active_projects) == 3
        assert entry.current_focus == "Fix the flaky test"

    async def test_bounce_back_to_previous_focus_does_not_duplicate(self, db):
        """focus 在 A/B 间来回切换时，已归档的 A 不会被再次插入。"""
        writer = ScratchpadWriter(db, user_id="default")

        await writer.update_focus(new_focus="Task A")
        await writer.update_focus(new_focus="Task B")
        await writer.update_focus(new_focus="Task A")
        # 第四次：old_focus = "Task A"，已在 active_projects 中 → 不归档
        await writer.update_focus(new_focus="Task C")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.current_focus == "Task C"
        assert len(entry.active_projects) == 2
        assert [p.split("] ", 1)[1] for p in entry.active_projects] == ["Task B", "Task A"]

    async def test_case_difference_still_archives(self, db):
        """大小写不同视为不同 focus（精确匹配是刻意的设计选择）。"""
        self._seed(
            db,
            current_focus="Deploy The Service",
            active_projects=["[09-10 15:30] deploy the service"],
        )
        writer = ScratchpadWriter(db, user_id="default")

        await writer.update_focus(new_focus="Next thing")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        # 仅大小写不同 → 归一化不相等 → 照常归档
        assert len(entry.active_projects) == 2
        assert entry.active_projects[0].endswith("Deploy The Service")

    async def test_chinese_difference_still_archives(self, db):
        """中文字符不同视为不同 focus（不做同义/近似归并）。"""
        self._seed(
            db,
            current_focus="优化登录流程",
            active_projects=["[09-10 15:30] 优化登陆流程"],
        )
        writer = ScratchpadWriter(db, user_id="default")

        await writer.update_focus(new_focus="别的任务")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        # 「登录」vs「登陆」字面不同 → 照常归档，不做字形/语义近似匹配
        assert len(entry.active_projects) == 2
        assert entry.active_projects[0].endswith("优化登录流程")

    async def test_max_active_projects_cap_still_enforced(self, db):
        """去重不改变 [:MAX_ACTIVE_PROJECTS] 上限行为。"""
        writer = ScratchpadWriter(db, user_id="default")

        for i in range(7):
            await writer.update_focus(new_focus=f"Task {i}")

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert len(entry.active_projects) == MAX_ACTIVE_PROJECTS
        # 最旧的 Task 0 仍被挤出
        assert all("Task 0" not in p for p in entry.active_projects)


# ------------------------------------------------------------------
# _normalize_focus_entry
# ------------------------------------------------------------------

class TestNormalizeFocusEntry:
    def test_strips_timestamp_prefix(self):
        """剥离 [MM-DD HH:MM] 时间戳前缀。"""
        assert _normalize_focus_entry("[09-10 15:30] Review auth") == "Review auth"

    def test_strips_leading_and_trailing_whitespace(self):
        """忽略首尾空白。"""
        assert _normalize_focus_entry("  Review auth  ") == "Review auth"
        assert _normalize_focus_entry("\t\nReview auth\n") == "Review auth"

    def test_collapses_repeated_whitespace(self):
        """连续空白折叠成一个空格。"""
        assert _normalize_focus_entry("Review    the\t\tauth \n flow") == (
            "Review the auth flow"
        )

    def test_strips_prefix_before_collapsing(self):
        """前缀与空白叠加时结果一致。"""
        assert _normalize_focus_entry("  [09-10 15:30]   Review   auth  ") == (
            "Review auth"
        )

    def test_keeps_case_sensitive(self):
        """大小写不归并。"""
        assert _normalize_focus_entry("Review Auth") != _normalize_focus_entry(
            "review auth"
        )

    def test_keeps_chinese_exact(self):
        """中文字符不归并。"""
        assert _normalize_focus_entry("优化登录流程") != _normalize_focus_entry(
            "优化登陆流程"
        )

    def test_entry_without_prefix_is_preserved(self):
        """无时间戳前缀的条目原样归一化。"""
        assert _normalize_focus_entry("Review auth") == "Review auth"

    def test_empty_and_blank(self):
        """空串与纯空白归一化为空串，不抛异常。"""
        assert _normalize_focus_entry("") == ""
        assert _normalize_focus_entry("   \t ") == ""

    def test_does_not_strip_non_timestamp_brackets(self):
        """非 [MM-DD HH:MM] 形态的方括号不当作前缀剥离。"""
        assert _normalize_focus_entry("[WIP] Review auth") == "[WIP] Review auth"


# ------------------------------------------------------------------
# archive_completed
# ------------------------------------------------------------------

@pytest.mark.asyncio
class TestArchiveCompleted:
    async def test_archive_completed_removes_resolved(self, db):
        """resolved_items 从 open_questions 移除。"""
        # 先用 repository 写入一个带 open_questions 的 scratchpad
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1: Auth bug", "Q2: Perf issue", "Q3: UI glitch"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db, user_id="default")
        await writer.archive_completed(resolved_items=["Q1: Auth bug", "Q3: UI glitch"])

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q2: Perf issue"]

    async def test_archive_completed_empty_list_no_change(self, db):
        """resolved_items 为空列表时 open_questions 保持不变。"""
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db, user_id="default")
        await writer.archive_completed(resolved_items=[])

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q1"]

    async def test_archive_completed_no_matching_items(self, db):
        """resolved_items 中没有匹配项时 open_questions 完全保持。"""
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1: Auth bug", "Q2: Perf issue"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db, user_id="default")
        await writer.archive_completed(resolved_items=["Q9: Non-existent"])

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q1: Auth bug", "Q2: Perf issue"]

    async def test_archive_completed_partial_match(self, db):
        """部分匹配时只移除存在的项，其余保留。"""
        from nanobot.memory.models import ScratchpadEntry

        initial = ScratchpadEntry(
            user_id="default",
            workspace_id="default",
            updated_at="2026-09-10T00:00:00Z",
            open_questions=["Q1", "Q2", "Q3"],
        )
        with db.connect() as conn:
            from nanobot.memory.repository import upsert_scratchpad

            upsert_scratchpad(conn, initial)

        writer = ScratchpadWriter(db, user_id="default")
        await writer.archive_completed(resolved_items=["Q1", "Q99"])  # Q99 不存在

        with db.connect() as conn:
            entry = get_scratchpad(conn, "default", "default")

        assert entry is not None
        assert entry.open_questions == ["Q2", "Q3"]


# ------------------------------------------------------------------
# _format_project_entry
# ------------------------------------------------------------------

class TestFormatProjectEntry:
    def test_format_project_entry_with_date_prefix(self):
        """条目包含 [MM-DD HH:MM] 日期前缀。"""
        entry = ScratchpadWriter._format_project_entry("My task")
        import re

        assert re.match(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] My task$", entry)

    def test_format_project_entry_truncates_long_focus(self):
        """focus 超过 200 字符时被截断。"""
        long_focus = "x" * 300
        entry = ScratchpadWriter._format_project_entry(long_focus)
        # 日期前缀 "[MM-DD HH:MM] " 占 14 字符，focus 截断至 200
        # 总长 = 14 + 200 = 214
        assert len(entry) == 214
        assert entry.endswith("x" * 200)

    def test_format_project_entry_empty_focus(self):
        """空 focus 仍返回带前缀的条目（不报错）。"""
        entry = ScratchpadWriter._format_project_entry("")
        import re

        assert re.match(r"^\[\d{2}-\d{2} \d{2}:\d{2}\] $", entry)
