"""上下文装配（WeKnora §9.1 CHUNK_MERGE）：把「一条 chunk」变成「一段读得通的上下文」。

文章的口径与实现取舍：

- **子块 → 父块回填**：子块召回准，但上下文不足。我们不存父块表（不动存储层），
  改为**按需合成父块**——以命中块为中心，在同文档同标题路径下合并相邻块，
  上限 `parent_max_chars`。达成的效果与文章一致：召回的是子块，给模型的是完整段落。
- **合并连续正文**：同文档、标题路径相同、块序号连续的命中块合并成一段。
- **短上下文向邻居扩展**：命中块本身太短（`short_chunk_chars`）时扩读邻居，
  避免一句话被切在两块之间时断章取义。
- **最终去重**：ID + 内容签名 + 部分包含（§8 三级判据）。
- **注入历史引用**：由调用方传入此前会话里被引用过的块，作为补充上下文。

装配**不改变相关性**：分数沿用精排后的复合分，只改内容与来源块。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import replace

from nanobot.knowledge.fusion import SearchHit, dedup_partial
from nanobot.knowledge.repository import StoredChunk

#: 邻块读取回调：(chunk, before, after) → 同文档邻块（按 chunk_index 升序）。
NeighborLoader = Callable[[StoredChunk, int, int], Sequence[StoredChunk]]

#: 文档标题读取回调：doc_id → 标题。
TitleLoader = Callable[[str], str]


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _neighbor_window(hit: SearchHit, short_chunk_chars: int) -> int:
    """短块读更多邻居；正常块只读前后各 1 块。"""
    if hit.char_count < short_chunk_chars:
        return 2
    return 1


def _merge_texts(texts: Sequence[str]) -> str:
    """按顺序拼接块正文（保留原文，不做改写）。"""
    return "\n".join(text for text in texts if text)


def _parent_block(
    hit: SearchHit,
    *,
    chunk: StoredChunk,
    title: str,
    load_neighbors: NeighborLoader,
    parent_max_chars: int,
    parent_min_chars: int,
    short_chunk_chars: int,
) -> SearchHit:
    """父块回填：命中块太短时，用同标题路径的相邻块把它补成一段完整上下文。

    我们不存父块表，因此父块是**按需合成**的：以命中块为中心合并相邻块，
    上限 `parent_max_chars`。只有短于 `parent_min_chars` 的命中块才回填——
    已经足够长的块再拼邻块只会稀释证据、撑爆输出预算。
    合并顺序按块序号，命中块自身一定在内（上下文可以少，证据不能少）。
    """
    if hit.char_count >= parent_min_chars:
        return hit
    window = _neighbor_window(hit, short_chunk_chars)
    if window <= 0:
        return hit
    neighbors = [
        item
        for item in load_neighbors(chunk, window, window)
        if item.heading_path == hit.heading_path
    ]
    if not neighbors:
        return hit
    merged = _merge_texts([item.content for item in neighbors])
    if not merged.strip() or merged == hit.content:
        return hit
    if len(merged) > parent_max_chars:
        merged = merged[:parent_max_chars]
    return replace(
        hit,
        content=merged,
        char_count=len(merged),
        content_hash=_content_hash(merged),
        title=title or hit.title,
        source_chunk_ids=tuple(item.id for item in neighbors),
    )


def _merge_consecutive(hits: Sequence[SearchHit]) -> list[SearchHit]:
    """合并连续正文：同文档、同标题路径、块序号相邻的命中块拼成一段。"""
    merged: list[SearchHit] = []
    for hit in hits:
        if merged:
            previous = merged[-1]
            if (
                previous.doc_id == hit.doc_id
                and previous.heading_path == hit.heading_path
                and hit.chunk_index == previous.chunk_index + 1
            ):
                content = _merge_texts([previous.content, hit.content])
                merged[-1] = replace(
                    previous,
                    content=content,
                    char_count=len(content),
                    content_hash=_content_hash(content),
                    source_chunk_ids=previous.source_chunk_ids + (hit.chunk_id,),
                )
                continue
        merged.append(hit)
    return merged


def assemble_context(
    hits: Sequence[SearchHit],
    *,
    chunks: dict[str, StoredChunk],
    load_neighbors: NeighborLoader,
    load_title: TitleLoader,
    parent_max_chars: int = 2400,
    parent_min_chars: int = 600,
    short_chunk_chars: int = 200,
    extra_blocks: Sequence[SearchHit] = (),
) -> list[SearchHit]:
    """装配上下文（§9.1）。

    `hits` 是精排 + MMR 之后的命中块；`chunks` 是这些块的存储态（用于取邻块）；
    `extra_blocks` 是注入的历史引用块（§9.1 ③），排在最后。
    """
    if not hits and not extra_blocks:
        return []

    parents: list[SearchHit] = []
    for hit in hits:
        chunk = chunks.get(hit.chunk_id)
        if chunk is None:
            parents.append(hit)
            continue
        title = hit.title or load_title(hit.doc_id)
        parents.append(
            _parent_block(
                hit,
                chunk=chunk,
                title=title,
                load_neighbors=load_neighbors,
                parent_max_chars=parent_max_chars,
                parent_min_chars=parent_min_chars,
                short_chunk_chars=short_chunk_chars,
            )
        )

    merged = _merge_consecutive(parents)
    combined = list(merged) + list(extra_blocks)
    # §8/§9.1 ⑧：最终去重（ID + 签名 + 部分包含）。
    return dedup_partial(combined)
