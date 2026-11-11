"""图谱 SQLite 仓储：实体/关系/证据、计数重算与抽取状态机。

SQLite 是唯一真相源；Neo4j 只是由这里的数据重建的派生索引。
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from nanobot.knowledge.database import KnowledgeDatabase
from nanobot.knowledge.graph.models import (
    GraphEntity,
    GraphExtractionState,
    GraphRelation,
    GraphStats,
)
from nanobot.knowledge.graph.normalize import normalize_entity_name

_DEFAULT_LEASE_SECONDS = 600
_ID_BATCH = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def entity_id_for(kb_id: str, name_norm: str) -> str:
    digest = hashlib.sha256(f"{kb_id}\0{name_norm}".encode()).hexdigest()
    return f"ent_{digest[:32]}"


def relation_id_for(kb_id: str, src_norm: str, relation: str, tgt_norm: str) -> str:
    digest = hashlib.sha256(
        f"{kb_id}\0{src_norm}\0{relation}\0{tgt_norm}".encode()
    ).hexdigest()
    return f"rel_{digest[:32]}"


def _like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _batches(items: Sequence[str], size: int = _ID_BATCH) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class GraphRepository:
    def __init__(self, database: KnowledgeDatabase) -> None:
        self._db = database

    # ---------- 写入（幂等重抽） ----------
    def replace_document_graph(
        self,
        doc_id: str,
        kb_id: str,
        *,
        entities: Sequence[tuple[str, str, str]],
        relations: Sequence[tuple[str, str, str, str, float]],
        mentions: Sequence[tuple[str, str]],
    ) -> tuple[set[str], set[str]]:
        """先删本文档证据，再写本轮结果，最后按证据表重算计数并清理孤儿。"""
        now = _now()
        touched_entities: set[str] = set()
        touched_relations: set[str] = set()
        with self._db.connect(immediate=True) as conn:
            touched_entities.update(
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT entity_id FROM graph_mentions WHERE doc_id=?",
                    (doc_id,),
                )
            )
            touched_relations.update(
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT relation_id FROM graph_relation_evidence"
                    " WHERE doc_id=?",
                    (doc_id,),
                )
            )
            conn.execute("DELETE FROM graph_mentions WHERE doc_id=?", (doc_id,))
            conn.execute(
                "DELETE FROM graph_relation_evidence WHERE doc_id=?", (doc_id,)
            )
            for name, entity_type, description in entities:
                norm = normalize_entity_name(name)
                if not norm:
                    continue
                eid = entity_id_for(kb_id, norm)
                conn.execute(
                    """INSERT INTO graph_entities
                       (id,kb_id,name,name_norm,type,description,mention_count,
                        created_at,updated_at)
                       VALUES (?,?,?,?,?,?,0,?,?)
                       ON CONFLICT(kb_id,name_norm) DO UPDATE SET
                         type=excluded.type,
                         description=excluded.description,
                         updated_at=excluded.updated_at""",
                    (eid, kb_id, name.strip(), norm, entity_type, description, now, now),
                )
                touched_entities.add(eid)
            norm_to_id = {
                row[1]: row[0]
                for row in conn.execute(
                    "SELECT id, name_norm FROM graph_entities WHERE kb_id=?", (kb_id,)
                )
            }
            relation_by_id: dict[str, tuple[str, str, str, str, float]] = {}
            for source, target, relation, description, weight in relations:
                src_norm = normalize_entity_name(source)
                tgt_norm = normalize_entity_name(target)
                src_id = norm_to_id.get(src_norm)
                tgt_id = norm_to_id.get(tgt_norm)
                if src_id is None or tgt_id is None:
                    continue
                rid = relation_id_for(kb_id, src_norm, relation, tgt_norm)
                conn.execute(
                    """INSERT INTO graph_relations
                       (id,kb_id,source_entity_id,target_entity_id,relation,
                        description,weight,mention_count,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,0,?,?)
                       ON CONFLICT(kb_id,source_entity_id,target_entity_id,relation)
                       DO UPDATE SET description=excluded.description,
                         weight=excluded.weight, updated_at=excluded.updated_at""",
                    (
                        rid,
                        kb_id,
                        src_id,
                        tgt_id,
                        relation,
                        description,
                        weight,
                        now,
                        now,
                    ),
                )
                touched_relations.add(rid)
                relation_by_id[rid] = (src_norm, tgt_norm, relation, description, weight)
            # 证据：mention 落实体；关系证据落在源实体出现的块上。
            chunks_by_entity: dict[str, set[str]] = {}
            for name, chunk_id in mentions:
                eid = norm_to_id.get(normalize_entity_name(name))
                if eid is None:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO graph_mentions"
                    " (entity_id,doc_id,chunk_id,created_at) VALUES (?,?,?,?)",
                    (eid, doc_id, chunk_id, now),
                )
                chunks_by_entity.setdefault(eid, set()).add(chunk_id)
            for rid, (src_norm, _tgt, _rel, _desc, _weight) in relation_by_id.items():
                src_id = norm_to_id.get(src_norm)
                for chunk_id in chunks_by_entity.get(src_id or "", ()):
                    conn.execute(
                        "INSERT OR IGNORE INTO graph_relation_evidence"
                        " (relation_id,doc_id,chunk_id,created_at) VALUES (?,?,?,?)",
                        (rid, doc_id, chunk_id, now),
                    )
            self._recount(conn, touched_entities, touched_relations)
        return touched_entities, touched_relations

    def _recount(
        self,
        conn: sqlite3.Connection,
        entity_ids: set[str],
        relation_ids: set[str],
    ) -> None:
        for rid in relation_ids:
            conn.execute(
                """UPDATE graph_relations SET mention_count=(
                     SELECT COUNT(*) FROM graph_relation_evidence
                     WHERE relation_id=?)
                   WHERE id=?""",
                (rid, rid),
            )
            conn.execute(
                "DELETE FROM graph_relations WHERE id=? AND mention_count=0"
                " AND NOT EXISTS (SELECT 1 FROM graph_relation_evidence WHERE relation_id=?)",
                (rid, rid),
            )
        for eid in entity_ids:
            conn.execute(
                """UPDATE graph_entities SET mention_count=(
                     SELECT COUNT(*) FROM graph_mentions WHERE entity_id=?)
                   WHERE id=?""",
                (eid, eid),
            )
            conn.execute(
                "DELETE FROM graph_entities WHERE id=? AND mention_count=0", (eid,)
            )

    def cleanup_orphans(self, entity_ids: Sequence[str]) -> None:
        if not entity_ids:
            return
        with self._db.connect(immediate=True) as conn:
            self._recount(conn, set(entity_ids), set())

    def cleanup_relations(self, kb_id: str) -> None:
        """按剩余证据重算该库全部关系计数，删除证据归零的孤立关系。"""
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                """UPDATE graph_relations SET mention_count=(
                     SELECT COUNT(*) FROM graph_relation_evidence e
                     WHERE e.relation_id = graph_relations.id)
                   WHERE kb_id=?""",
                (kb_id,),
            )
            conn.execute(
                """DELETE FROM graph_relations
                   WHERE kb_id=? AND mention_count=0
                     AND NOT EXISTS (
                       SELECT 1 FROM graph_relation_evidence e
                       WHERE e.relation_id = graph_relations.id)""",
                (kb_id,),
            )

    def purge_kb(self, kb_id: str) -> None:
        with self._db.connect(immediate=True) as conn:
            conn.execute("DELETE FROM graph_relations WHERE kb_id=?", (kb_id,))
            conn.execute("DELETE FROM graph_entities WHERE kb_id=?", (kb_id,))
            conn.execute(
                "DELETE FROM graph_extraction_state WHERE doc_id IN"
                " (SELECT id FROM documents WHERE kb_id=?)",
                (kb_id,),
            )

    def delete_states_for_documents(self, doc_ids: Sequence[str]) -> None:
        with self._db.connect(immediate=True) as conn:
            for chunk in _batches(doc_ids):
                placeholders = ",".join("?" for _ in chunk)
                conn.execute(
                    f"DELETE FROM graph_extraction_state WHERE doc_id IN ({placeholders})",
                    tuple(chunk),
                )

    # ---------- 读：实体/关系 ----------
    def count_entities(self, kb_id: str, *, q: str = "", type_: str = "") -> int:
        where, params = self._entity_filter(kb_id, q, type_)
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) FROM graph_entities WHERE {where}", params
            ).fetchone()
        return int(row[0]) if row else 0

    def list_entities(
        self,
        kb_id: str,
        *,
        q: str = "",
        type_: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphEntity]:
        where, params = self._entity_filter(kb_id, q, type_)
        with self._db.connect() as conn:
            rows = conn.execute(
                f"""SELECT id,kb_id,name,name_norm,type,description,mention_count
                    FROM graph_entities WHERE {where}
                    ORDER BY mention_count DESC, id ASC LIMIT ? OFFSET ?""",
                (*params, max(1, limit), max(0, offset)),
            ).fetchall()
        return [self._row_to_entity(row) for row in rows]

    @staticmethod
    def _entity_filter(kb_id: str, q: str, type_: str) -> tuple[str, tuple[Any, ...]]:
        clauses = ["kb_id = ?"]
        params: list[Any] = [kb_id]
        if q.strip():
            norm = normalize_entity_name(q)
            clauses.append("(name_norm LIKE ? ESCAPE '\\' OR description LIKE ? ESCAPE '\\')")
            params.extend([_like(norm), _like(q.strip())])
        if type_.strip():
            clauses.append("type = ?")
            params.append(type_.strip())
        return " AND ".join(clauses), tuple(params)

    def search_entities_by_like(
        self,
        kb_id: str,
        exact_norm: str,
        tokens: Sequence[str],
        *,
        limit: int = 200,
    ) -> list[GraphEntity]:
        clauses = ["kb_id = ?"]
        params: list[Any] = [kb_id]
        matches: list[str] = []
        if exact_norm:
            matches.append("name_norm = ?")
            params.append(exact_norm)
        for token in tokens:
            if not token:
                continue
            matches.append("name_norm LIKE ? ESCAPE '\\'")
            params.append(_like(normalize_entity_name(token)))
        if not matches:
            return []
        clauses.append("(" + " OR ".join(matches) + ")")
        with self._db.connect() as conn:
            rows = conn.execute(
                f"""SELECT id,kb_id,name,name_norm,type,description,mention_count
                    FROM graph_entities WHERE {' AND '.join(clauses)}
                    ORDER BY mention_count DESC, id ASC LIMIT ?""",
                (*params, max(1, limit)),
            ).fetchall()
        return [self._row_to_entity(row) for row in rows]

    def list_entities_by_ids(
        self, kb_id: str, entity_ids: Sequence[str]
    ) -> list[GraphEntity]:
        if not entity_ids:
            return []
        entities: list[GraphEntity] = []
        with self._db.connect() as conn:
            for chunk in _batches(list(dict.fromkeys(entity_ids))):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""SELECT id,kb_id,name,name_norm,type,description,mention_count
                        FROM graph_entities WHERE kb_id=? AND id IN ({placeholders})""",
                    (kb_id, *chunk),
                ).fetchall()
                entities.extend(self._row_to_entity(row) for row in rows)
            chunks_by_entity: dict[str, list[str]] = {}
            for chunk in _batches(list(dict.fromkeys(entity_ids))):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""SELECT entity_id, chunk_id FROM graph_mentions
                        WHERE entity_id IN ({placeholders})
                        ORDER BY created_at DESC, chunk_id ASC""",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    bucket = chunks_by_entity.setdefault(row[0], [])
                    if len(bucket) < 200 and row[1] not in bucket:
                        bucket.append(row[1])
        return [
            GraphEntity(
                id=entity.id,
                kb_id=entity.kb_id,
                name=entity.name,
                name_norm=entity.name_norm,
                type=entity.type,
                description=entity.description,
                mention_count=entity.mention_count,
                chunk_ids=tuple(chunks_by_entity.get(entity.id, ())),
            )
            for entity in entities
        ]

    def list_entity_ids(self, kb_id: str, entity_ids: Sequence[str]) -> set[str]:
        if not entity_ids:
            return set()
        alive: set[str] = set()
        with self._db.connect() as conn:
            for chunk in _batches(list(dict.fromkeys(entity_ids))):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"SELECT id FROM graph_entities WHERE kb_id=? AND id IN ({placeholders})",
                    (kb_id, *chunk),
                ).fetchall()
                alive.update(row[0] for row in rows)
        return alive

    def get_entity_in_scope(
        self, entity_id: str, workspace_ids: Sequence[str]
    ) -> GraphEntity | None:
        if not workspace_ids:
            return None
        placeholders = ",".join("?" for _ in workspace_ids)
        with self._db.connect() as conn:
            row = conn.execute(
                f"""SELECT e.id,e.kb_id,e.name,e.name_norm,e.type,e.description,
                           e.mention_count
                    FROM graph_entities e
                    JOIN knowledge_bases kb ON kb.id = e.kb_id
                    WHERE e.id=? AND kb.is_enabled=1
                      AND (kb.visibility='global' OR kb.workspace_id IN ({placeholders}))""",
                (entity_id, *workspace_ids),
            ).fetchone()
        return self._row_to_entity(row) if row else None

    def entities_for_chunk(self, chunk_id: str) -> set[str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT entity_id FROM graph_mentions WHERE chunk_id=?", (chunk_id,)
            ).fetchall()
        return {row[0] for row in rows}

    def mention_chunks(self, entity_ids: Sequence[str]) -> list[str]:
        if not entity_ids:
            return []
        seen: dict[str, None] = {}
        with self._db.connect() as conn:
            for chunk in _batches(list(dict.fromkeys(entity_ids))):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""SELECT chunk_id FROM graph_mentions
                        WHERE entity_id IN ({placeholders})
                        GROUP BY chunk_id
                        ORDER BY MIN(created_at), chunk_id""",
                    tuple(chunk),
                ).fetchall()
                for row in rows:
                    seen.setdefault(row[0], None)
        return list(seen)

    def list_mentions(self, entity_id: str) -> list[dict[str, Any]]:
        with self._db.connect() as conn:
            rows = conn.execute(
                """SELECT m.doc_id, d.title AS doc_title, m.chunk_id,
                          c.chunk_index, c.heading_path
                   FROM graph_mentions m
                   JOIN documents d ON d.id = m.doc_id
                   LEFT JOIN document_chunks c ON c.id = m.chunk_id
                   WHERE m.entity_id=?
                   ORDER BY d.title ASC, c.chunk_index ASC""",
                (entity_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_neighbors(self, entity_id: str) -> list[dict[str, Any]]:
        sql = """
          SELECT e.id, e.name, e.type, e.description, e.mention_count,
                 r.relation, r.weight, 'out' AS direction
          FROM graph_relations r
          JOIN graph_entities e ON e.id = r.target_entity_id
          WHERE r.source_entity_id = ?
          UNION ALL
          SELECT e.id, e.name, e.type, e.description, e.mention_count,
                 r.relation, r.weight, 'in' AS direction
          FROM graph_relations r
          JOIN graph_entities e ON e.id = r.source_entity_id
          WHERE r.target_entity_id = ?
          ORDER BY weight DESC, name ASC
        """
        with self._db.connect() as conn:
            rows = conn.execute(sql, (entity_id, entity_id)).fetchall()
        return [dict(row) for row in rows]

    def list_relations(
        self, kb_id: str, *, limit: int = 100_000
    ) -> list[GraphRelation]:
        with self._db.connect() as conn:
            rows = conn.execute(
                """SELECT id,kb_id,source_entity_id,target_entity_id,relation,
                          description,weight,mention_count
                   FROM graph_relations WHERE kb_id=?
                   ORDER BY mention_count DESC, id ASC LIMIT ?""",
                (kb_id, max(1, limit)),
            ).fetchall()
        return [self._row_to_relation(row) for row in rows]

    def list_relations_for_entities(
        self, kb_id: str, entity_ids: Sequence[str]
    ) -> list[GraphRelation]:
        if not entity_ids:
            return []
        found: dict[str, GraphRelation] = {}
        with self._db.connect() as conn:
            for chunk in _batches(list(dict.fromkeys(entity_ids))):
                placeholders = ",".join("?" for _ in chunk)
                rows = conn.execute(
                    f"""SELECT id,kb_id,source_entity_id,target_entity_id,relation,
                               description,weight,mention_count
                        FROM graph_relations
                        WHERE kb_id=?
                          AND (source_entity_id IN ({placeholders})
                               OR target_entity_id IN ({placeholders}))""",
                    (kb_id, *chunk, *chunk),
                ).fetchall()
                for row in rows:
                    relation = self._row_to_relation(row)
                    found[relation.id] = relation
        return list(found.values())

    def stats(self, kb_id: str) -> GraphStats:
        with self._db.connect() as conn:
            entities = conn.execute(
                "SELECT COUNT(*) FROM graph_entities WHERE kb_id=?", (kb_id,)
            ).fetchone()[0]
            relations = conn.execute(
                "SELECT COUNT(*) FROM graph_relations WHERE kb_id=?", (kb_id,)
            ).fetchone()[0]
            rows = conn.execute(
                """SELECT s.status, COUNT(*) FROM graph_extraction_state s
                   JOIN documents d ON d.id = s.doc_id
                   WHERE d.kb_id=? GROUP BY s.status""",
                (kb_id,),
            ).fetchall()
            counts = {row[0]: int(row[1]) for row in rows}
            truncated = conn.execute(
                """SELECT COUNT(*) FROM graph_extraction_state s
                   JOIN documents d ON d.id = s.doc_id
                   WHERE d.kb_id=? AND s.truncated=1""",
                (kb_id,),
            ).fetchone()[0]
            last = conn.execute(
                """SELECT MAX(s.extracted_at) FROM graph_extraction_state s
                   JOIN documents d ON d.id = s.doc_id WHERE d.kb_id=?""",
                (kb_id,),
            ).fetchone()[0]
        return GraphStats(
            enabled=False,
            backend="",
            status="",
            reason="",
            entities=int(entities),
            relations=int(relations),
            docs_ready=counts.get("ready", 0),
            docs_pending=counts.get("pending", 0) + counts.get("processing", 0),
            docs_failed=counts.get("failed", 0),
            docs_skipped=counts.get("skipped", 0),
            truncated_docs=int(truncated),
            last_extracted_at=last,
        )

    # ---------- 抽取状态机 ----------
    def get_state(self, doc_id: str) -> GraphExtractionState | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM graph_extraction_state WHERE doc_id=?", (doc_id,)
            ).fetchone()
        return self._row_to_state(row) if row else None

    def enqueue_document(
        self,
        doc_id: str,
        *,
        model: str,
        prompt_version: str,
        force: bool = False,
    ) -> None:
        now = _now()
        allowed = (
            "graph_extraction_state.status != 'processing'"
            if force
            else "graph_extraction_state.status IN ('pending','failed','skipped')"
        )
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                f"""INSERT INTO graph_extraction_state
                    (doc_id,status,attempt,model,prompt_version,updated_at)
                    VALUES (?, 'pending', 1, ?, ?, ?)
                    ON CONFLICT(doc_id) DO UPDATE SET
                      status='pending',
                      attempt=graph_extraction_state.attempt + 1,
                      model=excluded.model,
                      prompt_version=excluded.prompt_version,
                      lease_owner=NULL,
                      lease_expires_at=NULL,
                      error_code='',
                      error_message='',
                      updated_at=excluded.updated_at
                    WHERE {allowed}""",
                (doc_id, model, prompt_version, now),
            )

    def claim_document(
        self, doc_id: str, *, lease_seconds: int = _DEFAULT_LEASE_SECONDS
    ) -> GraphExtractionState | None:
        now = datetime.now(timezone.utc)
        with self._db.connect(immediate=True) as conn:
            row = conn.execute(
                "SELECT * FROM graph_extraction_state WHERE doc_id=?", (doc_id,)
            ).fetchone()
            if row is None or row["status"] != "pending":
                return None
            expires = (now + timedelta(seconds=lease_seconds)).isoformat(
                timespec="seconds"
            )
            conn.execute(
                """UPDATE graph_extraction_state
                   SET status='processing', lease_owner='graph-worker',
                       lease_expires_at=?, updated_at=?
                   WHERE doc_id=? AND status='pending'""",
                (expires, now.isoformat(timespec="seconds"), doc_id),
            )
            updated = conn.execute(
                "SELECT * FROM graph_extraction_state WHERE doc_id=?", (doc_id,)
            ).fetchone()
        return self._row_to_state(updated) if updated else None

    def update_progress(self, doc_id: str, attempt: int, *, chunk_done: int) -> None:
        with self._db.connect(immediate=True) as conn:
            conn.execute(
                """UPDATE graph_extraction_state
                   SET chunk_done=?, updated_at=?
                   WHERE doc_id=? AND attempt=? AND status='processing'""",
                (max(0, chunk_done), _now(), doc_id, attempt),
            )

    def finish_document(
        self,
        doc_id: str,
        attempt: int,
        *,
        status: str,
        chunk_total: int = 0,
        chunk_done: int = 0,
        entity_count: int = 0,
        relation_count: int = 0,
        truncated: bool = False,
    ) -> bool:
        now = _now()
        with self._db.connect(immediate=True) as conn:
            cursor = conn.execute(
                """UPDATE graph_extraction_state
                   SET status=?, chunk_total=?, chunk_done=?, entity_count=?,
                       relation_count=?, truncated=?, error_code='', error_message='',
                       lease_owner=NULL, lease_expires_at=NULL,
                       extracted_at=?, updated_at=?
                   WHERE doc_id=? AND attempt=? AND status='processing'""",
                (
                    status,
                    chunk_total,
                    chunk_done,
                    entity_count,
                    relation_count,
                    1 if truncated else 0,
                    now,
                    now,
                    doc_id,
                    attempt,
                ),
            )
            return cursor.rowcount > 0

    def fail_document(
        self, doc_id: str, attempt: int, code: str, message: str
    ) -> bool:
        with self._db.connect(immediate=True) as conn:
            cursor = conn.execute(
                """UPDATE graph_extraction_state
                   SET status='failed', error_code=?, error_message=?,
                       lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                   WHERE doc_id=? AND attempt=? AND status='processing'""",
                (code, message[:500], _now(), doc_id, attempt),
            )
            return cursor.rowcount > 0

    def reset_expired_leases(
        self, *, lease_seconds: int = _DEFAULT_LEASE_SECONDS
    ) -> list[str]:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=lease_seconds)
        ).isoformat(timespec="seconds")
        with self._db.connect(immediate=True) as conn:
            rows = conn.execute(
                """SELECT doc_id FROM graph_extraction_state
                   WHERE status='processing'
                     AND (lease_expires_at IS NULL OR lease_expires_at < ?)""",
                (cutoff,),
            ).fetchall()
            doc_ids = [row[0] for row in rows]
            for doc_id in doc_ids:
                conn.execute(
                    """UPDATE graph_extraction_state
                       SET status='pending', attempt=attempt + 1,
                           lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                       WHERE doc_id=? AND status='processing'""",
                    (_now(), doc_id),
                )
        return doc_ids

    def list_documents_for_extraction(
        self, kb_id: str, statuses: Sequence[str] = ()
    ) -> list[str]:
        sql = """
          SELECT d.id FROM documents d
          LEFT JOIN graph_extraction_state s ON s.doc_id = d.id
          WHERE d.kb_id = ? AND d.parse_status = 'ready'
        """
        params: list[Any] = [kb_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND (s.doc_id IS NULL OR s.status IN ({placeholders}))"
            params.extend(statuses)
        sql += " ORDER BY d.created_at ASC, d.id ASC"
        with self._db.connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [row[0] for row in rows]

    def list_pending_documents(self) -> list[str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT doc_id FROM graph_extraction_state WHERE status='pending'"
                " ORDER BY updated_at ASC, doc_id ASC"
            ).fetchall()
        return [row[0] for row in rows]

    def entity_ids_for_document(self, doc_id: str) -> list[str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT entity_id FROM graph_mentions WHERE doc_id=?",
                (doc_id,),
            ).fetchall()
        return [row[0] for row in rows]

    # ---------- 行映射 ----------
    @staticmethod
    def _row_to_entity(row: sqlite3.Row) -> GraphEntity:
        return GraphEntity(
            id=row["id"],
            kb_id=row["kb_id"],
            name=row["name"],
            name_norm=row["name_norm"],
            type=row["type"],
            description=row["description"],
            mention_count=int(row["mention_count"]),
        )

    @staticmethod
    def _row_to_relation(row: sqlite3.Row) -> GraphRelation:
        return GraphRelation(
            id=row["id"],
            kb_id=row["kb_id"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            relation=row["relation"],
            description=row["description"],
            weight=float(row["weight"]),
            mention_count=int(row["mention_count"]),
        )

    @staticmethod
    def _row_to_state(row: sqlite3.Row) -> GraphExtractionState:
        return GraphExtractionState(
            doc_id=row["doc_id"],
            status=row["status"],
            attempt=int(row["attempt"]),
            chunk_total=int(row["chunk_total"]),
            chunk_done=int(row["chunk_done"]),
            entity_count=int(row["entity_count"]),
            relation_count=int(row["relation_count"]),
            truncated=bool(row["truncated"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            extracted_at=row["extracted_at"],
            updated_at=row["updated_at"],
        )
