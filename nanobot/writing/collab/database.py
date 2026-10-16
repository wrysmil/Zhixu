"""协作域 DDL：契约 §2 里除内置资产表之外的全部表与索引。

本模块**不修改** ``nanobot/writing/database.py``：既有库没有 ALTER 迁移路径，
协作层因此自管自己的 ``CREATE TABLE/INDEX IF NOT EXISTS``，并用独立的
``ensure_collab_schema`` 在 ``WritingDatabase.connect()`` 上幂等补建，
这样 schema v1 的老库也能原地长出协作表。
"""

from __future__ import annotations

from nanobot.writing.database import WritingDatabase

COLLAB_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS writing_collaborations (
      id                TEXT    NOT NULL,
      workspace_key     TEXT    NOT NULL,
      article_id        TEXT    NOT NULL,
      chat_id           TEXT    NOT NULL,
      session_key       TEXT    NOT NULL,
      goal_json         TEXT    NOT NULL DEFAULT '{}',
      status            TEXT    NOT NULL DEFAULT 'idle',
      stage             TEXT    NOT NULL DEFAULT '',
      active_batch_id   TEXT,
      cancel_generation INTEGER NOT NULL DEFAULT 0,
      version           INTEGER NOT NULL DEFAULT 1,
      seq               INTEGER NOT NULL DEFAULT 0,
      last_handoff_id   TEXT,
      created_at        TEXT    NOT NULL,
      updated_at        TEXT    NOT NULL,
      PRIMARY KEY (workspace_key, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS writing_collaborations_article_idx"
    " ON writing_collaborations (workspace_key, article_id, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS writing_collab_batches (
      id                TEXT    NOT NULL,
      workspace_key     TEXT    NOT NULL,
      collaboration_id  TEXT    NOT NULL,
      attempt           INTEGER NOT NULL DEFAULT 1,
      team_run_id       TEXT    NOT NULL DEFAULT '',
      base_revision     INTEGER NOT NULL,
      snapshot_digest   TEXT    NOT NULL,
      action            TEXT    NOT NULL,
      instruction       TEXT    NOT NULL DEFAULT '',
      status            TEXT    NOT NULL DEFAULT 'queued',
      cancel_generation INTEGER NOT NULL DEFAULT 0,
      frozen_json       TEXT    NOT NULL DEFAULT '{}',
      error_message     TEXT    NOT NULL DEFAULT '',
      created_at        TEXT    NOT NULL,
      finished_at       TEXT,
      PRIMARY KEY (workspace_key, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS writing_collab_batches_collab_idx"
    " ON writing_collab_batches (workspace_key, collaboration_id, created_at)",
    """
    CREATE TABLE IF NOT EXISTS writing_collab_artifacts (
      id               TEXT    NOT NULL,
      workspace_key    TEXT    NOT NULL,
      collaboration_id TEXT    NOT NULL,
      batch_id         TEXT    NOT NULL,
      attempt          INTEGER NOT NULL DEFAULT 1,
      kind             TEXT    NOT NULL,
      role_id          TEXT    NOT NULL,
      version          INTEGER NOT NULL DEFAULT 1,
      content_json     TEXT    NOT NULL DEFAULT '{}',
      content_digest   TEXT    NOT NULL,
      source_ids       TEXT    NOT NULL DEFAULT '[]',
      gaps             TEXT    NOT NULL DEFAULT '[]',
      status           TEXT    NOT NULL DEFAULT 'ready',
      created_at       TEXT    NOT NULL,
      PRIMARY KEY (workspace_key, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS writing_collab_artifacts_collab_idx"
    " ON writing_collab_artifacts (workspace_key, collaboration_id, kind, created_at)",
    """
    CREATE TABLE IF NOT EXISTS writing_collab_handoffs (
      id               TEXT    NOT NULL,
      workspace_key    TEXT    NOT NULL,
      collaboration_id TEXT    NOT NULL,
      batch_id         TEXT    NOT NULL,
      artifact_id      TEXT    NOT NULL,
      kind             TEXT    NOT NULL,
      artifact_version INTEGER NOT NULL,
      status           TEXT    NOT NULL DEFAULT 'pending',
      created_at       TEXT    NOT NULL,
      resolved_at      TEXT,
      PRIMARY KEY (workspace_key, id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS writing_collab_suggestions (
      id                  TEXT    NOT NULL,
      workspace_key       TEXT    NOT NULL,
      collaboration_id    TEXT    NOT NULL,
      artifact_id         TEXT    NOT NULL DEFAULT '',
      article_id          TEXT    NOT NULL,
      base_revision       INTEGER NOT NULL,
      snapshot_digest     TEXT    NOT NULL,
      target_json         TEXT    NOT NULL DEFAULT '{}',
      before_text         TEXT    NOT NULL DEFAULT '',
      after_text          TEXT    NOT NULL,
      summary             TEXT    NOT NULL DEFAULT '',
      notes               TEXT    NOT NULL DEFAULT '',
      source_material_ids TEXT    NOT NULL DEFAULT '[]',
      status              TEXT    NOT NULL DEFAULT 'pending',
      applied_revision    INTEGER,
      created_at          TEXT    NOT NULL,
      applied_at          TEXT,
      PRIMARY KEY (workspace_key, id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS writing_collab_suggestions_article_idx"
    " ON writing_collab_suggestions (workspace_key, article_id, status)",
    """
    CREATE TABLE IF NOT EXISTS writing_collab_undo_entries (
      id              TEXT    NOT NULL,
      workspace_key   TEXT    NOT NULL,
      article_id      TEXT    NOT NULL,
      suggestion_id   TEXT    NOT NULL,
      before_body     TEXT    NOT NULL,
      before_revision INTEGER NOT NULL,
      result_revision INTEGER NOT NULL,
      result_digest   TEXT    NOT NULL,
      reverted        INTEGER NOT NULL DEFAULT 0,
      created_at      TEXT    NOT NULL,
      reverted_at     TEXT,
      PRIMARY KEY (workspace_key, id)
    )
    """,
)


def ensure_collab_schema(db: WritingDatabase) -> None:
    """幂等补建协作表与索引；DDL 全为 IF NOT EXISTS，任何状态都能重跑。"""
    with db.connect() as conn:
        for statement in COLLAB_SCHEMA_STATEMENTS:
            conn.execute(statement)
