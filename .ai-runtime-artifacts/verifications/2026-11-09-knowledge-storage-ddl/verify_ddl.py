"""Execute the storage spec's §6 DDL verbatim and assert its invariants."""

import re
import sqlite3
import sys
import tempfile
import unicodedata
from pathlib import Path

SPEC = Path(
    "/Users/mima0000/Documents/学习-001/do-project/zhixu/"
    ".ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md"
)


def extract_ddl(text: str) -> str:
    blocks = re.findall(r"```sql\n(.*?)```", text, re.S)
    for block in blocks:
        if "_schema_meta" in block and "document_chunks_fts" in block:
            return block
    raise SystemExit("DDL block not found")


_ASCII_SEPARATORS = frozenset("-_.:/#@")


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0xF900 <= o <= 0xFAFF
        or 0x3040 <= o <= 0x30FF
        or 0xAC00 <= o <= 0xD7AF
    )


def _cjk_run_tokens(run: str, mode: str) -> list[str]:
    if not run:
        return []
    out = list(run)
    bigrams = [run[i : i + 2] for i in range(len(run) - 1)]
    if mode == "unigram_bigram":
        out.extend(bigrams)
    else:
        out = bigrams or [run]
    return out


def tokenize_index(text: str, *, mode: str = "unigram_bigram") -> str:
    normalized = unicodedata.normalize("NFKC", text)
    tokens: list[str] = []
    i, n = 0, len(normalized)
    while i < n:
        ch = normalized[i]
        if _is_cjk(ch):
            j = i
            while j < n and _is_cjk(normalized[j]):
                j += 1
            tokens.extend(_cjk_run_tokens(normalized[i:j], mode))
            i = j
            continue
        if ch.isalnum():
            parts: list[str] = []
            j = i
            while j < n and (
                normalized[j].isalnum() or normalized[j] in _ASCII_SEPARATORS
            ):
                if normalized[j].isalnum():
                    k = j
                    while k < n and normalized[k].isalnum():
                        k += 1
                    parts.append(normalized[j:k].lower())
                    j = k
                else:
                    j += 1
            tokens.extend(parts)
            compact = "".join(parts)
            if len(parts) > 1 and 2 <= len(compact) <= 32:
                tokens.append(compact)
            i = j
            continue
        i += 1
    return " ".join(tokens)


def tokenize_query(text: str, *, mode: str = "unigram_bigram", limit: int = 64) -> str:
    tokens = tokenize_index(text, mode=mode).split()
    seen: list[str] = []
    for tok in tokens:
        cleaned = "".join(ch for ch in tok if ch.isalnum() or _is_cjk(ch))
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) >= limit:
            break
    return " OR ".join(f'"{tok}"' for tok in seen)


def main() -> None:
    ddl = extract_ddl(SPEC.read_text(encoding="utf-8"))
    tmpdir = tempfile.mkdtemp(prefix="kc-ddl-")
    db = Path(tmpdir) / "state.db"

    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    # executescript() 会先隐式提交，故把 BEGIN/COMMIT 放进脚本本身，
    # 与方案 §5「迁移在 BEGIN IMMEDIATE 内完成」一致。
    conn.executescript("BEGIN IMMEDIATE;\n" + ddl + "\nCOMMIT;")
    print("OK  schema created")

    # --- seed -------------------------------------------------------------
    conn.execute("BEGIN IMMEDIATE")
    conn.execute(
        "INSERT INTO knowledge_bases (id, name, workspace_id, visibility,"
        " embedding_backend, embedding_model, embedding_dimensions,"
        " created_at, updated_at)"
        " VALUES ('kb_a','笔记','ws1','workspace','local','m',384,'t','t')"
    )
    conn.execute(
        "INSERT INTO knowledge_bases (id, name, workspace_id, visibility,"
        " embedding_backend, embedding_model, embedding_dimensions,"
        " created_at, updated_at)"
        " VALUES ('kb_b','公共','ws1','global','none','',0,'t','t')"
    )
    for doc_id, status in (("doc_1", "ready"), ("doc_2", "failed")):
        conn.execute(
            "INSERT INTO documents (id, kb_id, workspace_id, title, source_path,"
            " parse_status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,'t','t')",
            (doc_id, "kb_a", "ws1", doc_id, f"/tmp/{doc_id}.md", status),
        )
    texts = [
        ("chk_1", "doc_1", "kb_a", "ws1", 0, "用户热爱创作，长期坚持写小说。"),
        ("chk_2", "doc_1", "kb_a", "ws1", 1, "模型 e-1024 的参数规模是十亿。"),
        ("chk_3", "doc_2", "kb_a", "ws1", 0, "这篇文档解析失败，不应被检索到。"),
    ]
    for cid, doc, kb, ws, idx, content in texts:
        conn.execute(
            "INSERT INTO document_chunks (id, doc_id, kb_id, workspace_id,"
            " chunk_index, content, search_text, created_at)"
            " VALUES (?,?,?,?,?,?,?,'t')",
            (cid, doc, kb, ws, idx, content, tokenize_index(content)),
        )
    conn.execute("COMMIT")
    print("OK  seed")

    # --- FTS sync via AFTER INSERT trigger --------------------------------
    chunks = conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
    fts = conn.execute("SELECT count(*) FROM document_chunks_fts").fetchone()[0]
    assert (chunks, fts) == (3, 3), (chunks, fts)
    print(f"OK  fts auto-synced rows={fts}")

    # --- keyword query: CROSS JOIN + scope + status + enabled ------------
    def keyword(q: str) -> list[str]:
        match = tokenize_query(q)
        sql = f"""
        SELECT c.id, -bm25(document_chunks_fts) AS s
        FROM document_chunks_fts
        CROSS JOIN document_chunks c ON c.row_id = document_chunks_fts.rowid
        CROSS JOIN documents d ON d.id = c.doc_id
        CROSS JOIN knowledge_bases kb ON kb.id = c.kb_id
        WHERE document_chunks_fts MATCH ?
          AND d.parse_status = 'ready' AND d.is_enabled = 1
          AND c.is_enabled = 1 AND kb.is_enabled = 1
          AND (kb.visibility = 'global' OR c.workspace_id IN ('ws1'))
        ORDER BY document_chunks_fts.rank
        LIMIT 20
        """
        return [r["id"] for r in conn.execute(sql, (match,))]

    hits = keyword("创作")
    assert hits == ["chk_1"], hits
    print(f"OK  CJK substring recall 创作 -> {hits}")

    hits = keyword("E-1024")
    assert hits == ["chk_2"], hits
    print(f"OK  compact ascii recall E-1024 -> {hits}")

    # failed document must never surface
    assert "chk_3" not in keyword("解析"), keyword("解析")
    print("OK  parse_status filter holds")

    # scope filter: another workspace sees only global
    hits = [
        r["id"]
        for r in conn.execute(
            f"""
            SELECT c.id FROM document_chunks_fts
            CROSS JOIN document_chunks c ON c.row_id = document_chunks_fts.rowid
            CROSS JOIN documents d ON d.id = c.doc_id
            CROSS JOIN knowledge_bases kb ON kb.id = c.kb_id
            WHERE document_chunks_fts MATCH ?
              AND d.parse_status = 'ready'
              AND (kb.visibility = 'global' OR c.workspace_id IN ('ws2'))
            """,
            ('"创作" OR "小说"',),
        )
    ]
    assert hits == [], hits
    print("OK  workspace isolation holds")

    # --- immutability -----------------------------------------------------
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE document_chunks SET content='x' WHERE id='chk_1'")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK")
        assert "CHUNK_IMMUTABLE" in str(exc), exc
        print("OK  content update blocked")
    else:
        raise SystemExit("immutability trigger did not fire")

    # mutable columns still work
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE document_chunks SET is_enabled=0 WHERE id='chk_1'")
    conn.execute("COMMIT")
    print("OK  mutable column (is_enabled) still writable")

    # --- cascade delete clears FTS ---------------------------------------
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM documents WHERE id='doc_2'")
    conn.execute("COMMIT")
    fts = conn.execute("SELECT count(*) FROM document_chunks_fts").fetchone()[0]
    chunks = conn.execute("SELECT count(*) FROM document_chunks").fetchone()[0]
    assert (chunks, fts) == (2, 2), (chunks, fts)
    print(f"OK  cascade delete kept FTS in sync rows={fts}")

    # deletes leave no ghost posting
    assert keyword("解析") == []
    print("OK  deleted chunk no longer matchable")

    # --- KB cascade removes documents + chunks + FTS ----------------------
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("DELETE FROM knowledge_bases WHERE id='kb_a'")
    conn.execute("COMMIT")
    fts = conn.execute("SELECT count(*) FROM document_chunks_fts").fetchone()[0]
    assert fts == 0, fts
    print("OK  kb cascade clears fts")

    # --- integrity check / optimize / rebuild ----------------------------
    conn.execute("INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('optimize')")
    conn.execute(
        "INSERT INTO document_chunks_fts(document_chunks_fts) VALUES('rebuild')"
    )
    print("OK  optimize + rebuild accepted")

    # --- CHECK constraints ------------------------------------------------
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO knowledge_bases (id, name, workspace_id, visibility,"
            " chunk_size, chunk_overlap, embedding_backend, embedding_model,"
            " embedding_dimensions, created_at, updated_at)"
            " VALUES ('kb_x','x','ws','workspace',300,400,'none','',0,'t','t')"
        )
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK")
        print(f"OK  overlap>=size rejected: {exc}")
    else:
        raise SystemExit("chunk_overlap CHECK did not fire")

    conn.close()
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    sys.exit(main())
