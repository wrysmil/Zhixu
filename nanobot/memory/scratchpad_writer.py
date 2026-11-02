"""Scratchpad 写入器（T0 即时同步路径，plan §6）。

写入策略（不调 LLM）：
- T0 即时同步路径：update_focus / archive_completed

与 runtime_control.__scratchpad 完全独立，不在本模块范围内连接。
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from loguru import logger

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import ScratchpadEntry
from nanobot.memory.repository import get_scratchpad, upsert_scratchpad

# ---------- 常量 ----------

# active_projects 最多保留条目数（plan §6.1）
MAX_ACTIVE_PROJECTS = 5

# active_projects 条目的时间戳前缀，形如 "[09-10 15:30] "
_FOCUS_ENTRY_PREFIX_RE = re.compile(r"^\[\d{2}-\d{2} \d{2}:\d{2}\]\s*")


# ---------- 归一化 ----------

def _normalize_focus_entry(text: str) -> str:
    """归一化 focus / active_projects 条目，用于归档去重比较。

    仅做「无损」归一化：剥离 :meth:`ScratchpadWriter._format_project_entry` 加的
    ``[MM-DD HH:MM] `` 时间戳前缀、去首尾空白、折叠连续空白为一个空格。
    刻意**不**做大小写折叠、不做中文简繁/同义归并、不做模糊匹配：
    ``update_focus`` 是每轮对话后的同步热路径（目标 < 50ms），且精确匹配
    语义更可预测（不会误杀真正不同的任务）。

    Args:
        text: 原始 focus 文本或带前缀的 active_projects 条目。

    Returns:
        归一化后的比较键；空串/纯空白输入返回空串。
    """
    # 先 strip 再剥前缀：current_focus 来自用户/LLM 文本，可能带首尾空白，
    # 而归档条目由 _format_project_entry 生成（前缀永远在最前面）。
    return " ".join(_FOCUS_ENTRY_PREFIX_RE.sub("", text.strip()).split())


# ---------- 注入渲染 ----------

# 渲染进 prompt 的单条内容字符上限。写入侧已截断一轮（_format_project_entry /
# update_focus），但历史行可能由旧代码或别的入口写入而超长，渲染侧独立兜底。
MAX_RENDERED_ITEM_CHARS = 200

# 独占一行的水平分隔线（3 个及以上连字符，允许首尾空白）。
# 显式吃掉行尾 \r：\r\n 源下 "[ \t]*$" 匹配不到分隔行，会让伪造的 --- 漏过清洗。
_HR_LINE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*\r?$", re.MULTILINE)

# 行首标题标记：markdown 允许最多 3 空格缩进，故只吃 0-3 个空格，
# 避免把 4 空格以上的缩进代码块也压平。
_HEADING_PREFIX_RE = re.compile(r"^[ \t]{0,3}#{1,}[ \t]*", re.MULTILINE)

# 3 个及以上连续换行。
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{3,}")

_WORKING_MEMORY_HEADER = (
    "# Working Memory\n\n"
    "以下是你自己维护的短时工作状态，跨会话保留，可能已经过时；以当前对话为准。"
)


def _sanitize_injected_text(value: str) -> str:
    """清洗将要注入 system prompt 的工作记忆文本。

    工作记忆内容来自用户输入且会被原样喂回 prompt，存在自反馈回路
    （用户可写入「忽略你之前的所有规则」，下一轮该句即回到模型眼前），
    故渲染前必须阻断结构性伪造。

    清洗项：
    1. 剥离独占一行的 ``---``：调用方用 ``\\n\\n---\\n\\n`` 拼接 prompt 各段，
       值里伪造该分隔符即可凭空造出一段新内容。
    2. 折叠 3 个及以上连续换行为 2 个：剥离分隔线后残留的空行会破坏段落结构。
    3. 剥离行首 ``#``：防伪造标题层级冒充 ``# Working Memory``。

    刻意**不**动行内 markdown 强调（``**粗体**``）与列表符号（``- ``）——
    这段内容本就是给模型读的富文本，剥过头会损伤可读性。

    Args:
        value: 原始文本（current_focus 或单条 active_projects 条目）。

    Returns:
        清洗并去首尾空白后的文本；清洗后为空时返回空串。
    """
    text = _HR_LINE_RE.sub("", value)
    text = _EXCESS_BLANK_LINES_RE.sub("\n\n", text)
    text = _HEADING_PREFIX_RE.sub("", text)
    return text.strip()


def render_working_memory_markdown(entry: ScratchpadEntry) -> str:
    """把一条 scratchpad 记录渲染成可注入 prompt 的 markdown 片段。

    只渲染 ``current_focus`` 与 ``active_projects`` 两个字段——``content`` /
    ``open_questions`` / ``next_steps`` 目前生产环境无写入方，渲染它们只会
    输出恒空的小节。active_projects 条目自带写入侧加的 ``[MM-DD HH:MM] ``
    前缀，此处原样保留，不重新生成时间戳。

    Args:
        entry: scratchpad 记录。

    Returns:
        markdown 片段；``current_focus`` 与 ``active_projects`` 皆为空时返回空串，
        供调用方判定「本轮不注入」。
    """
    sections: list[str] = [_WORKING_MEMORY_HEADER]
    truncated_focus = 0
    truncated_items = 0

    focus = _sanitize_injected_text(entry.current_focus)
    if focus:
        if len(focus) > MAX_RENDERED_ITEM_CHARS:
            focus = focus[:MAX_RENDERED_ITEM_CHARS]
            truncated_focus = 1
        sections.append(f"## 当前任务\n{focus}")

    raw_projects = list(entry.active_projects)
    items: list[str] = []
    for raw in raw_projects[:MAX_ACTIVE_PROJECTS]:
        item = _sanitize_injected_text(raw)
        if not item:
            continue
        if len(item) > MAX_RENDERED_ITEM_CHARS:
            item = item[:MAX_RENDERED_ITEM_CHARS]
            truncated_items += 1
        items.append(f"- {item}")
    if items:
        sections.append("## 进行中\n" + "\n".join(items))

    dropped_items = len(raw_projects) - MAX_ACTIVE_PROJECTS
    if dropped_items > 0 or truncated_items or truncated_focus:
        # 只记条数，绝不记 focus 原文——那可能含用户输入内容。
        logger.debug(
            "[scratchpad] render working memory: dropped_projects={} truncated_projects={}"
            " truncated_focus={} kept_projects={}",
            dropped_items,
            truncated_items,
            truncated_focus,
            len(items),
        )

    if len(sections) == 1:
        return ""
    return "\n\n".join(sections)


# ---------- ScratchpadWriter ----------

class ScratchpadWriter:
    """处理 scratchpad 写入的类（plan §6）。

    T0 即时同步路径（不调 LLM），适用于 after_run 钩子等高频低延迟场景。
    所有操作通过 :class:`MemoryDatabase` 的线程安全连接执行。

    Args:
        database: MemoryDatabase 实例。
        user_id: 用户 ID，必传；用于跨用户隔离 scratchpad 行（FIX-1 Sec-C-α）。
        workspace_id: 工作区 ID，默认为 "default"。
    """

    def __init__(
        self,
        database: MemoryDatabase,
        user_id: str,
        workspace_id: str = "default",
    ) -> None:
        self.database = database
        self.user_id = user_id
        self.workspace_id = workspace_id

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    @staticmethod
    def user_id_for_key(session_key: str) -> str:
        """从 ``session_key``（格式 ``channel:chat_id``）派生 ``user_id``。

        FIX-1 Sec-C-α：避免所有会话共用 ``"default"`` 导致的跨用户数据串扰。
        对 ``channel:chat_id`` 格式返回 ``chat_id``；其他格式（如无冒号的单段 key）
        整体回退，保证向后兼容。
        """
        if ":" in session_key:
            _, _, tail = session_key.partition(":")
            if tail:
                return tail
        return session_key

    @staticmethod
    def _now_iso() -> str:
        """返回当前 UTC 时间 ISO 字符串。"""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _format_project_entry(focus: str) -> str:
        """格式化 active_projects 条目：带日期前缀，如 "[09-10 15:30] xxx"。

        Args:
            focus: 项目描述文本。

        Returns:
            带本地时间戳前缀的条目，focus 截断至 200 字符。
        """
        now = datetime.now()
        date_prefix = now.strftime("%m-%d %H:%M")
        return f"[{date_prefix}] {focus[:200]}"

    # ------------------------------------------------------------------
    # T0 即时同步路径
    # ------------------------------------------------------------------

    async def update_focus(
        self,
        new_focus: str,
    ) -> None:
        """T0 即时同步：更新 current_focus，旧 focus 入 active_projects。

        触发时机：每轮 after_run。
        语义：
        - 写入 scratchpad.current_focus = new_focus[:200]
        - 若存在旧 focus 且与 new_focus 不同，将其归档至 active_projects
        - 旧 focus 归一化后（剥时间戳前缀 / 去首尾空白 / 折叠连续空白）若已存在于
          active_projects，则跳过归档，防止同话题碎片占满有限的归档格
        - active_projects 最多保留 MAX_ACTIVE_PROJECTS 条，超出时移除最旧条目
        - 同步执行，目标 < 50ms

        注意：实例的 ``user_id`` 决定 scratchpad 行的归属（FIX-1 Sec-C-α）。
        生产应由调用方在构造 ``ScratchpadWriter`` 时用 ``user_id_for_key(session_key)``
        派生 ``user_id``，确保不同会话得到独立 scratchpad 行。

        Args:
            new_focus: 新的当前焦点描述。
        """
        old_focus: str = ""
        existing_projects: list[str] = []

        # 读取现有 scratchpad（获取旧 focus 和 active_projects）
        with self.database.connect() as conn:
            entry = get_scratchpad(conn, self.user_id, self.workspace_id)
            if entry is not None:
                old_focus = entry.current_focus
                existing_projects = list(entry.active_projects)

        # 准备新的 active_projects
        # 去重：旧 focus 归一化后若已在列表中，不再重复归档。active_projects
        # 只有 MAX_ACTIVE_PROJECTS 格，用户围绕同一件事连着追问时若每轮都归档，
        # 5 轮之内就会把格子塞满同话题碎片并挤掉真实历史任务。
        new_projects = list(existing_projects)
        already_archived = {_normalize_focus_entry(p) for p in existing_projects}
        if old_focus and old_focus != new_focus:
            if _normalize_focus_entry(old_focus) in already_archived:
                # 不记 focus 原文（可能含用户内容），只记事件与定位字段。
                logger.debug(
                    "[T0] skip archive: focus already in active_projects"
                    " (user={} workspace={} archived={})",
                    self.user_id,
                    self.workspace_id,
                    len(new_projects),
                )
            else:
                new_projects.insert(0, self._format_project_entry(old_focus))
                # 最多保留 MAX_ACTIVE_PROJECTS 条
                new_projects = new_projects[:MAX_ACTIVE_PROJECTS]

        # 构建新 entry 并写入
        updated_entry = ScratchpadEntry(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            updated_at=self._now_iso(),
            content="",
            active_projects=new_projects,
            current_focus=new_focus[:200],
            open_questions=[],
            next_steps=[],
        )
        with self.database.connect() as conn:
            upsert_scratchpad(conn, updated_entry)

    async def archive_completed(
        self,
        resolved_items: list[str],
    ) -> None:
        """从 open_questions 中移除已解决项。

        触发时机：调用方判定某些问题已解决时（本模块不调 LLM）。

        实现：在 get_scratchpad 基础上过滤掉 resolved_items 中的条目，
        通过 upsert_scratchpad 写回。resolved_items 中不在 open_questions
        中的条目无操作影响。

        注意：本方法仅支持从 open_questions 移除项，不支持修改其他字段。
        完整的 open_questions 替换需由调用方保证一致性。

        Args:
            resolved_items: 已解决的项目列表（精确匹配移除）。
        """
        if not resolved_items:
            return

        existing_questions: list[str] = []
        current_focus: str = ""
        existing_projects: list[str] = []
        existing_next_steps: list[str] = []

        # 读取现有 scratchpad
        with self.database.connect() as conn:
            entry = get_scratchpad(conn, self.user_id, self.workspace_id)
            if entry is not None:
                current_focus = entry.current_focus
                existing_projects = list(entry.active_projects)
                existing_questions = list(entry.open_questions)
                existing_next_steps = list(entry.next_steps)

        # 过滤掉已解决项（精确匹配）
        resolved_set = set(resolved_items)
        remaining_questions = [q for q in existing_questions if q not in resolved_set]

        # 写回（保留其他字段不变）
        updated_entry = ScratchpadEntry(
            user_id=self.user_id,
            workspace_id=self.workspace_id,
            updated_at=self._now_iso(),
            content="",
            active_projects=existing_projects,
            current_focus=current_focus,
            open_questions=remaining_questions,
            next_steps=existing_next_steps,
        )
        with self.database.connect() as conn:
            upsert_scratchpad(conn, updated_entry)
