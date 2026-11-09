"""Check that the spec's §7.3 query keeps FTS5 as the driving loop."""

import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_ddl import tokenize_index, tokenize_query
import tempfile

SPEC = Path(
    "/Users/mima0000/Documents/学习-001/do-project/zhixu/"
    ".ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md"
)
ddl = next(
    b
    for b in re.findall(r"```sql\n(.*?)```", SPEC.read_text(encoding="utf-8"), re.S)
    if "_schema_meta" in b and "document_chunks_fts" in b
)

tmp = Path(tempfile.mkdtemp(prefix="kc-eqp-")) / "state.db"
conn = sqlite3.connect(tmp, isolation_level=None)
conn.executescript("BEGIN IMMEDIATE;\n" + ddl + "\nCOMMIT;")

conn.execute("BEGIN IMMEDIATE")
conn.execute(
    "INSERT INTO knowledge_bases (id,name,workspace_id,visibility,created_at,updated_at)"
    " VALUES ('kb_a','n','ws1','workspace','t','t')"
)
for i in range(1250):
    doc = f"doc_{i}"
    conn.execute(
        "INSERT INTO documents (id,kb_id,workspace_id,title,source_path,parse_status,"
        "created_at,updated_at) VALUES (?,?,?,?,?,'ready','t','t')",
        (doc, "kb_a", "ws1", doc, f"/tmp/{doc}.md"),
    )
    for j in range(40):
        content = f"用户热爱创作 {j}" if i < 5 else f"普通内容 {j}"
        conn.execute(
            "INSERT INTO document_chunks (id,doc_id,kb_id,workspace_id,chunk_index,"
            "content,search_text,created_at) VALUES (?,?,?,?,?,?,?,'t')",
            (
                f"chk_{i}_{j}",
                doc,
                "kb_a",
                "ws1",
                j,
                content,
                tokenize_index(content),
            ),
        )
conn.execute("COMMIT")

SPEC_SQL = """
SELECT c.id FROM document_chunks_fts
CROSS JOIN document_chunks c ON c.row_id = document_chunks_fts.rowid
JOIN documents d        ON d.id = c.doc_id
JOIN knowledge_bases kb ON kb.id = c.kb_id
WHERE document_chunks_fts MATCH ?
  AND c.is_enabled = 1
  AND d.is_enabled = 1
  AND d.parse_status = 'ready'
  AND kb.is_enabled = 1
  AND (kb.visibility = 'global' OR kb.workspace_id IN (?, ?))
ORDER BY document_chunks_fts.rank
LIMIT 20
"""

PLAIN_SQL = SPEC_SQL.replace("CROSS JOIN document_chunks", "JOIN document_chunks")

for name, sql in (("spec(CROSS JOIN)", SPEC_SQL), ("plain JOIN", PLAIN_SQL)):
    plan = [r[3] for r in conn.execute(
        "EXPLAIN QUERY PLAN " + sql, (tokenize_query("创作"), "ws1", "ws1")
    )]
    print(f"--- {name}")
    for line in plan:
        print("   ", line)

print("--- timing (best of 5)")
import time

for name, sql in (("spec(CROSS JOIN)", SPEC_SQL), ("plain JOIN", PLAIN_SQL)):
    samples = []
    for _ in range(5):
        t0 = time.perf_counter()
        rows = conn.execute(sql, (tokenize_query("创作"), "ws1", "ws1")).fetchall()
        assert len(rows) == 20, len(rows)
        samples.append(time.perf_counter() - t0)
    print(f"    {name}: {min(samples) * 1000:.1f} ms  (rows=20)")

conn.close()
