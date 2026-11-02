"""三层记忆系统的 SQLite 存储层。

公开 API:
    MemoryDatabase    — SQLite 连接与 schema 管理入口
    Memory / Episode / ScratchpadEntry — 数据模型
    add_memory / get_memory / list_memories / search_memories / delete_memory — 语义记忆 CRUD
    add_episode / get_episode / list_episodes_by_session — 情节记忆 CRUD
    upsert_scratchpad / get_scratchpad — 工作记忆 CRUD
"""

from nanobot.memory.database import MemoryDatabase
from nanobot.memory.models import (
    Episode,
    EpisodeOutcome,
    EpisodeSource,
    Memory,
    MemoryPriority,
    MemoryType,
    ScratchpadEntry,
)
from nanobot.memory.profile_extractor import ProfileExtractor
from nanobot.memory.repository import (
    add_episode,
    add_memory,
    delete_memory,
    get_episode,
    get_memory,
    get_scratchpad,
    list_episodes_by_session,
    list_memories,
    search_memories,
    upsert_scratchpad,
)
from nanobot.memory.session_end_event import SessionEndEvent, SessionEndReason

__all__ = [
    "MemoryDatabase",
    "Memory", "Episode", "ScratchpadEntry",
    "MemoryType", "MemoryPriority", "EpisodeOutcome", "EpisodeSource",
    "add_memory", "get_memory", "list_memories", "search_memories", "delete_memory",
    "add_episode", "get_episode", "list_episodes_by_session",
    "upsert_scratchpad", "get_scratchpad",
]
