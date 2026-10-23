"""文章 ↔ WebUI 会话的服务端绑定。

一篇一聊：客户端只说文章 id，``chat_id``/``session_key`` 与团队绑定全部由服务端生成
并落盘。绑定时同步写入 ``session.metadata``，因此首条消息到达前团队身份就已经确定，
不存在「先按普通聊天回答、再变成团队」的窗口。
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from nanobot.writing.database import WritingDatabase
from nanobot.writing.models import utc_now_iso
from nanobot.writing.repository import ArticleNotFound, ArticleRepository

TEAM_BINDING_METADATA_KEY = "team_binding"
WRITING_ARTICLE_METADATA_KEY = "writing_article"


class SessionLike(Protocol):
    key: str
    metadata: dict[str, Any]


class SessionStoreLike(Protocol):
    def get_or_create(self, key: str) -> SessionLike: ...

    def save(self, session: SessionLike, *, fsync: bool = False) -> None: ...


@dataclass(frozen=True)
class ArticleSession:
    article_id: str
    chat_id: str
    session_key: str
    team_id: str
    team_revision: int
    team_name: str
    article_revision: int
    article_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "chat_id": self.chat_id,
            "session_key": self.session_key,
            "team_id": self.team_id,
            "team_revision": self.team_revision,
            "team_name": self.team_name,
            "revision": self.article_revision,
            "digest": self.article_digest,
        }


class WritingSessionBinder:
    """一个工作区一个实例；团队信息由注入的 resolver 提供。"""

    def __init__(
        self,
        db: WritingDatabase,
        sessions: SessionStoreLike | None,
        *,
        team_resolver: Callable[[], Mapping[str, Any]] | None,
        session_key_for: Callable[[str], str],
    ) -> None:
        self._db = db
        self._sessions = sessions
        self._team_resolver = team_resolver
        self._session_key_for = session_key_for
        self._articles = ArticleRepository(db)

    def bind(self, article_id: str) -> ArticleSession:
        article = self._articles.get_article(article_id)["article"]
        existing = self._row(article_id)
        team = self._team()
        if existing is not None:
            # 团队模板升级后 revision 会前进：重复绑定顺手把会话指向最新修订，
            # 否则 resolve() 仍按旧修订校验名册，新成员会被误判为越权。
            existing = self._refresh_team(article_id, team) or existing
            bound = self._record(existing, team, article)
            self._ensure_session(bound)
            return bound
        if self._sessions is None:
            raise RuntimeError("session store is not configured")

        chat_id = str(uuid.uuid4())
        now = utc_now_iso()
        try:
            with self._db.connect() as conn:
                conn.execute(
                    "INSERT INTO article_sessions (workspace_key, article_id, chat_id,"
                    " session_key, team_id, team_revision, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        self._db.workspace_key, article_id, chat_id,
                        self._session_key_for(chat_id), str(team["team_id"]),
                        int(team["team_revision"]), now, now,
                    ),
                )
        except sqlite3.IntegrityError:
            # 两个页面同时打开同一篇文章：先落库的 chat 才是权威会话。
            row = self._row(article_id)
            if row is None:
                raise
            row = self._refresh_team(article_id, team) or row
            bound = self._record(row, team, article)
            self._ensure_session(bound)
            return bound
        bound = ArticleSession(
            article_id=article_id,
            chat_id=chat_id,
            session_key=self._session_key_for(chat_id),
            team_id=str(team["team_id"]),
            team_revision=int(team["team_revision"]),
            team_name=str(team["team_name"]),
            article_revision=int(article["revision"]),
            article_digest=self._digest(article_id),
        )
        self._ensure_session(bound)
        return bound

    def resolve(self, article_id: str) -> ArticleSession | None:
        row = self._row(article_id)
        if row is None:
            return None
        article = self._articles.get_article(article_id)["article"]
        team = {
            "team_id": str(row["team_id"]),
            "team_revision": int(row["team_revision"]),
            "team_name": "",
        }
        row_team = self._team()
        team["team_name"] = str(row_team["team_name"])
        return self._record(row, team, article)

    def _ensure_session(self, bound: ArticleSession) -> None:
        if self._sessions is None:
            return
        session = self._sessions.get_or_create(bound.session_key)
        metadata = dict(getattr(session, "metadata", {}) or {})
        binding = {
            "teamId": bound.team_id,
            "teamRevision": bound.team_revision,
            "teamName": bound.team_name,
        }
        writing = {"articleId": bound.article_id, "workspaceKey": self._db.workspace_key}
        changed = (
            metadata.get(TEAM_BINDING_METADATA_KEY) != binding
            or metadata.get(WRITING_ARTICLE_METADATA_KEY) != writing
        )
        if not changed:
            return
        session.metadata[TEAM_BINDING_METADATA_KEY] = binding
        session.metadata[WRITING_ARTICLE_METADATA_KEY] = writing
        self._sessions.save(session)

    def _team(self) -> dict[str, Any]:
        if self._team_resolver is None:
            raise RuntimeError("writing team is not configured")
        team = dict(self._team_resolver())
        return {
            "team_id": str(team["team_id"]),
            "team_revision": int(team["team_revision"]),
            "team_name": str(team["team_name"]),
        }

    def _refresh_team(
        self, article_id: str, team: Mapping[str, Any]
    ) -> Mapping[str, Any] | None:
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE article_sessions SET team_id = ?, team_revision = ?, updated_at = ?"
                " WHERE workspace_key = ? AND article_id = ?",
                (
                    str(team["team_id"]), int(team["team_revision"]), utc_now_iso(),
                    self._db.workspace_key, article_id,
                ),
            )
            row = conn.execute(
                "SELECT article_id, chat_id, session_key, team_id, team_revision"
                " FROM article_sessions WHERE workspace_key = ? AND article_id = ?",
                (self._db.workspace_key, article_id),
            ).fetchone()
        return None if row is None else cast(Mapping[str, Any], row)

    def _row(self, article_id: str) -> Mapping[str, Any] | None:
        try:
            self._articles.get_article(article_id)
        except ArticleNotFound:
            raise
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT article_id, chat_id, session_key, team_id, team_revision"
                " FROM article_sessions WHERE workspace_key = ? AND article_id = ?",
                (self._db.workspace_key, article_id),
            ).fetchone()
        return None if row is None else cast(Mapping[str, Any], row)

    def _digest(self, article_id: str) -> str:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT content_digest FROM articles WHERE workspace_key = ? AND id = ?",
                (self._db.workspace_key, article_id),
            ).fetchone()
        if row is None:
            raise ArticleNotFound(article_id)
        return str(row["content_digest"])

    def _record(
        self,
        row: Mapping[str, Any],
        team: Mapping[str, Any],
        article: Mapping[str, Any],
    ) -> ArticleSession:
        return ArticleSession(
            article_id=str(row["article_id"]),
            chat_id=str(row["chat_id"]),
            session_key=str(row["session_key"]),
            team_id=str(team["team_id"]),
            team_revision=int(team["team_revision"]),
            team_name=str(team["team_name"]),
            article_revision=int(article["revision"]),
            article_digest=self._digest(str(row["article_id"])),
        )
