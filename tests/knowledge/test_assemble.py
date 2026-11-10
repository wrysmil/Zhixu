"""上下文装配（WeKnora §9.1 CHUNK_MERGE）：父块回填 / 连续合并 / 最终去重。"""

from __future__ import annotations

from collections.abc import Sequence

from nanobot.knowledge.assemble import assemble_context
from nanobot.knowledge.fusion import SearchHit
from nanobot.knowledge.repository import StoredChunk


def _stored(
    chunk_id: str,
    content: str,
    *,
    doc_id: str = "doc_1",
    chunk_index: int = 0,
    heading_path: str = "第三章",
    kb_id: str = "kb_1",
) -> StoredChunk:
    return StoredChunk(
        row_id=chunk_index + 1,
        id=chunk_id,
        doc_id=doc_id,
        kb_id=kb_id,
        workspace_id="ws1",
        chunk_index=chunk_index,
        content=content,
        heading_path=heading_path,
        page=1,
        locator="",
        start_offset=0,
        end_offset=len(content),
        char_count=len(content),
        content_hash="",
        is_enabled=True,
    )


def _hit(chunk: StoredChunk, score: float = 1.0, *, title: str = "") -> SearchHit:
    return SearchHit(
        chunk_id=chunk.id,
        doc_id=chunk.doc_id,
        kb_id=chunk.kb_id,
        chunk_index=chunk.chunk_index,
        heading_path=chunk.heading_path,
        page=chunk.page,
        locator=chunk.locator,
        content=chunk.content,
        char_count=chunk.char_count,
        content_hash=chunk.content_hash,
        score=score,
        title=title,
    )


def _load_neighbors(chunks: Sequence[StoredChunk]):
    def load(chunk: StoredChunk, before: int, after: int) -> list[StoredChunk]:
        return [
            item
            for item in sorted(chunks, key=lambda entry: entry.chunk_index)
            if item.doc_id == chunk.doc_id
            and chunk.chunk_index - before <= item.chunk_index <= chunk.chunk_index + after
        ]

    return load


def _assemble(
    hits: Sequence[SearchHit],
    chunks: Sequence[StoredChunk],
    *,
    extra: Sequence[SearchHit] = (),
    parent_max_chars: int = 2400,
    parent_min_chars: int = 600,
    short_chunk_chars: int = 200,
    titles: dict[str, str] | None = None,
) -> list[SearchHit]:
    return assemble_context(
        hits,
        chunks={chunk.id: chunk for chunk in chunks},
        load_neighbors=_load_neighbors(chunks),
        load_title=lambda doc_id: (titles or {}).get(doc_id, ""),
        parent_max_chars=parent_max_chars,
        parent_min_chars=parent_min_chars,
        short_chunk_chars=short_chunk_chars,
        extra_blocks=extra,
    )


# ---------- 父块回填 ----------


def test_parent_backfill_merges_same_heading_neighbors() -> None:
    chunks = [
        _stored("chk_0", "第一段正文", chunk_index=0),
        _stored("chk_1", "第二段正文", chunk_index=1),
        _stored("chk_2", "第三段正文", chunk_index=2),
    ]
    result = _assemble([_hit(chunks[1])], chunks, titles={"doc_1": "发布手册"})

    assert len(result) == 1
    assert result[0].content == "第一段正文\n第二段正文\n第三段正文"
    assert result[0].char_count == len(result[0].content)
    assert result[0].source_chunk_ids == ("chk_0", "chk_1", "chk_2")
    assert result[0].title == "发布手册"


def test_parent_backfill_skips_blocks_already_long_enough() -> None:
    long_text = "长" * 700
    chunks = [
        _stored("chk_0", "前一块", chunk_index=0),
        _stored("chk_1", long_text, chunk_index=1),
    ]
    result = _assemble([_hit(chunks[1])], chunks)

    assert [hit.chunk_id for hit in result] == ["chk_1"]
    assert result[0].content == long_text
    assert result[0].source_chunk_ids == ()


def test_parent_backfill_only_uses_same_heading_neighbors() -> None:
    chunks = [
        _stored("chk_0", "别的章节", chunk_index=0, heading_path="第四章"),
        _stored("chk_1", "本章内容", chunk_index=1, heading_path="第三章"),
    ]
    result = _assemble([_hit(chunks[1])], chunks)

    # 只有自身同标题路径 → 没有可合并的邻居，保持原块。
    assert [hit.chunk_id for hit in result] == ["chk_1"]
    assert result[0].content == "本章内容"


def test_parent_backfill_truncates_at_parent_max_chars() -> None:
    chunks = [
        _stored("chk_0", "甲" * 20, chunk_index=0),
        _stored("chk_1", "乙" * 20, chunk_index=1),
    ]
    result = _assemble([_hit(chunks[1])], chunks, parent_max_chars=15)

    assert len(result[0].content) == 15
    assert result[0].char_count == 15


def test_short_chunk_expands_two_neighbors() -> None:
    chunks = [
        _stored("chk_0", "零", chunk_index=0),
        _stored("chk_1", "一", chunk_index=1),
        _stored("chk_2", "二", chunk_index=2),
        _stored("chk_3", "三", chunk_index=3),
        _stored("chk_4", "四", chunk_index=4),
    ]
    windows: list[tuple[int, int]] = []

    def load(chunk: StoredChunk, before: int, after: int) -> list[StoredChunk]:
        windows.append((before, after))
        return [
            item
            for item in chunks
            if chunk.chunk_index - before <= item.chunk_index <= chunk.chunk_index + after
        ]

    assemble_context(
        [_hit(chunks[2])],
        chunks={chunk.id: chunk for chunk in chunks},
        load_neighbors=load,
        load_title=lambda doc_id: "",
        short_chunk_chars=200,
    )

    # 命中块很短（1 字符 < 200）→ 前后各扩 2 块。
    assert windows == [(2, 2)]


# ---------- 连续正文合并 ----------


def test_consecutive_blocks_are_merged() -> None:
    first = _stored("chk_0", "甲" * 700, chunk_index=0)
    second = _stored("chk_1", "乙" * 700, chunk_index=1)
    result = _assemble([_hit(first), _hit(second)], [first, second])

    assert len(result) == 1
    assert result[0].chunk_id == "chk_0"
    assert result[0].content == "甲" * 700 + "\n" + "乙" * 700
    assert result[0].source_chunk_ids == ("chk_1",)
    assert result[0].char_count == len(result[0].content)


def test_non_consecutive_blocks_are_not_merged() -> None:
    first = _stored("chk_0", "甲" * 700, chunk_index=0)
    third = _stored("chk_2", "丙" * 700, chunk_index=2)
    result = _assemble([_hit(first), _hit(third)], [first, third])

    assert [hit.chunk_id for hit in result] == ["chk_0", "chk_2"]


def test_different_heading_blocks_are_not_merged() -> None:
    first = _stored("chk_0", "甲" * 700, chunk_index=0, heading_path="第三章")
    second = _stored("chk_1", "乙" * 700, chunk_index=1, heading_path="第四章")
    result = _assemble([_hit(first), _hit(second)], [first, second])

    assert [hit.chunk_id for hit in result] == ["chk_0", "chk_1"]


# ---------- 最终去重 ----------


def test_identical_blocks_are_deduped() -> None:
    first = _stored("chk_0", "同样的内容", chunk_index=0)
    second = _stored("chk_1", "同样的内容", chunk_index=1, heading_path="第四章")
    result = _assemble([_hit(first), _hit(second)], [first, second])

    assert len(result) == 1


def test_history_blocks_are_appended_and_deduped() -> None:
    hit_chunk = _stored("chk_0", "命中正文", chunk_index=0)
    history_chunk = _stored("chk_9", "历史引用正文", chunk_index=9)
    duplicate = _stored("chk_8", "命中正文", chunk_index=8)

    result = _assemble(
        [_hit(hit_chunk)],
        [hit_chunk, duplicate, history_chunk],
        extra=[_hit(history_chunk, score=0.0), _hit(duplicate, score=0.0)],
    )

    assert [hit.chunk_id for hit in result] == ["chk_0", "chk_9"]
    assert result[1].score == 0.0


def test_empty_input_returns_empty() -> None:
    assert _assemble([], []) == []
