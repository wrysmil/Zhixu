"""Measure real per-chunk storage cost for the spec's capacity model.

Runs two corpora to bracket real-world behavior:
  repeated — a small phrase pool repeated (few distinct bigrams, optimistic)
  diverse  — random chars from a 500-char pool (many distinct bigrams, pessimistic)
"""

import random
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_ddl import tokenize_index

SPEC = Path(
    "/Users/mima0000/Documents/学习-001/do-project/zhixu/"
    ".ai-runtime-artifacts/specs/2026-11-09-knowledge-storage-spec.md"
)
ddl = next(
    b
    for b in re.findall(r"```sql\n(.*?)```", SPEC.read_text(encoding="utf-8"), re.S)
    if "_schema_meta" in b and "document_chunks_fts" in b
)

POOL = (
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动"
    "同工也能下过子说产种面而方后多定行学法所民得经十三之进着等部度家电力里如水化高自"
    "二理起小物现实加量都两体制机当使点从业本去把性好应开它合还因由其些然前外天政四日"
    "那社义事平形相全表间样与关各重新线内数正心反你明看原又么利比或但质气第向道命此变"
    "条只没结解问意建月公无系军很情者最立代想已通并提直题党程展五果料象员革位入常文总"
    "次品式活设及管特件长求老头基资边流路级少图山统接知较将组见计别她手角期根论运农指"
    "几九区强放决西被干做必战先回则任取据处队南给色光门即保治北造百规热领七海口东导器"
    "压志世金增争济阶油思术极交受联什认六共权收证改清己美再采转更单风切打白教速花带安"
)

REPEATED = (
    "知识库需要把用户上传的文档解析成连续可检索的文本切片，"
    "并在写入数据库之前完成归一化与分词。部署失败时应当回滚事务，"
    "避免留下半成品索引；检索路径只读取已经就绪的文档，"
    "任何失败都必须显式上报而不是返回空结果。"
)

N_DOCS = 300
CHUNKS_PER_DOC = 10
TOTAL = N_DOCS * CHUNKS_PER_DOC
CHUNK_CHARS = 800


def make_repeated(i: int) -> str:
    base = REPEATED * 20
    start = (i * 37) % (len(base) - CHUNK_CHARS - 1)
    return base[start : start + CHUNK_CHARS]


def make_diverse(i: int) -> str:
    rng = random.Random(20261109 + i)
    chars = [rng.choice(POOL) for _ in range(CHUNK_CHARS)]
    for pos in range(40, CHUNK_CHARS, 40):
        chars[pos] = "，"
    chars[-1] = "。"
    return "".join(chars)


def measure(label: str, make_chunk) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix=f"kc-size-{label}-")) / "state.db"
    conn = sqlite3.connect(tmp, isolation_level=None)
    conn.executescript("BEGIN IMMEDIATE;\n" + ddl + "\nCOMMIT;")
    conn.execute(
        "INSERT INTO knowledge_bases (id,name,workspace_id,created_at,updated_at)"
        " VALUES ('kb_a','n','ws1','t','t')"
    )
    conn.execute("BEGIN IMMEDIATE")
    for d in range(N_DOCS):
        doc = f"doc_{d}"
        conn.execute(
            "INSERT INTO documents (id,kb_id,workspace_id,title,source_path,"
            "created_at,updated_at) VALUES (?,?,?,?,?,'t','t')",
            (doc, "kb_a", "ws1", doc, f"/tmp/{doc}.md"),
        )
        for j in range(CHUNKS_PER_DOC):
            content = make_chunk(d * CHUNKS_PER_DOC + j)
            conn.execute(
                "INSERT INTO document_chunks (id,doc_id,kb_id,workspace_id,"
                "chunk_index,content,char_count,search_text,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,'t')",
                (
                    f"chk_{d}_{j}",
                    doc,
                    "kb_a",
                    "ws1",
                    j,
                    content,
                    len(content),
                    tokenize_index(content),
                ),
            )
    conn.execute("COMMIT")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    content_b = conn.execute(
        "SELECT sum(length(CAST(content AS BLOB))) FROM document_chunks"
    ).fetchone()[0]
    search_b = conn.execute(
        "SELECT sum(length(CAST(search_text AS BLOB))) FROM document_chunks"
    ).fetchone()[0]
    tokens = conn.execute(
        "SELECT sum(length(search_text) - length(replace(search_text, ' ', '')) + 1)"
        " FROM document_chunks"
    ).fetchone()[0]
    pages = dict(
        conn.execute("SELECT name, sum(pgsize) FROM dbstat GROUP BY name").fetchall()
    )
    db_bytes = tmp.stat().st_size
    conn.close()

    return {
        "label": label,
        "content": content_b / TOTAL,
        "search_text": search_b / TOTAL,
        "tokens": tokens / TOTAL,
        "chunk_table": pages.get("document_chunks", 0) / TOTAL,
        "fts_data": pages.get("document_chunks_fts_data", 0) / TOTAL,
        "fts_other": (
            pages.get("document_chunks_fts_idx", 0)
            + pages.get("document_chunks_fts_docsize", 0)
            + pages.get("document_chunks_fts_config", 0)
        )
        / TOTAL,
        "db": db_bytes / TOTAL,
    }


def main() -> None:
    print(f"chunks={TOTAL} chunk_chars={CHUNK_CHARS}")
    print(
        f"{'corpus':10s} {'tokens':>7s} {'content':>9s} {'search':>9s} "
        f"{'chunk_tab':>10s} {'fts_data':>9s} {'fts_other':>10s} "
        f"{'db/chunk':>9s} {'10k chunks':>11s}"
    )
    for label, fn in (("repeated", make_repeated), ("diverse", make_diverse)):
        r = measure(label, fn)
        print(
            f"{r['label']:10s} {r['tokens']:7.0f} {r['content'] / 1024:8.2f}K "
            f"{r['search_text'] / 1024:8.2f}K {r['chunk_table'] / 1024:9.2f}K "
            f"{r['fts_data'] / 1024:8.2f}K {r['fts_other'] / 1024:9.2f}K "
            f"{r['db'] / 1024:8.2f}K "
            f"{r['db'] * 10_000 / 1024 / 1024:10.0f} MB"
        )


if __name__ == "__main__":
    main()
