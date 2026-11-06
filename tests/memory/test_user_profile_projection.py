"""USER.md 投影（P0）：从 memories 表派生用户画像。

覆盖契约「删掉库里一条关于用户的记忆 → USER.md 少一行」，以及迁移、手写区、
动作词过滤、编译联动等边界。
"""

from __future__ import annotations

import uuid

import pytest

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.lifecycle import (
    USER_NOTE_BEGIN,
    USER_NOTE_PLACEHOLDER,
    USER_VIEW_BEGIN,
    USER_VIEW_EMPTY,
    USER_VIEW_END,
    MemoryLifecycle,
    _iso8601_now,
    classify_user_section,
    read_user_note,
    select_user_facts,
    user_note_id,
)
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import add_memory, delete_memory
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_ws(tmp_path):
    return tmp_path / "ws"


@pytest.fixture
def services(tmp_ws):
    tmp_ws.mkdir(parents=True)
    db = MemoryDatabase(tmp_ws)
    db.init_schema()
    return MemoryServices(workspace_id="test-ws", database=db)


@pytest.fixture
def lifecycle(services):
    MemoryLifecycle._instances.clear()
    MemoryLifecycle._last_refresh_iso.clear()
    MemoryLifecycle._last_refresh_trigger.clear()
    MemoryLifecycle._last_user_refresh_iso.clear()
    MemoryLifecycle._last_user_refresh_trigger.clear()
    return MemoryLifecycle("test-ws", services)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mem(
    content: str,
    mtype: MemoryType = MemoryType.FACT,
    *,
    importance: float = 0.7,
    subject: str = "",
    predicate: str = "",
) -> Memory:
    now = _iso8601_now()
    return Memory(
        id=str(uuid.uuid4()),
        content=content,
        created_at=now,
        updated_at=now,
        type=mtype,
        priority=MemoryPriority.LONG_TERM,
        source="extraction",
        importance_score=importance,
        subject=subject,
        predicate=predicate,
        # 真实库的默认值就是 global（抽取链路从不写 user）。
        scope="global",
        workspace_id="test-ws",
    )


def _seed(services: MemoryServices, *memories: Memory) -> None:
    with services.database.connect() as conn:
        for memory in memories:
            add_memory(conn, memory)


def _read(lifecycle: MemoryLifecycle) -> str:
    return lifecycle.user_file.read_text(encoding="utf-8")


def _user_file(lifecycle: MemoryLifecycle):
    return lifecycle.user_file


# ---------------------------------------------------------------------------
# 选择规则
# ---------------------------------------------------------------------------


class TestSelectUserFacts:
    def test_subject_alias_is_recalled(self, services):
        """「用户本人 / 我 / user」都算用户——精确等值是 openakita 踩过的坑。"""
        _seed(
            services,
            _mem("用户本人是后端工程师", subject="用户本人", predicate="是"),
            _mem("我住在杭州", subject="我", predicate="住在"),
            _mem("user prefers terse answers", subject="User", predicate="prefers"),
        )
        with services.database.connect() as conn:
            facts = select_user_facts(conn, workspace_id="test-ws")
        assert len(facts) == 3

    def test_empty_subject_falls_back_to_content_prefix(self, services):
        """历史库 subject 普遍为空，靠 content 主语开头兜底。"""
        _seed(
            services,
            _mem("用户偏好简洁回复"),
            _mem("项目 alpha-app 使用 Jenkins 部署", subject="项目 alpha-app"),
        )
        with services.database.connect() as conn:
            facts = select_user_facts(conn, workspace_id="test-ws")
        assert [m.content for m in facts] == ["用户偏好简洁回复"]

    def test_action_flow_is_excluded(self, services):
        """任务流水账不进画像（P1-7：删 py 文件那种）。"""
        _seed(
            services,
            _mem("用户希望删除工作区 py 文件"),
            _mem("用户偏好用 pnpm 管理依赖", predicate="偏好"),
        )
        with services.database.connect() as conn:
            facts = select_user_facts(conn, workspace_id="test-ws")
        assert [m.content for m in facts] == ["用户偏好用 pnpm 管理依赖"]

    def test_meta_observations_are_excluded(self, services):
        """AI 对配置文件的观察不是用户身份。"""
        _seed(
            services,
            _mem("用户的 USER.md 仍为未初始化的空白模板，所有字段均为占位符"),
            _mem("用户使用 Windows 系统"),
        )
        with services.database.connect() as conn:
            facts = select_user_facts(conn, workspace_id="test-ws")
        assert [m.content for m in facts] == ["用户使用 Windows 系统"]

    def test_skill_and_error_types_stay_in_memory_md(self, services):
        _seed(
            services,
            _mem("用户偏好详细汇报", MemoryType.PREFERENCE),
            _mem("用 spawn 派 researcher 更稳", MemoryType.SKILL),
            _mem("子 Agent 读到的文件可能与注入不一致", MemoryType.ERROR),
        )
        with services.database.connect() as conn:
            facts = select_user_facts(conn, workspace_id="test-ws")
        assert [m.content for m in facts] == ["用户偏好详细汇报"]

    def test_sections(self):
        assert classify_user_section(_mem("用户的称呼是老王", predicate="称呼")) == "基本信息"
        assert classify_user_section(_mem("用户常做的项目是知序", predicate="项目")) == "工作上下文"
        assert classify_user_section(_mem("用户偏好简洁回复", predicate="偏好")) == "协作偏好"
        # 都不命中 → 兜底「协作偏好」，不冒充客观事实。
        assert classify_user_section(_mem("用户周末爱爬山")) == "协作偏好"


# ---------------------------------------------------------------------------
# 导出与闭环
# ---------------------------------------------------------------------------


class TestRefreshUserMd:
    def test_writes_grouped_export(self, lifecycle, services):
        _seed(
            services,
            _mem("用户的称呼是老王", predicate="称呼"),
            _mem("用户偏好简洁回复", MemoryType.PREFERENCE, predicate="偏好"),
        )
        result = lifecycle.refresh_user_md_sync("test-ws")
        assert result["status"] == "ok"
        text = _read(lifecycle)
        assert USER_VIEW_BEGIN in text and USER_VIEW_END in text
        assert "## 基本信息" in text and "称呼是老王" in text
        assert "## 协作偏好" in text and "偏好简洁回复" in text
        assert USER_NOTE_BEGIN in text and USER_NOTE_PLACEHOLDER in text

    def test_delete_memory_removes_the_line(self, lifecycle, services):
        """P0 的核心闭环：删库里的记忆 → 文件里那行消失。"""
        doomed = _mem("用户的称呼是老王", predicate="称呼")
        _seed(services, doomed, _mem("用户偏好简洁回复", predicate="偏好"))
        lifecycle.refresh_user_md_sync("test-ws")
        assert "称呼是老王" in _read(lifecycle)

        with services.database.connect() as conn:
            delete_memory(conn, doomed.id)
        lifecycle.refresh_user_md_sync("test-ws")

        text = _read(lifecycle)
        assert "称呼是老王" not in text
        assert "偏好简洁回复" in text

    def test_second_refresh_skips_and_keeps_mtime(self, lifecycle, services):
        _seed(services, _mem("用户偏好简洁回复", predicate="偏好"))
        lifecycle.refresh_user_md_sync("test-ws")
        before = _user_file(lifecycle).stat().st_mtime_ns

        result = lifecycle.refresh_user_md_sync("test-ws")
        assert result["status"] == "skipped"
        assert _user_file(lifecycle).stat().st_mtime_ns == before

    def test_empty_profile_renders_factory_template(self, lifecycle, services):
        """一条画像都没有时回到出厂模板：编译侧据此「未定制」跳过注入。"""
        from nanobot.utils.helpers import load_bundled_template

        lifecycle.refresh_user_md_sync("test-ws")
        text = _read(lifecycle)
        assert text.strip() == (load_bundled_template("USER.md") or "").strip()

    def test_note_area_survives_refresh(self, lifecycle, services):
        with services.database.connect() as conn:
            from nanobot.memory.lifecycle import save_user_note

            save_user_note(conn, workspace_id="test-ws", content="不吃香菜，别推荐")
        _seed(services, _mem("用户偏好简洁回复", predicate="偏好"))

        lifecycle.refresh_user_md_sync("test-ws")
        first = _read(lifecycle)
        lifecycle.refresh_user_md_sync("test-ws")
        second = _read(lifecycle)

        assert "不吃香菜，别推荐" in first
        assert first == second

    def test_write_user_md_saves_note_only(self, lifecycle, services):
        """手工保存：手写区进库，自动区被重算覆盖。"""
        _seed(services, _mem("用户偏好简洁回复", predicate="偏好"))
        lifecycle.refresh_user_md_sync("test-ws")

        forged = _read(lifecycle)
        forged = forged.replace("偏好简洁回复", "偏好简洁回复（手改应被覆盖）")
        forged = forged.replace(USER_NOTE_PLACEHOLDER, "只在周五发周报")
        lifecycle.write_user_md(forged)

        result = _read(lifecycle)
        assert "只在周五发周报" in result
        assert "手改应被覆盖" not in result
        with services.database.connect() as conn:
            note = read_user_note(conn, "test-ws")
        assert note is not None and "只在周五发周报" in note.content

    def test_write_user_md_truncation_guard(self, lifecycle):
        with pytest.raises(ValueError):
            lifecycle.write_user_md("x" * 5000)

    def test_long_note_does_not_starve_facts(self, lifecycle, services):
        """画像优先于手写区：一份长笔记不许把自动区挤成「暂无记录」。"""
        from nanobot.memory.lifecycle import save_user_note

        with services.database.connect() as conn:
            save_user_note(
                conn,
                workspace_id="test-ws",
                content="\n".join(f"手写第{i}行：一些很长很长的补充说明" for i in range(80)),
            )
        _seed(services, _mem("用户偏好简洁回复", predicate="偏好"))

        lifecycle.refresh_user_md_sync("test-ws")

        text = _read(lifecycle)
        assert len(text) <= 1200 + 200  # 上限 + 截断提示的余量
        assert "偏好简洁回复" in text
        assert USER_VIEW_EMPTY not in text

    def test_meta_observation_kept_out_of_core_view(self, services):
        """核心视图每轮进 prompt：AI 对配置文件的观察不该出现在里面。"""
        from nanobot.memory.lifecycle import select_core_facts

        _seed(
            services,
            _mem("用户的 USER.md 仍为未初始化的空白模板，所有字段均为占位符"),
            _mem("用户使用 Windows 系统"),
        )
        with services.database.connect() as conn:
            facts = select_core_facts(conn, workspace_id="test-ws")
        assert [m.content for m in facts] == ["用户使用 Windows 系统"]


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------


class TestLegacyMigration:
    def test_factory_template_not_migrated(self, lifecycle, services):
        from nanobot.utils.helpers import load_bundled_template

        lifecycle.user_file_legacy.parent.mkdir(parents=True, exist_ok=True)
        lifecycle.user_file_legacy.write_text(
            load_bundled_template("USER.md") or "", encoding="utf-8"
        )
        _seed(services, _mem("用户偏好简洁回复", predicate="偏好"))

        lifecycle.refresh_user_md_sync("test-ws")

        with services.database.connect() as conn:
            assert read_user_note(conn, "test-ws") is None
        assert "（待填）" not in _read(lifecycle)

    def test_legacy_handwritten_content_becomes_review_note(self, lifecycle, services):
        lifecycle.user_file_legacy.parent.mkdir(parents=True, exist_ok=True)
        lifecycle.user_file_legacy.write_text(
            "# 关于我\n\n我是老王，做后端的，别给我推荐电影。\n", encoding="utf-8"
        )
        _seed(services, _mem("用户偏好简洁回复", predicate="偏好"))

        lifecycle.refresh_user_md_sync("test-ws")

        with services.database.connect() as conn:
            note = read_user_note(conn, "test-ws")
        assert note is not None
        assert note.id == user_note_id("test-ws")
        assert note.metadata.get("needs_review") is True
        assert "我是老王" in note.content
        # 旧稿内容没有丢：它作为待核对笔记显示在文件里。
        assert "我是老王" in _read(lifecycle)

    def test_migration_is_idempotent(self, lifecycle, services):
        lifecycle.user_file_legacy.parent.mkdir(parents=True, exist_ok=True)
        lifecycle.user_file_legacy.write_text("# 关于我\n\n我是老王\n", encoding="utf-8")

        lifecycle.refresh_user_md_sync("test-ws")
        first = _read(lifecycle)
        lifecycle.refresh_user_md_sync("test-ws")

        assert _read(lifecycle) == first

    def test_own_export_is_not_treated_as_legacy(self, lifecycle, services):
        """第二次刷新不能把上一轮的导出当成手写旧稿搬进笔记。"""
        _seed(services, _mem("用户偏好简洁回复", predicate="偏好"))
        lifecycle.refresh_user_md_sync("test-ws")
        lifecycle.refresh_user_md_sync("test-ws")

        with services.database.connect() as conn:
            assert read_user_note(conn, "test-ws") is None


# ---------------------------------------------------------------------------
# 编译联动
# ---------------------------------------------------------------------------


class TestCompileLinkage:
    def test_compiled_profile_updated(self, lifecycle, services):
        from nanobot.identity.compiler import runtime_dir

        _seed(services, _mem("用户偏好简洁回复", MemoryType.PREFERENCE, predicate="偏好"))
        lifecycle.refresh_user_md_sync("test-ws")

        product = runtime_dir(lifecycle.workspace) / "user.profile.core.md"
        assert product.is_file()
        compiled = product.read_text(encoding="utf-8")
        assert "偏好简洁回复" in compiled
        # 区块标记与空态提示都不该进 system prompt。
        assert "<!--" not in compiled
        assert "暂无" not in compiled


# ---------------------------------------------------------------------------
# 文件模式：USER.md 不再从记忆正文推导
# ---------------------------------------------------------------------------


class TestFileModeUserMd:
    def test_file_mode_skips_user_md_derivation(self, tmp_ws, services):
        MemoryLifecycle._instances.clear()
        lifecycle = MemoryLifecycle("test-ws", services, file_mode=True)
        result = lifecycle.refresh_user_md_sync("test-ws")
        assert result == {"status": "skipped", "reason": "file_mode"}
