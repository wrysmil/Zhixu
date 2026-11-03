"""MEMORY.md / USER.md：核心视图的导出（契约 §5 / spec §7）。

真值只有一处——SQLite。两个 md 都是**派生产物**，同一套机制：

* 自动导出区：来自 SQLite 的当前有效事实；
* 手工笔记区：用户全文编辑的内容，存为 memories 里的一条内部笔记记录
  （按作用域唯一），与自动派生内容分开更新。

``MEMORY.md`` 导出「项目/规则/经验」类的核心事实；``USER.md`` 导出「关于用户
本人」的画像（基本信息 / 协作偏好 / 工作上下文）。两者共用同一把
``_derive_lock``、同一套 backup 写入与同一套迁移语义，只是筛选条件与渲染格式不同。

注入侧不看这些文件——每轮直接读 SQLite。文件是给人看、给 WebUI 编辑的视图。

Public API:
    MemoryLifecycle
"""

from __future__ import annotations

import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from loguru import logger

from nanobot.memory.clock import MemoryClock
from nanobot.memory.context import (
    CORE_NOTE_RECORD_ID_PREFIX,
    USER_NOTE_RECORD_ID_PREFIX,
    is_manual_note_record,
)
from nanobot.memory.filters import (
    content_hash_legacy,
    is_ai_self_talk,
    is_task_artifact,
)
from nanobot.memory.models import Memory, MemoryPriority, MemoryType
from nanobot.memory.repository import (
    add_memory,
    current_memory_sql,
    delete_memory,
    get_memory,
    list_memories,
    update_memory,
)
from nanobot.utils.helpers import load_bundled_template
from nanobot.webui.memory_services import MemoryServices

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MEMORY_MD_MAX_CHARS = 1500

#: 核心视图的旧「scope=user」规则已废弃（见 ``select_core_facts`` 的说明）。
#: 常量保留只为两类手工笔记的写入：它们是本系统自己造的内部记录，标 ``user``
#: 语义，不参与任何召回筛选。
CORE_SCOPE = "user"
CORE_MIN_IMPORTANCE = 0.5
#: 单次核心视图最多取多少条；扫描上限只为「有效/重复」过滤留余量。
CORE_MAX_ITEMS = 12
CORE_SCAN_LIMIT = 60
TOP_K_PER_SECTION = 4

#: 手工 core-note：按作用域唯一的内部记录，不是事实。
CORE_NOTE_SOURCE = "core_note"
CORE_NOTE_IMPORTANCE = 1.0
CORE_NOTE_PLACEHOLDER = "（暂无手工笔记）"
CORE_NOTE_REVIEW_MARK = "<!-- 待核对旧稿：确认内容无误后保存一次即转为正式手工笔记 -->"

CORE_VIEW_HEADING = "# 核心记忆"
CORE_VIEW_BEGIN = (
    "<!-- zhixu:core-view:begin 自动导出区：来自 SQLite 当前有效事实"
    "（未删除/未过期/未被替代），请勿手改 -->"
)
CORE_VIEW_END = "<!-- zhixu:core-view:end -->"
CORE_NOTE_BEGIN = (
    "<!-- zhixu:core-note:begin 手工笔记区：本区全文保存为 memory 库中的 "
    "core-note 记录，可自由编辑 -->"
)
CORE_NOTE_END = "<!-- zhixu:core-note:end -->"
CORE_NOTE_HEADING = "## 手工笔记"

_TRUNCATION_MARKER = "...(规则被截断)"

#: 超预算时的裁剪顺序（先砍的分值低）：经验/技能/教训 → 事实 → 偏好 → 规则。
#: P2 的预算要求是「规则类优先保留」——规则被事实挤掉等于模型忘了怎么做事。
_DROP_ORDER: dict[MemoryType, int] = {
    MemoryType.EXPERIENCE: 0,
    MemoryType.SKILL: 1,
    MemoryType.ERROR: 2,
    MemoryType.FACT: 3,
    MemoryType.PREFERENCE: 4,
    MemoryType.RULE: 5,
}


def _drop_index(facts: list[Memory]) -> int:
    """预算裁剪时最先该砍掉的下标：类型优先级最低、重要性最低、越靠后越先砍。"""
    return min(
        range(len(facts)),
        key=lambda i: (
            _DROP_ORDER.get(facts[i].type, 0),
            facts[i].importance_score,
            -i,
        ),
    )

# -- USER.md 导出（P0：与 MEMORY.md 同构的派生视图） --------------------------

#: 文件上限。比 MEMORY.md 宽松：它要装三个分区，且内容本来就是短句。
#: 真正的注入上限在编译层（``user_profile_core`` 600 字符），这里只是防止文件
#: 无限增长。
USER_MD_MAX_CHARS = 1200

USER_VIEW_HEADING = "# User Profile"
USER_VIEW_INTRO = "帮助知序了解你，以便用你舒服的方式协作。"
USER_VIEW_BEGIN = (
    "<!-- zhixu:user-auto:begin 自动导出区：来自 SQLite 当前有效记忆"
    "（未删除/未过期/未被替代），请勿手改 -->"
)
USER_VIEW_END = "<!-- zhixu:user-auto:end -->"
USER_NOTE_BEGIN = (
    "<!-- zhixu:user-note:begin 手写笔记区：本区全文保存为 memory 库中的 "
    "user-note 记录，可自由编辑 -->"
)
USER_NOTE_END = "<!-- zhixu:user-note:end -->"
USER_NOTE_HEADING = "## 特别说明"
USER_NOTE_PLACEHOLDER = "（暂无手写笔记）"
USER_NOTE_SOURCE = "user_note"
USER_VIEW_EMPTY = "（暂无记录：对话中了解到的信息会自动出现在这里）"

#: 用户画像只收这三类。RULE / SKILL / ERROR / EXPERIENCE 一律不进——
#: 它们是「怎么做事」，属于 MEMORY.md，塞进画像会把 600 字符预算挤爆。
_USER_FACT_TYPES: frozenset[MemoryType] = frozenset(
    {MemoryType.PREFERENCE, MemoryType.FACT, MemoryType.RULE}
)

#: ``subject`` 的别名集合。取值来源是抽取提示词里「用户」这个示例，但模型会
#: 自由发挥出「用户本人 / 我 / user」等写法；精确等值匹配（openakita 的
#: ``subject = '用户'``）会静默漏掉它们，这里统一 casefold 后按集合命中。
_USER_SUBJECT_ALIASES: frozenset[str] = frozenset(
    {"用户", "用户本人", "用户自己", "当前用户", "我", "user", "me", "the user"}
)

#: 历史数据里 ``subject`` 可能为空（抽取提示词早期版本没要求填）。空主语时
#: 退一步看 content 开头，命中这些前缀才认定为「关于用户」。
_USER_CONTENT_PREFIXES: tuple[str, ...] = ("用户", "我")

#: 动作词黑名单：防「[experience] 用户希望删除工作区 py 文件」这类任务流水账
#: 进画像。代价是「用户习惯用 VS Code 打开文件」也会被误杀——宁可漏，不可脏：
#: 画像里出现一条操作流水账，会让模型以为那是长期偏好。
_USER_ACTION_WORDS: tuple[str, ...] = (
    "打开", "关闭", "运行", "安装", "卸载", "部署", "创建", "新建",
    "删除", "修改", "搜索", "下载", "上传", "生成", "编写", "整理",
    "翻译", "截图", "帮我", "请你", "麻烦你",
)

#: 分桶规则：(分区名, 关键词)。**顺序即优先级**，第一个命中的桶胜出；
#: 都没命中时兜底落「协作偏好」——它是画像里最无害的一栏，把一条不确定的
#: 记忆放进去，比塞进「基本信息」冒充客观事实要诚实。
_USER_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "基本信息",
        (
            "称呼", "名字", "姓名", "昵称", "叫我", "时区", "所在地",
            "常住", "居住", "城市", "邮箱", "生日", "母语", "语言偏好",
        ),
    ),
    (
        "工作上下文",
        (
            "角色", "职位", "岗位", "职业", "工作领域", "项目", "技术栈",
            "框架", "工具", "编辑器", "ide", "操作系统", "公司", "团队",
            "行业", "编程语言", "windows", "macos", "linux", "ubuntu",
        ),
    ),
    (
        "协作偏好",
        (
            "偏好", "喜欢", "讨厌", "不喜欢", "习惯", "风格", "语气",
            "口吻", "回复", "答复", "简洁", "简短", "详细", "啰嗦",
            "解释", "emoji", "表情", "注释", "格式", "表格", "步骤",
            "确认", "提问",
        ),
    ),
)

_USER_FALLBACK_SECTION = "协作偏好"
#: 单分区条目上限。与 MEMORY.md 的 ``TOP_K_PER_SECTION`` 同量级，但画像是
#: 「越全越好」的地方，放宽到 8 条。
USER_TOP_K_PER_SECTION = 8
#: 候选扫描上限：给动作词过滤与去重留余量，取到的前 50 条里能活下来多少算多少。
USER_SCAN_LIMIT = 60
USER_MAX_ITEMS = 24


# Priority sections: order matters for display
_MEMORY_TYPE_TO_SECTION: dict[MemoryType, str] = {
    MemoryType.RULE: "规则",
    MemoryType.PREFERENCE: "偏好",
    MemoryType.FACT: "事实",
    MemoryType.ERROR: "教训",
    MemoryType.SKILL: "技能",
    MemoryType.EXPERIENCE: "经验",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso8601_now() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def core_note_id(workspace_id: str) -> str:
    """手工 core-note 的记录 id：由作用域派生，天然唯一。"""
    return f"{CORE_NOTE_RECORD_ID_PREFIX}{workspace_id}"


def user_note_id(workspace_id: str) -> str:
    """手工 user-note 的记录 id：同样由作用域派生，与 core-note 互不覆盖。"""
    return f"{USER_NOTE_RECORD_ID_PREFIX}{workspace_id}"


def _current_ids(conn: Any, ids: Iterable[str]) -> set[str]:
    """批量取出「当前有效」的子集。

    「有效」的谓词只认 ``repository.current_memory_sql``（契约 §3：SQL 候选与
    向量回表共用一处），这里不另写一套判定。
    """
    id_list = list(ids)
    if not id_list:
        return set()
    placeholders = ", ".join("?" for _ in id_list)
    rows = conn.execute(
        f"SELECT id FROM memories WHERE id IN ({placeholders}) "
        f"AND {current_memory_sql('memories')}",
        id_list,
    ).fetchall()
    return {str(row["id"]) for row in rows}


def select_core_facts(
    conn: Any,
    *,
    workspace_id: str,
    limit: int = CORE_MAX_ITEMS,
    include_note: bool = True,
) -> list[Memory]:
    """核心视图：当前有效、重要性达阈值的记忆。

    **不按 ``scope`` 过滤**（D01-D13 的回归修正）：``Memory.scope`` 的默认值是
    ``global``，而抽取链路从不写 ``scope='user'``，所以「scope=user + 重要性阈值」
    这套旧规则在 SQLite 落地后恒为空——核心视图、以及注入侧
    ``_load_core_hits`` 一起变成了死代码。本库的 ``scope`` 是可见性轴
    （global / agent / session），不是「是否关于用户」的标记，判据只能是
    workspace + 有效性 + 重要性；手工笔记另由 ``is_manual_note_record`` 排除。

    ``include_note`` 为真时把已确认的手工 core-note 放在最前面——它是用户
    维护的内容，与自动派生事实分开呈现；**待核对**旧稿不进注入（文件里可见，
    但不冒充事实）。
    """
    note = read_core_note(conn, workspace_id) if include_note else None
    if note is not None and note.metadata.get("needs_review"):
        note = None

    rows = list_memories(
        conn,
        workspace_id=workspace_id,
        min_importance=CORE_MIN_IMPORTANCE,
        order_by="importance",
        limit=CORE_SCAN_LIMIT,
    )
    candidates = [memory for memory in rows if not is_manual_note_record(memory.id)]
    valid: set[str] = _current_ids(conn, [memory.id for memory in candidates])

    picked: list[Memory] = []
    seen: set[tuple[MemoryType, str]] = set()
    for memory in candidates:
        if memory.id not in valid:
            continue
        # 「用户的 USER.md 仍是空白模板」这类对配置文件的观察不是核心事实；
        # 核心视图每轮都进 system prompt，放进去等于让模型引用一句自我描述。
        if _is_meta_observation(memory):
            continue
        key = (memory.type, content_hash_legacy(memory.content))
        if key in seen:
            continue
        seen.add(key)
        picked.append(memory)
        if len(picked) >= limit:
            break
    return ([note] if note is not None else []) + picked


def read_core_note(conn: Any, workspace_id: str) -> Memory | None:
    """按作用域读手工 core-note；没有则 ``None``。"""
    return get_memory(conn, core_note_id(workspace_id))


def save_core_note(
    conn: Any,
    *,
    workspace_id: str,
    content: str,
    needs_review: bool = False,
) -> Memory:
    """按作用域唯一地写入手工 core-note（存在即更新）。"""
    text = content.strip()
    metadata = {"kind": "core_note", "needs_review": bool(needs_review)}
    existing = read_core_note(conn, workspace_id)
    if existing is None:
        memory = Memory(
            id=core_note_id(workspace_id),
            content=text,
            created_at=_iso8601_now(),
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            source=CORE_NOTE_SOURCE,
            importance_score=CORE_NOTE_IMPORTANCE,
            scope=CORE_SCOPE,
            workspace_id=workspace_id,
            metadata=metadata,
        )
        add_memory(conn, memory)
        return memory
    update_memory(
        conn,
        existing.id,
        content=text,
        importance_score=CORE_NOTE_IMPORTANCE,
        metadata={**existing.metadata, **metadata},
    )
    return read_core_note(conn, workspace_id) or existing


def delete_core_note(conn: Any, workspace_id: str) -> None:
    """删除手工 core-note（用户清空手工区时调用）。"""
    existing = read_core_note(conn, workspace_id)
    if existing is not None:
        delete_memory(conn, existing.id)


# -- USER.md 侧的手工笔记 -----------------------------------------------------


def read_user_note(conn: Any, workspace_id: str) -> Memory | None:
    """按作用域读 USER.md 手写笔记；没有则 ``None``。"""
    return get_memory(conn, user_note_id(workspace_id))


def save_user_note(
    conn: Any,
    *,
    workspace_id: str,
    content: str,
    needs_review: bool = False,
) -> Memory:
    """按作用域唯一地写入 USER.md 手写笔记（存在即更新）。"""
    text = content.strip()
    metadata = {"kind": "user_note", "needs_review": bool(needs_review)}
    existing = read_user_note(conn, workspace_id)
    if existing is None:
        memory = Memory(
            id=user_note_id(workspace_id),
            content=text,
            created_at=_iso8601_now(),
            type=MemoryType.FACT,
            priority=MemoryPriority.LONG_TERM,
            source=USER_NOTE_SOURCE,
            importance_score=CORE_NOTE_IMPORTANCE,
            scope=CORE_SCOPE,
            workspace_id=workspace_id,
            metadata=metadata,
        )
        add_memory(conn, memory)
        return memory
    update_memory(
        conn,
        existing.id,
        content=text,
        importance_score=CORE_NOTE_IMPORTANCE,
        metadata={**existing.metadata, **metadata},
    )
    return read_user_note(conn, workspace_id) or existing


def delete_user_note(conn: Any, workspace_id: str) -> None:
    """删除 USER.md 手写笔记（用户清空手写区时调用）。"""
    existing = read_user_note(conn, workspace_id)
    if existing is not None:
        delete_memory(conn, existing.id)


def extract_user_note_text(content: str) -> str | None:
    """取出 USER.md 文本中手写区的内容；没有该区时返回 ``None``。"""
    start = content.find(USER_NOTE_BEGIN)
    end = content.find(USER_NOTE_END)
    if start < 0 or end < start:
        return None
    body = content[start + len(USER_NOTE_BEGIN): end]
    lines = [
        line for line in body.splitlines()
        if line.strip() and not line.strip().startswith("<!--")
        and line.strip() != USER_NOTE_HEADING
    ]
    text = "\n".join(lines).strip()
    if not text or text == USER_NOTE_PLACEHOLDER:
        return ""
    return text


# -- USER.md 的候选筛选 -------------------------------------------------------

#: 记忆自称「在讲配置文件」时一律不进核心视图 / 画像。这类记录是 AI 对
#: workspace 的观察（「用户的 USER.md 仍为未初始化模板」），不是关于用户或项目
#: 的事实；真实库里确实出现过，不挡就会被渲染成「用户是一名 USER.md」。
_USER_META_MARKERS: tuple[str, ...] = (
    "USER.md", "SOUL.md", "MEMORY.md", "AGENTS.md", "AGENT.md",
    "配置文件", "占位符", "空白模板",
)


def _is_meta_observation(memory: Memory) -> bool:
    """这条记忆是不是 AI 对身份文件本身的观察（而非用户/项目事实）。"""
    content = memory.content or ""
    return any(marker in content for marker in _USER_META_MARKERS)


def _is_about_user(memory: Memory) -> bool:
    """这条记忆是否在讲「用户本人」。

    ``subject`` 是首选判据，但真实库里大量历史记录 ``subject`` 为空（抽取提示词
    早期版本没要求填），所以空主语时退一步看 content 开头——抽取规范要求
    content 以主语开头（见 ``memory/prompts.py``）。
    """
    subject = (memory.subject or "").strip().casefold()
    if subject:
        if subject in _USER_SUBJECT_ALIASES:
            return True
        # 有主语但不是用户：这是「关于某个项目/文件」的记忆，不该进画像。
        return False
    return (memory.content or "").lstrip().startswith(_USER_CONTENT_PREFIXES)


def _looks_like_task_flow(memory: Memory) -> bool:
    """任务流水账 / AI 自述 / 系统观察：进画像会污染，直接挡在门外。"""
    content = memory.content or ""
    if _is_meta_observation(memory):
        return True
    if is_task_artifact(content) or is_ai_self_talk(content):
        return True
    probe = f"{memory.predicate or ''}{content[:20]}"
    return any(word in probe for word in _USER_ACTION_WORDS)


def classify_user_section(memory: Memory) -> str:
    """把一条记忆分到画像的三个分区之一；都不命中时落兜底分区。"""
    haystack = " ".join(
        part for part in (memory.predicate or "", memory.content or "") if part
    ).casefold()
    for section, keywords in _USER_SECTIONS:
        if any(keyword in haystack for keyword in keywords):
            return section
    return _USER_FALLBACK_SECTION


def select_user_facts(
    conn: Any,
    *,
    workspace_id: str,
    limit: int = USER_MAX_ITEMS,
) -> list[Memory]:
    """画像候选：当前有效、讲用户本人、非任务流水账的记忆。

    与 :func:`select_core_facts` 的三处有意差异：

    1. **类型收窄**到 FACT / PREFERENCE / RULE——SKILL / ERROR / EXPERIENCE 是
       「怎么做事」，归 MEMORY.md；
    2. **不做 scope 过滤**。``scope`` 在本库是可见性轴（现实数据几乎全是
       ``global``），拿它当「是不是用户画像」的判据会命中不了任何东西；
    3. **主语走别名集合 + 空主语回退看开头**，不用精确等值——openakita 在这个
       坑里漏掉了「用户本人 / 我 / user」。
    """
    rows = list_memories(
        conn,
        workspace_id=workspace_id,
        order_by="importance",
        limit=USER_SCAN_LIMIT,
    )
    valid = _current_ids(conn, [memory.id for memory in rows])

    picked: list[Memory] = []
    seen: set[str] = set()
    for memory in rows:
        if memory.id not in valid:
            continue
        if is_manual_note_record(memory.id):
            continue
        if memory.type not in _USER_FACT_TYPES:
            continue
        if not _is_about_user(memory) or _looks_like_task_flow(memory):
            continue
        digest = content_hash_legacy(memory.content)
        if digest in seen:
            continue
        seen.add(digest)
        picked.append(memory)
        if len(picked) >= limit:
            break
    return picked



def extract_core_note_text(content: str) -> str | None:
    """取出导出文本中手工笔记区的内容；没有该区时返回 ``None``。"""
    start = content.find(CORE_NOTE_BEGIN)
    end = content.find(CORE_NOTE_END)
    if start < 0 or end < start:
        return None
    body = content[start + len(CORE_NOTE_BEGIN): end]
    lines = [
        line for line in body.splitlines()
        if line.strip() and not line.strip().startswith("<!--")
        and line.strip() != CORE_NOTE_HEADING
    ]
    text = "\n".join(lines).strip()
    if not text or text == CORE_NOTE_PLACEHOLDER:
        return ""
    return text


def looks_like_generated_export(content: str, memories: Iterable[Memory]) -> bool:
    """判断一段旧稿是不是「自动导出」，而不是用户手写内容。

    首次迁移不许把当前的自动生成列表复制成手工笔记（契约 §5）。判据是
    确定性的：整份文本只由标题行与 ``- `` 条目组成，且每一条的内容都能在
    现有记忆里找到——这就是导出，不是手稿。
    """
    text = content.strip()
    if not text:
        return True
    if CORE_VIEW_BEGIN in text or CORE_NOTE_BEGIN in text:
        return True
    known = {memory.content.strip() for memory in memories if memory.content.strip()}
    body = [line.strip() for line in text.splitlines() if line.strip()]
    bullets = [line[2:].strip() for line in body if line.startswith("- ")]
    if not bullets:
        return False
    for bullet in bullets:
        if bullet.endswith(_TRUNCATION_MARKER):
            bullet = bullet[: -len(_TRUNCATION_MARKER)].strip()
        if not any(bullet and (bullet == known_text or bullet in known_text)
                   for known_text in known):
            return False
    return all(line.startswith("#") or line.startswith("- ") for line in body)


# ---------------------------------------------------------------------------
# MemoryLifecycle
# ---------------------------------------------------------------------------


class MemoryLifecycle:
    """Derives the MEMORY.md / USER.md exports of a workspace's memory store.

    Class-level state for the per-workspace singleton and the last-refresh
    stats the WebUI reads. Nothing here is on the chat read path: a stale or
    failed export never changes what the model sees.
    """

    # Per-workspace singletons
    _instances: dict[str, "MemoryLifecycle"] = {}

    # Export stats（WebUI 展示用；不参与任何缓存或注入决策）
    _last_refresh_iso: dict[str, str] = {}
    _last_refresh_trigger: dict[str, str] = {}
    _last_user_refresh_iso: dict[str, str] = {}
    _last_user_refresh_trigger: dict[str, str] = {}

    def __init__(
        self,
        workspace_id: str,
        services: MemoryServices,
        *,
        clock: MemoryClock | None = None,
    ) -> None:
        self.workspace_id = workspace_id
        self.services = services
        # 评测可注入的时钟；缺省时全部走真实时间。
        self._clock = clock
        self.workspace: Path = services.database.workspace
        self.memory_dir: Path = services.database.workspace / "memory"
        self.memory_file: Path = self.memory_dir / "MEMORY.md"
        # USER.md 的落点是 identity/：注入侧（agent/context.py）优先读这里，
        # 老 workspace 根的 USER.md 只作为迁移来源，不再被写。
        self.user_file: Path = self.workspace / "identity" / "USER.md"
        self.user_file_legacy: Path = self.workspace / "USER.md"
        # Instance-level lock: each workspace has its own lock.
        self._derive_lock = threading.Lock()

    def _now_iso(self) -> str:
        """当前 UTC 时间戳；注入 clock 时使用虚拟墙上时间。"""
        if self._clock is not None:
            return self._clock.wall_now().strftime("%Y-%m-%dT%H:%M:%SZ")
        return _iso8601_now()

    @classmethod
    def for_workspace(
        cls,
        workspace_id: str,
        services: MemoryServices,
        *,
        clock: MemoryClock | None = None,
    ) -> "MemoryLifecycle":
        """Return the singleton MemoryLifecycle for the given workspace."""
        if workspace_id not in cls._instances:
            cls._instances[workspace_id] = cls(workspace_id, services, clock=clock)
        return cls._instances[workspace_id]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def refresh_memory_md_sync(self, workspace_id: str) -> dict[str, Any]:
        """Re-export MEMORY.md from SQLite (on demand / after mutations).

        Thread-safe, backup-on-write, and failure-isolated: any error is
        logged and reported as ``{"status": "error"}``; callers of chat never
        depend on this file.
        """
        with self._derive_lock:
            try:
                with self.services.database.connect() as conn:
                    self._migrate_legacy_memory_md(conn)
                    facts = select_core_facts(
                        conn, workspace_id=workspace_id, include_note=False
                    )
                    note = read_core_note(conn, workspace_id)

                content = self._render_export(facts, note)
                if self.memory_file.exists() and self.memory_file.read_text(
                    encoding="utf-8"
                ) == content:
                    return {"status": "skipped", "reason": "no_change"}

                self._safe_write_with_backup(self.memory_file, content)
                self._last_refresh_iso[workspace_id] = self._now_iso()
                self._last_refresh_trigger[workspace_id] = "manual"
                return {"status": "ok", "chars": len(content)}

            except Exception:
                logger.exception(
                    "Failed to refresh MEMORY.md for workspace {}",
                    workspace_id,
                )
                return {"status": "error", "reason": "write_failed"}

    def write_memory_md(self, content: str) -> None:
        """接收用户对 MEMORY.md 的全文编辑（现有保存路由）。

        手工内容进 memories 里的 core-note 记录（按作用域唯一），随后文件按
        「自动导出区 + 手工笔记区」重新导出。长度超 ``MEMORY_MD_MAX_CHARS``
        时抛 ``ValueError``（由调用方转成 4xx）。

        迁移语义（契约 §5）：不含手工区标记的旧稿若无法确认，保留为**待核对**
        版本，不静默重写成事实；若它本来就是导出（或出厂模板），则不动手工笔记。
        """
        # 边界校验：内容来自 WebUI 的 JSON 载荷，不能只靠类型注解。
        if not isinstance(content, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("MEMORY.md 内容必须是字符串")
        if len(content) > MEMORY_MD_MAX_CHARS:
            raise ValueError(
                f"内容超过 {MEMORY_MD_MAX_CHARS} 字符上限（当前 {len(content)}）"
            )

        with self._derive_lock:
            try:
                with self.services.database.connect() as conn:
                    note_text, needs_review = self._resolve_saved_note(conn, content)
                    if note_text is None:
                        pass  # 保存的是导出版本/模板：手工笔记维持原样
                    elif note_text:
                        save_core_note(
                            conn,
                            workspace_id=self.workspace_id,
                            content=note_text,
                            needs_review=needs_review,
                        )
                    else:
                        delete_core_note(conn, self.workspace_id)
                    facts = select_core_facts(
                        conn, workspace_id=self.workspace_id, include_note=False
                    )
                    note = read_core_note(conn, self.workspace_id)

                self._safe_write_with_backup(
                    self.memory_file,
                    self._render_export(facts, note),
                )
                self._last_refresh_iso[self.workspace_id] = self._now_iso()
                self._last_refresh_trigger[self.workspace_id] = "manual"
            except ValueError:
                raise
            except Exception as exc:
                logger.exception(
                    "MEMORY.md manual write failed for workspace {}",
                    self.workspace_id,
                )
                raise OSError(f"MEMORY.md 写入失败: {exc}") from exc

            logger.info(
                "MEMORY.md manually written for workspace {} ({} chars)",
                self.workspace_id,
                len(content),
            )

    # -- USER.md -------------------------------------------------------

    def refresh_user_md_sync(
        self, workspace_id: str, *, trigger: str = "manual"
    ) -> dict[str, Any]:
        """Re-export USER.md from SQLite (on demand / after mutations).

        与 :meth:`refresh_memory_md_sync` 同一套语义：线程安全、写前备份、
        失败隔离、内容无变化时跳过（不刷新 mtime，避免白白击穿编译缓存）。

        与它的唯一差别是收尾：写完必须触发一次 ``compile_identity``，
        否则 ``identity/runtime/user.profile.core.md`` 不更新，模型下一轮看到
        的还是旧画像。
        """
        with self._derive_lock:
            try:
                with self.services.database.connect() as conn:
                    self._migrate_legacy_user_md(conn)
                    facts = select_user_facts(conn, workspace_id=workspace_id)
                    note = read_user_note(conn, workspace_id)

                content = self._render_user_export(facts, note)
                if self.user_file.exists() and self.user_file.read_text(
                    encoding="utf-8"
                ) == content:
                    return {"status": "skipped", "reason": "no_change", "items": len(facts)}

                self._safe_write_with_backup(self.user_file, content)
                self._last_user_refresh_iso[workspace_id] = self._now_iso()
                self._last_user_refresh_trigger[workspace_id] = trigger
            except Exception:
                logger.exception(
                    "Failed to refresh USER.md for workspace {}", workspace_id
                )
                return {"status": "error", "reason": "write_failed"}

        # 编译是本地毫秒级操作，放在锁外：即便它失败，写盘也已经成功，
        # 下一轮启动会补齐产物，不该让整次派生判失败。
        self._compile_profile()
        return {"status": "ok", "chars": len(content), "items": len(facts)}

    def write_user_md(self, content: str) -> None:
        """接收用户对 USER.md 的全文编辑（WebUI 保存路由）。

        手写区进 memories 里的 user-note 记录（按作用域唯一），随后文件按
        「自动导出区 + 手写区」重新导出。超 ``USER_MD_MAX_CHARS`` 时抛
        ``ValueError``（由调用方转成 4xx）。
        """
        if not isinstance(content, str):  # pyright: ignore[reportUnnecessaryIsInstance]
            raise ValueError("USER.md 内容必须是字符串")
        if len(content) > USER_MD_MAX_CHARS:
            raise ValueError(
                f"内容超过 {USER_MD_MAX_CHARS} 字符上限（当前 {len(content)}）"
            )

        with self._derive_lock:
            try:
                with self.services.database.connect() as conn:
                    note_text, needs_review = self._resolve_saved_user_note(conn, content)
                    if note_text is None:
                        pass  # 保存的是导出格式本身：手写笔记维持原样
                    elif note_text:
                        save_user_note(
                            conn,
                            workspace_id=self.workspace_id,
                            content=note_text,
                            needs_review=needs_review,
                        )
                    else:
                        delete_user_note(conn, self.workspace_id)
                    facts = select_user_facts(conn, workspace_id=self.workspace_id)
                    note = read_user_note(conn, self.workspace_id)

                self._safe_write_with_backup(
                    self.user_file, self._render_user_export(facts, note)
                )
                self._last_user_refresh_iso[self.workspace_id] = self._now_iso()
                self._last_user_refresh_trigger[self.workspace_id] = "manual"
            except ValueError:
                raise
            except Exception as exc:
                logger.exception(
                    "USER.md manual write failed for workspace {}", self.workspace_id
                )
                raise OSError(f"USER.md 写入失败: {exc}") from exc

        self._compile_profile()
        logger.info(
            "USER.md manually written for workspace {} ({} chars)",
            self.workspace_id,
            len(content),
        )

    def refresh_derived_sync(
        self, workspace_id: str, *, trigger: str = "mutation"
    ) -> dict[str, Any]:
        """一次刷新两个派生文件（mutation 之后的统一入口）。

        两个 ``refresh_*`` 各自持同一把不可重入锁，所以不能互相调用，只能顺序调。
        任一失败不影响另一个：画像写不出来不该拖累 MEMORY.md。
        """
        return {
            "memory_md": self.refresh_memory_md_sync(workspace_id),
            "user_md": self.refresh_user_md_sync(workspace_id, trigger=trigger),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _migrate_legacy_memory_md(self, conn: Any) -> None:
        """首次导出前把旧版全文迁移成 core-note / 待核对稿（契约 §5）。

        - 出厂模板、自动导出：不迁移（绝不把自动列表复制成手工笔记）；
        - 其它无法确认的旧稿：保留为待核对版本，不重写成事实。
        """
        if read_core_note(conn, self.workspace_id) is not None:
            return
        if not self.memory_file.exists():
            return
        try:
            legacy = self.memory_file.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "MEMORY.md migration skipped: unreadable file for workspace {}",
                self.workspace_id,
            )
            return
        if not legacy.strip() or self._is_template_content(legacy):
            return
        # A generated export may still carry a user-edited core-note section.
        # Preserve that section before the generic "looks like export" check,
        # otherwise a lost/never-written DB row is silently erased on refresh.
        note_text = extract_core_note_text(legacy)
        if note_text:
            save_core_note(
                conn,
                workspace_id=self.workspace_id,
                content=note_text,
                needs_review=True,
            )
            logger.info(
                "MEMORY.md core-note region recovered for workspace {}",
                self.workspace_id,
            )
            return
        facts = select_core_facts(conn, workspace_id=self.workspace_id, include_note=False)
        if looks_like_generated_export(legacy, facts):
            return
        save_core_note(
            conn,
            workspace_id=self.workspace_id,
            content=legacy,
            needs_review=True,
        )
        logger.info(
            "MEMORY.md legacy content kept as an unconfirmed core-note for workspace {}",
            self.workspace_id,
        )

    def _resolve_saved_note(
        self, conn: Any, content: str
    ) -> tuple[str | None, bool]:
        """把一次保存的全文解析成 ``(手工笔记正文, 是否待核对)``。

        ``None`` 表示「这不是对手工笔记的编辑」——保存的是导出版本本身或出厂
        模板，手工笔记维持原样；空串表示用户清空了手工区。

        保存动作本身就是用户的明确意图，所以这里一律视为**已确认**；
        「待核对」只来自旧稿迁移（见 ``_migrate_legacy_memory_md``）。
        """
        explicit = extract_core_note_text(content)
        if explicit is not None:
            return explicit, False
        if not content.strip() or self._is_template_content(content):
            return None, False
        facts = select_core_facts(conn, workspace_id=self.workspace_id, include_note=False)
        if looks_like_generated_export(content, facts):
            return None, False
        return _strip_note_heading(content), False

    def _is_template_content(self, content: str) -> bool:
        template = load_bundled_template("memory/MEMORY.md")
        return bool(template) and content.strip() == template.strip()

    # -- USER.md 内部实现 ----------------------------------------------

    def _compile_profile(self) -> None:
        """刷新 ``identity/runtime/user.profile.core.md``。

        局部 import：``identity.compiler`` → ``identity.store`` → ``identity.catalog``
        → 本模块，模块级导入会成环。
        """
        try:
            from nanobot.identity.compiler import compile_identity

            compile_identity(self.workspace)
        except Exception:
            logger.warning(
                "USER.md 派生后编译失败，产物留待下次启动补齐：workspace={}",
                self.workspace_id,
            )

    def _effective_user_file(self) -> Path | None:
        """USER.md 当前生效的那一份：identity/ 优先，老 workspace 根兜底。"""
        if self.user_file.is_file():
            return self.user_file
        if self.user_file_legacy.is_file():
            return self.user_file_legacy
        return None

    def _is_user_template_content(self, content: str) -> bool:
        """这版 USER.md 是不是「出厂模板」，没有值得迁移的用户内容。

        两代模板都要认：当前中文版逐字比对；**旧版英文模板**（``(your name)``
        那一版）只比标记——真实 workspace 里有大量旧文件，正文逐字对不上当前
        模板，但它们同样是没填过的空表。不认的话，第一次迁移会把整份英文脚手架
        当成「用户手稿」搬进笔记区，把画像全挤掉。
        """
        template = load_bundled_template("USER.md")
        if template and content.strip() == template.strip():
            return True
        return not _strip_legacy_template_noise(content)

    def _migrate_legacy_user_md(self, conn: Any) -> None:
        """首次导出前把旧 USER.md 转成手写笔记，不静默丢内容。

        三条判据与 MEMORY.md 的迁移完全对称：

        - 已经有手写笔记记录 → 迁移过了，直接返回（幂等）；
        - 文件不存在 / 空 / 出厂模板 → 没有旧稿，不迁移；
        - 其它 → 整份作为**待核对**笔记入库，并在文件里带一行待核对提示。
          不把它当成「事实」，因为它可能是上一版 Dream 自动写的，也可能
          混着模板占位符。
        """
        if read_user_note(conn, self.workspace_id) is not None:
            return
        path = self._effective_user_file()
        if path is None:
            return
        try:
            legacy = path.read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "USER.md migration skipped: unreadable file for workspace {}",
                self.workspace_id,
            )
            return
        if not legacy.strip() or self._is_user_template_content(legacy):
            return
        if USER_VIEW_BEGIN in legacy:
            # 已经是本函数自己的导出格式（第二次以后）：没有旧稿，别把导出
            # 当成手写笔记再搬一次。
            return
        save_user_note(
            conn,
            workspace_id=self.workspace_id,
            content=_strip_legacy_template_noise(legacy),
            needs_review=True,
        )
        logger.info(
            "USER.md legacy content kept as an unconfirmed user-note for workspace {}",
            self.workspace_id,
        )

    def _resolve_saved_user_note(
        self, conn: Any, content: str
    ) -> tuple[str | None, bool]:
        """把一次保存的全文解析成 ``(手写正文, 是否待核对)``。

        ``None`` = 这不算对手写区的编辑（保存的是导出版本 / 出厂模板），
        手写笔记维持原样；空串 = 用户清空了手写区。
        """
        explicit = extract_user_note_text(content)
        if explicit is not None:
            return explicit, False
        if not content.strip() or self._is_user_template_content(content):
            return None, False
        if USER_VIEW_BEGIN in content or content.lstrip().startswith(USER_VIEW_HEADING):
            # 是导出格式但手写区被整体删掉：视为「清空手写区」。
            return "", False
        return _strip_user_note_heading(content), False

    def _render_user_export(self, facts: list[Memory], note: Memory | None) -> str:
        """渲染 USER.md。

        预算分配原则：**自动区优先，手写区拿剩余**。理由与 MEMORY.md 相反——
        MEMORY.md 的手工笔记是唯一由人维护的内容，值得挤掉事实；而 USER.md 的
        自动区才是这个文件存在的理由（画像），手写区是补充。旧实现让笔记先占
        满预算，一份长笔记就能把全部画像挤成「暂无记录」。
        """
        note_text = note.content.strip() if note is not None else ""
        needs_review = bool(note.metadata.get("needs_review")) if note is not None else False
        if not facts and not note_text:
            # 一条画像都没有：直接显示出厂模板，让编译侧按「未定制」跳过注入。
            template = load_bundled_template("USER.md")
            if template:
                return template

        overhead = len(self._render_user_md([], "", needs_review=needs_review))
        note_budget = max(0, USER_MD_MAX_CHARS - overhead)
        fitted_note = note_text
        if len(note_text) > note_budget:
            # 留给笔记的显示长度（正文仍完整保存在库里）。
            fitted_note = note_text[:note_budget] + "…（文件显示已截断）"

        content = self._render_user_md(facts, fitted_note, needs_review=needs_review)
        omitted = 0
        while facts and len(content) > USER_MD_MAX_CHARS:
            facts = facts[:-1]
            omitted += 1
            content = self._render_user_md(
                facts, fitted_note, needs_review=needs_review, omitted=omitted
            )
        if len(content) > USER_MD_MAX_CHARS:
            # 自动区自身就超限（不太可能，事实已按重要性排过）：硬截笔记兜底。
            fitted_note = note_text[: max(0, USER_MD_MAX_CHARS // 3)] + "…（文件显示已截断）"
            content = self._render_user_md(
                facts, fitted_note, needs_review=needs_review, omitted=omitted
            )
        return content

    def _render_user_md(
        self,
        facts: list[Memory],
        note_text: str = "",
        *,
        needs_review: bool = False,
        omitted: int = 0,
    ) -> str:
        """固定格式：标题 + 三个画像分区（自动区）+ 手写区。"""
        buckets: dict[str, list[Memory]] = {name: [] for name, _ in _USER_SECTIONS}
        for memory in facts:
            buckets[classify_user_section(memory)].append(memory)

        lines: list[str] = [USER_VIEW_HEADING, "", USER_VIEW_INTRO, "", USER_VIEW_BEGIN]
        rendered = 0
        for section, _ in _USER_SECTIONS:
            items = sorted(
                buckets.get(section, []),
                key=lambda memory: memory.importance_score,
                reverse=True,
            )[:USER_TOP_K_PER_SECTION]
            if not items:
                continue
            if rendered:
                lines.append("")
            rendered += len(items)
            lines.append(f"## {section}")
            lines.extend(f"- {_inline(memory.content)}" for memory in items)
        if not rendered and not omitted:
            lines.append(USER_VIEW_EMPTY)
        if omitted:
            lines.append(f"<!-- 超出字符上限，已省略 {omitted} 条画像 -->")
        lines.append(USER_VIEW_END)
        lines.extend(["", USER_NOTE_BEGIN, USER_NOTE_HEADING])
        if needs_review:
            lines.append(CORE_NOTE_REVIEW_MARK)
        lines.append(note_text.strip() or USER_NOTE_PLACEHOLDER)
        lines.append(USER_NOTE_END)
        return "\n".join(lines).rstrip() + "\n"

    def _render_export(self, facts: list[Memory], note: Memory | None) -> str:
        """渲染导出文件：自动导出区 + 手工笔记区，并压在字符上限内。

        超预算时按 P2 的优先级整条裁：经验/技能/教训先走，事实次之，偏好与
        规则最后——「规则类优先保留」是硬要求，绝不让尾部的事实段把规则段
        挤掉。整条裁而不是截半句：半条规则比没有规则更危险。
        """
        note_text = (note.content.strip() if note is not None else "")
        needs_review = bool(note.metadata.get("needs_review")) if note is not None else False
        content = self._render_memory_md(facts, note_text, needs_review=needs_review)
        omitted = 0
        while facts and len(content) > MEMORY_MD_MAX_CHARS:
            facts = [
                memory
                for index, memory in enumerate(facts)
                if index != _drop_index(facts)
            ]
            omitted += 1
            content = self._render_memory_md(
                facts, note_text, needs_review=needs_review, omitted=omitted
            )
        if len(content) > MEMORY_MD_MAX_CHARS:
            # 只剩笔记本身超限：文件里截断显示，完整正文仍保存在库里。
            note_text = note_text[: max(0, MEMORY_MD_MAX_CHARS // 2)] + "…（文件显示已截断）"
            content = self._render_memory_md(
                facts, note_text, needs_review=needs_review, omitted=omitted
            )
        return content

    def _render_memory_md(
        self,
        memories: list[Memory],
        note_text: str = "",
        *,
        needs_review: bool = False,
        omitted: int = 0,
    ) -> str:
        """把核心事实与手工笔记渲染成固定格式的导出文本。"""
        grouped: dict[MemoryType, list[Memory]] = {}
        for memory in memories:
            if is_manual_note_record(memory.id):
                continue
            grouped.setdefault(memory.type, []).append(memory)

        lines: list[str] = [CORE_VIEW_HEADING, "", CORE_VIEW_BEGIN]
        for memory_type, label in _MEMORY_TYPE_TO_SECTION.items():
            items = sorted(
                grouped.get(memory_type, []),
                key=lambda memory: memory.importance_score,
                reverse=True,
            )[:TOP_K_PER_SECTION]
            if not items:
                continue
            lines.append(f"## {label}")
            lines.extend(f"- {_inline(memory.content)}" for memory in items)
            lines.append("")
        if omitted:
            lines.append(f"<!-- 超出字符上限，已省略 {omitted} 条核心事实 -->")
        lines.append(CORE_VIEW_END)
        lines.extend(["", CORE_NOTE_BEGIN, CORE_NOTE_HEADING])
        if needs_review:
            lines.append(CORE_NOTE_REVIEW_MARK)
        lines.append(note_text.strip() or CORE_NOTE_PLACEHOLDER)
        lines.append(CORE_NOTE_END)
        return "\n".join(lines).rstrip() + "\n"

    def _safe_write_with_backup(self, path: Path, content: str) -> None:
        """Write content to path with .bak backup; restore on failure."""
        backup = path.with_suffix(path.suffix + ".bak")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            shutil.copy2(path, backup)
        try:
            path.write_text(content, encoding="utf-8")
        except Exception:
            if backup.exists():
                shutil.copy2(backup, path)
            raise


def _inline(content: str) -> str:
    """导出条目压成一行，避免一条记忆伪造出新的段头/条目。"""
    return " ".join(content.split())


def _strip_note_heading(text: str) -> str:
    """去掉用户粘贴进来的「## 手工笔记」标题行（导出格式自带该标题）。"""
    lines = text.strip().splitlines()
    while lines and lines[0].strip() == CORE_NOTE_HEADING:
        lines.pop(0)
    return "\n".join(lines).strip()


def _strip_user_note_heading(text: str) -> str:
    """去掉用户粘贴进来的「## 特别说明」标题行（导出格式自带该标题）。"""
    lines = text.strip().splitlines()
    while lines and lines[0].strip() == USER_NOTE_HEADING:
        lines.pop(0)
    return "\n".join(lines).strip()


def _strip_legacy_template_noise(text: str) -> str:
    """剥掉旧版出厂模板的脚手架，只留下用户真正写过的东西。

    旧英文模板（``(your name)`` / ``[ ] Casual`` / ``Topics of Interest``）
    在真实 workspace 里普遍存在。保留它会把一份空表当手稿，全剥掉又会连用户
    填过的那两行一起丢——所以逐行判断：**含占位标记的行、空勾选项、空列表项
    都丢掉**，剩下还有正文才算「有价值迁移」。

    返回剥完的正文；返回空串即表示「这就是一份没填过的模板」。
    """
    placeholders = (
        "(your name)", "(your timezone", "(preferred language",
        "(your role", "(what you're working on)", "(ides, languages",
        "edit this file to customize", "topics of interest",
        "(any specific instructions",
        "information about the user to help personalize",
    )
    kept: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("<!--") or line.startswith("*edit this file"):
            continue
        lowered = line.lower()
        if any(marker in lowered for marker in placeholders):
            continue
        # 空勾选框 / 空列表项 / 只剩字段名
        if line in {"- [ ]", "-", "*", "- [ ] "}:
            continue
        if line.startswith("- [ ]"):
            continue
        if set(line) <= {"-", "*", " ", "|", "#"}:
            continue
        kept.append(line)
    # 标题行不进迁移结果：旧模板的 `## Basic Information` 之类是脚手架，
    # 搬进笔记区只会变成一堆空标题。只剩标题也等于空模板。
    body = [line for line in kept if not line.startswith("#")]
    return "\n".join(body)
