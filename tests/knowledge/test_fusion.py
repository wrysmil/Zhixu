"""融合层测试：三分支归一化（A cosine / B BM25 相对分 / C 加权 RRF）、三级去重、邻块下限 + 相关性带 + MMR。

手算基准（spec §5.2 / §9.1；分支 C：k=60、w_v=0.7、w_k=0.3、maxRRF=(0.7+0.3)/61）：
  两路 rank1 同一块        = 1.0
  仅向量 rank1             = 0.7
  仅关键词 rank1           = 0.3
  向量 rank1 + 关键词 rank2 = 0.7 + 0.3×61/62 ≈ 0.995161
  两路 rank2               = (0.7+0.3)×61/62 ≈ 0.983871

夹具正文用空格分隔的 ASCII 词构造（tokenizer 中一个 alnum run = 一个 token），
因此 token 集合、Jaccard、包含率与长度比都能按词精确控制。
"""

from __future__ import annotations

import pytest

from nanobot.knowledge.fusion import (
    SearchHit,
    SearchResult,
    dedup_partial,
    jaccard,
    mmr_rerank,
    single_keyword_scores,
    single_vector_scores,
    weighted_rrf,
)
from nanobot.knowledge.repository import FtsHit
from nanobot.knowledge.tokenizer import tokenize_index


def _hit(
    chunk_id: str,
    content: str,
    score: float = 1.0,
    *,
    doc_id: str = "doc",
    chunk_index: int = 0,
    content_hash: str = "",
    heading_path: str = "",
    page: int | None = None,
    locator: str = "",
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        doc_id=doc_id,
        kb_id="kb",
        chunk_index=chunk_index,
        heading_path=heading_path,
        page=page,
        locator=locator,
        content=content,
        char_count=len(content),
        content_hash=content_hash,
        score=score,
    )


def _words(start: int, count: int) -> str:
    """`t{start} .. t{start+count-1}`：每个词是一个独立 token。"""
    return " ".join(f"t{index}" for index in range(start, start + count))


def _tokens(text: str) -> set[str]:
    return set(tokenize_index(text).split())


def _containment(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    shorter = a if len(a) <= len(b) else b
    return len(a & b) / len(shorter)


def _length_ratio(left: str, right: str) -> float:
    a, b = len(_tokens(left)), len(_tokens(right))
    return max(a, b) / min(a, b)


# ---------- 分支 C：加权 RRF / maxRRF（spec §5.2、§9.1） ----------


def test_weighted_rrf_both_routes_rank1_is_one():
    assert weighted_rrf(["a"], ["a"])["a"] == 1.0


def test_weighted_rrf_vector_only_rank1_keeps_its_share():
    scores = weighted_rrf(["a", "b"], ["b"])
    assert scores["a"] == 0.7
    # b 两路命中：0.7×61/62 + 0.3
    assert scores["b"] == pytest.approx(0.7 * 61 / 62 + 0.3)


def test_weighted_rrf_keyword_only_rank1_keeps_its_share():
    assert weighted_rrf(["b"], ["a", "b"])["a"] == 0.3


def test_weighted_rrf_vector_rank1_plus_keyword_rank2():
    scores = weighted_rrf(["a", "b"], ["b", "a"])
    assert scores["a"] == pytest.approx(0.7 + 0.3 * 61 / 62)


def test_weighted_rrf_both_routes_rank2_is_61_over_62():
    scores = weighted_rrf(["x", "a"], ["y", "a"])
    assert scores["a"] == pytest.approx((0.7 + 0.3) * 61 / 62)


def test_weighted_rrf_never_renormalizes_single_route_to_one():
    # spec §5.2 明确否决「按参与路由重新归一化」：单路 rank1 仍是 0.7 / 0.3。
    assert weighted_rrf(["a"], [])["a"] == 0.7
    assert weighted_rrf([], ["a"])["a"] == 0.3


def test_weighted_rrf_deeper_ranks_stay_inside_unit_interval():
    vector_ids = [f"v{index:02d}" for index in range(30)]
    keyword_ids = [f"k{index:02d}" for index in range(30)]
    scores = weighted_rrf(vector_ids, keyword_ids)
    assert scores["v00"] == 0.7
    assert scores["k00"] == 0.3
    assert all(0.0 <= score <= 1.0 for score in scores.values())


def test_weighted_rrf_is_scale_invariant_in_weights():
    base = weighted_rrf(["a", "b"], ["b", "c"], w_v=0.7, w_k=0.3)
    scaled = weighted_rrf(["a", "b"], ["b", "c"], w_v=1.4, w_k=0.6)
    assert scaled == base


def test_weighted_rrf_weight_ratio_change_flips_single_route_items_only():
    base = weighted_rrf(["a"], ["b"], w_v=0.7, w_k=0.3)
    shifted = weighted_rrf(["a"], ["b"], w_v=0.3, w_k=0.7)
    # 只被一路命中的项：相对位置随权重比例翻转。
    assert base["a"] > base["b"]
    assert shifted["a"] < shifted["b"]
    assert base["a"] == 0.7 and base["b"] == 0.3
    assert shifted["a"] == 0.3 and shifted["b"] == 0.7
    # 双路命中项仍恒为 1.0。
    assert weighted_rrf(["a"], ["a"], w_v=0.3, w_k=0.7)["a"] == 1.0


def test_weighted_rrf_k_change_keeps_rank1_and_shrinks_rank_gap():
    # spec §9.1 的「rank2 随 k 单调下降」措辞有误：rank2 = (k+1)/(k+2) 随 k 上升，
    # 正确断言是 rank1 恒为 1.0、rank1−rank2 的差距随 k 增大而收窄（更平）。
    rank_two: list[float] = []
    for k in (10, 60, 120):
        scores = weighted_rrf(["x", "a"], ["y", "a"], k=k)
        assert scores["a"] == pytest.approx((k + 1) / (k + 2))
        rank_two.append(scores["a"])
        assert weighted_rrf(["a"], ["a"], k=k)["a"] == 1.0
    assert rank_two[0] < rank_two[1] < rank_two[2]
    gaps = [1.0 - score for score in rank_two]
    assert gaps[0] > gaps[1] > gaps[2]


def test_weighted_rrf_duplicate_id_within_route_only_counts_first_rank():
    scores = weighted_rrf(["a", "a"], ["b"])
    assert scores["a"] == 0.7
    assert scores["b"] == 0.3
    assert weighted_rrf(["a", "a"], ["a"])["a"] == 1.0
    assert weighted_rrf(["b"], ["a", "a"])["a"] == 0.3
    assert weighted_rrf(["b"], ["a", "a"])["b"] == 0.7


def test_weighted_rrf_empty_input_returns_empty():
    assert weighted_rrf([], []) == {}


def test_weighted_rrf_rounding_keeps_ceiling_at_exactly_one():
    # 0.6+0.4 在 float 下会漂到 1+1ulp；出口取 9 位小数后必须严格为 1.0。
    for w_v, w_k in ((0.6, 0.4), (0.9, 0.1), (0.123, 0.877)):
        for k in (10, 60, 777):
            assert weighted_rrf(["a"], ["a"], w_v=w_v, w_k=w_k, k=k)["a"] == 1.0


def test_weighted_rrf_never_invents_ids():
    scores = weighted_rrf(["a", "b"], ["b", "c"])
    assert set(scores) == {"a", "b", "c"}


def test_weighted_rrf_ties_break_on_chunk_id_ascending():
    scores = weighted_rrf(["b"], ["a"], w_v=0.5, w_k=0.5)
    assert scores["a"] == scores["b"] == 0.5
    assert list(scores) == ["a", "b"]


def test_weighted_rrf_is_deterministic():
    first = weighted_rrf(["a", "b", "c"], ["c", "b", "d"])
    second = weighted_rrf(["a", "b", "c"], ["c", "b", "d"])
    assert list(first.items()) == list(second.items())


# ---------- 分支 A：只有向量路，保留原始 cosine（spec §5.2、§9.1） ----------


def test_single_vector_scores_preserve_raw_cosine():
    scores = single_vector_scores([("chk_a", 0.83), ("chk_b", 0.41)])
    # 不做 RRF、不除以 max：cosine 的绝对相似度语义必须保住。
    assert scores == {"chk_a": 0.83, "chk_b": 0.41}


def test_single_vector_scores_top_is_not_scaled_to_one():
    assert single_vector_scores([("chk_a", 0.42)])["chk_a"] == 0.42


def test_single_vector_scores_dedup_keeps_max_score():
    scores = single_vector_scores([("chk_a", 0.4), ("chk_a", 0.9), ("chk_b", 0.5)])
    assert scores == {"chk_a": 0.9, "chk_b": 0.5}


def test_single_vector_scores_sort_by_score_desc_then_chunk_id():
    scores = single_vector_scores([("chk_b", 0.5), ("chk_a", 0.5), ("chk_c", 0.9)])
    assert list(scores) == ["chk_c", "chk_a", "chk_b"]


def test_single_vector_scores_empty_returns_empty():
    assert single_vector_scores([]) == {}


# ---------- 分支 B：只有关键词路，除以自身最大值（spec §5.2、§9.1） ----------


def _fts(chunk_id: str, score: float) -> FtsHit:
    return FtsHit(
        chunk_id=chunk_id,
        doc_id="doc",
        kb_id="kb",
        chunk_index=0,
        heading_path="",
        page=None,
        locator="",
        content="",
        char_count=0,
        content_hash="",
        score=score,
    )


def test_single_keyword_scores_divides_by_max():
    scores = single_keyword_scores([("chk_a", 8.0), ("chk_b", 4.0)])
    assert scores == {"chk_a": 1.0, "chk_b": 0.5}


def test_single_keyword_scores_leaves_scores_when_max_not_above_one():
    scores = single_keyword_scores([("chk_a", 0.8), ("chk_b", 0.4)])
    assert scores == {"chk_a": 0.8, "chk_b": 0.4}


def test_single_keyword_scores_accepts_fts_hits():
    scores = single_keyword_scores([_fts("chk_a", 9.0), _fts("chk_b", 3.0)])
    assert scores == {"chk_a": 1.0, "chk_b": pytest.approx(1 / 3)}


def test_single_keyword_scores_dedup_keeps_max_before_normalizing():
    scores = single_keyword_scores([("chk_a", 2.0), ("chk_a", 4.0), ("chk_b", 1.0)])
    assert scores == {"chk_a": 1.0, "chk_b": 0.25}


def test_single_keyword_scores_sort_by_score_desc_then_chunk_id():
    scores = single_keyword_scores([("chk_b", 2.0), ("chk_a", 2.0), ("chk_c", 4.0)])
    assert list(scores) == ["chk_c", "chk_a", "chk_b"]


def test_single_keyword_scores_empty_returns_empty():
    assert single_keyword_scores([]) == {}


# ---------- Jaccard ----------


def test_jaccard_is_zero_for_empty_sets():
    assert jaccard("", _words(0, 3)) == 0.0
    assert jaccard("", "") == 0.0
    assert jaccard(_words(0, 3), _words(0, 3)) == 1.0


# ---------- 三级去重：spec §5.4、§9.2 ----------


def test_dedup_exact_hash_keeps_higher_score():
    low = _hit("chk_b", _words(100, 10), 0.5, content_hash="hash-1")
    high = _hit("chk_a", _words(0, 10), 0.9, content_hash="hash-1")
    assert [hit.chunk_id for hit in dedup_partial([low, high])] == ["chk_a"]


def test_dedup_exact_hash_merges_even_when_tokens_are_disjoint():
    # 级别 ② 先于级别 ③：内容指纹相同即重复，与词面重合度无关。
    left = _hit("chk_a", _words(0, 10), 0.9, content_hash="hash-1")
    right = _hit("chk_b", _words(500, 10), 0.8, content_hash="hash-1")
    assert jaccard(left.content, right.content) == 0.0
    assert [hit.chunk_id for hit in dedup_partial([left, right])] == ["chk_a"]


def test_dedup_exact_hash_tie_breaks_on_smaller_chunk_id():
    later = _hit("chk_z", _words(0, 10), 0.5, content_hash="hash-1")
    earlier = _hit("chk_a", _words(100, 10), 0.5, content_hash="hash-1")
    assert [hit.chunk_id for hit in dedup_partial([later, earlier])] == ["chk_a"]


def test_dedup_exact_hash_ignores_empty_hash():
    left = _hit("chk_a", _words(0, 10), 0.9)
    right = _hit("chk_b", _words(100, 10), 0.5)
    assert len(dedup_partial([left, right])) == 2


def test_dedup_true_containment_subset_is_duplicate():
    # A ⊂ B：包含率 1.0；J = 10/30 ≈ 0.333；长度比 3.0 ≤ 3 → 判重。
    narrow = _hit("chk_narrow", _words(0, 10), 0.9)
    wide = _hit("chk_wide", _words(0, 30), 0.8)
    assert _containment(narrow.content, wide.content) == 1.0
    assert jaccard(narrow.content, wide.content) == pytest.approx(1 / 3)
    assert _length_ratio(narrow.content, wide.content) == 3.0
    assert [hit.chunk_id for hit in dedup_partial([wide, narrow])] == ["chk_narrow"]


def test_dedup_true_containment_beyond_length_ratio_is_retained():
    # WeKnora §6：长度比 > 3 时不判重，即使短文本是长文本子串。
    narrow = _hit("chk_narrow", _words(0, 10), 0.9)
    wide = _hit("chk_wide", _words(0, 40), 0.8)
    assert _containment(narrow.content, wide.content) == 1.0
    assert _length_ratio(narrow.content, wide.content) == 4.0
    assert [hit.chunk_id for hit in dedup_partial([wide, narrow])] == [
        "chk_narrow",
        "chk_wide",
    ]


def test_dedup_true_containment_respects_length_ratio():
    narrow = _hit("chk_narrow", _words(0, 10), 0.9)
    wide = _hit("chk_wide", _words(0, 40), 0.8)
    assert [hit.chunk_id for hit in dedup_partial([wide, narrow], length_ratio=5.0)] == [
        "chk_narrow"
    ]


def test_dedup_containment_at_threshold_is_duplicate():
    # 包含率 49/50 = 0.98，长度比 1.0 → 判重。
    narrow = _hit("chk_a", _words(0, 50), 0.9)
    wide = _hit("chk_b", _words(0, 49) + " " + _words(200, 1), 0.8)
    assert _containment(narrow.content, wide.content) == pytest.approx(0.98)
    assert _length_ratio(narrow.content, wide.content) == 1.0
    assert [hit.chunk_id for hit in dedup_partial([wide, narrow])] == ["chk_a"]


def test_dedup_high_jaccard_and_low_unique_ratio_is_duplicate():
    # OR 子句（J ≥ 0.6 且独有 ≤2%）在数学上被包含率 ≥0.98 蕴含（unique = 1−containment）；
    # 仍按 spec 字面实现，并断言该夹具同时满足两个子句。
    left = _hit("chk_a", _words(0, 50), 0.9)
    right = _hit("chk_b", _words(0, 49) + " " + _words(200, 1), 0.8)
    assert _containment(left.content, right.content) >= 0.98
    assert jaccard(left.content, right.content) == pytest.approx(49 / 51)
    assert 1.0 - _containment(left.content, right.content) <= 0.02 + 1e-12
    assert len(dedup_partial([left, right])) == 1


def test_dedup_containment_above_threshold_but_beyond_length_ratio_is_retained():
    narrow = _hit("chk_narrow", _words(0, 10), 0.9)
    wide = _hit("chk_wide", _words(0, 60), 0.8)
    assert _containment(narrow.content, wide.content) == 1.0
    assert _length_ratio(narrow.content, wide.content) == 6.0
    assert [hit.chunk_id for hit in dedup_partial([wide, narrow])] == [
        "chk_narrow",
        "chk_wide",
    ]


def test_dedup_shared_half_content_is_retained():
    # J ≈ 0.333、包含率 0.50：两块各有独有结论，不得判重。
    left = _hit("chk_a", _words(0, 10), 0.9)
    right = _hit("chk_b", _words(0, 5) + " " + _words(100, 5), 0.7)
    assert jaccard(left.content, right.content) == pytest.approx(1 / 3)
    assert _containment(left.content, right.content) == pytest.approx(0.5)
    assert [hit.chunk_id for hit in dedup_partial([right, left])] == ["chk_a", "chk_b"]


def test_dedup_small_score_gap_keeps_longer_block():
    narrow = _hit("chk_narrow", _words(0, 10), 0.9)
    wide = _hit("chk_wide", _words(0, 12), 0.88)
    # 分数差 2.2% ≤ 5% → 取更长块（信息更全）。
    assert [hit.chunk_id for hit in dedup_partial([narrow, wide])] == ["chk_wide"]


def test_dedup_never_merges_adjacent_overlap_blocks():
    # E1 边界：重叠邻块 J≈0.077、包含率≈0.167，硬去重不得删除（防回归）。
    head = _hit("chk_a", _words(0, 30), 0.9, doc_id="doc-1", chunk_index=0)
    adjacent = _hit(
        "chk_b",
        _words(0, 5) + " " + _words(30, 35),
        0.88,
        doc_id="doc-1",
        chunk_index=1,
    )
    assert 0.07 <= jaccard(head.content, adjacent.content) <= 0.08
    assert 0.16 <= _containment(head.content, adjacent.content) <= 0.17
    assert [hit.chunk_id for hit in dedup_partial([adjacent, head])] == [
        "chk_a",
        "chk_b",
    ]


def test_dedup_thresholds_are_configurable():
    narrow = _hit("chk_narrow", _words(0, 10), 0.9)
    wide = _hit("chk_wide", _words(0, 20), 0.8)
    assert len(dedup_partial([narrow, wide])) == 1
    assert len(dedup_partial([narrow, wide], length_ratio=1.5)) == 2
    # 短文本是长文本子串：即使 containment 阈值放到 >1 也判重（WeKnora §6）。
    assert len(dedup_partial([narrow, wide], containment=1.01)) == 1
    # 覆盖 0.97、独有 3%：默认阈值不判重；放宽 unique_ratio 或收紧覆盖率才改变结果。
    near = _hit("chk_near", _words(0, 100), 0.9)
    near2 = _hit("chk_near2", _words(0, 97) + " " + _words(200, 3), 0.8)
    assert _containment(near.content, near2.content) == pytest.approx(0.97)
    assert len(dedup_partial([near, near2])) == 2
    assert len(dedup_partial([near, near2], unique_ratio=0.05)) == 1
    assert len(dedup_partial([near, near2], unique_ratio=0.05, containment=0.99)) == 2


def test_dedup_is_order_independent_and_deterministic():
    first = _hit("chk_a", _words(0, 10), 0.9)
    second = _hit("chk_b", _words(0, 5) + " " + _words(100, 5), 0.7)
    third = _hit("chk_c", _words(500, 10), 0.5)
    forward = [hit.chunk_id for hit in dedup_partial([first, second, third])]
    backward = [hit.chunk_id for hit in dedup_partial([third, second, first])]
    assert forward == backward == ["chk_a", "chk_b", "chk_c"]


def test_dedup_empty_input_returns_empty():
    assert dedup_partial([]) == []


# ---------- 邻块下限、相关性带、MMR：spec §5.5、§5.6、§9.3 ----------


def _neighbor_pair() -> tuple[str, str]:
    """同文档邻块夹具：共享 2 个词，J ≈ 0.034（远低于 0.25 下限）。"""
    return _words(0, 30), _words(28, 30)


def test_mmr_adjacent_block_floor_suppresses_neighbor():
    head, adjacent = _neighbor_pair()
    a = _hit("chk_a", head, 1.0, doc_id="doc-1", chunk_index=0)
    b = _hit("chk_b", adjacent, 0.98, doc_id="doc-1", chunk_index=1)
    c = _hit("chk_c", _words(500, 30), 0.9, doc_id="doc-2", chunk_index=0)
    assert jaccard(a.content, b.content) <= 0.04
    # b 被抬到 sim=0.25：0.7×0.98 − 0.3×0.25 = 0.611 < c 的 0.63。
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=2)] == [
        "chk_a",
        "chk_c",
    ]


def test_mmr_same_doc_blocks_two_apart_are_not_floored():
    head, adjacent = _neighbor_pair()
    a = _hit("chk_a", head, 1.0, doc_id="doc-1", chunk_index=0)
    b = _hit("chk_b", adjacent, 0.98, doc_id="doc-1", chunk_index=2)
    c = _hit("chk_c", _words(500, 30), 0.9, doc_id="doc-2", chunk_index=0)
    # 相隔 2 块：sim 保持 ≈0.034 → b ≈ 0.676 > c 的 0.63。
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=2)] == [
        "chk_a",
        "chk_b",
    ]


def test_mmr_cross_doc_blocks_are_not_floored():
    head, adjacent = _neighbor_pair()
    a = _hit("chk_a", head, 1.0, doc_id="doc-1", chunk_index=0)
    b = _hit("chk_b", adjacent, 0.98, doc_id="doc-2", chunk_index=1)
    c = _hit("chk_c", _words(500, 30), 0.9, doc_id="doc-3", chunk_index=0)
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=2)] == [
        "chk_a",
        "chk_b",
    ]


def test_mmr_shared_half_content_keeps_measured_similarity():
    a = _hit("chk_a", _words(0, 20), 1.0, doc_id="doc-1", chunk_index=0)
    b = _hit("chk_b", _words(10, 20), 0.98, doc_id="doc-2", chunk_index=0)
    c = _hit("chk_c", _words(500, 30), 0.92, doc_id="doc-3", chunk_index=0)
    assert jaccard(a.content, b.content) == pytest.approx(1 / 3)
    # 相似度保持实测值（不被邻块规则改写）：b = 0.686 − 0.3×0.333 ≈ 0.586 < c 的 0.644。
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=2)] == [
        "chk_a",
        "chk_c",
    ]


def test_mmr_relevance_band_drops_far_below_top():
    a = _hit("chk_a", _words(0, 30), 1.0, doc_id="doc-1")
    b = _hit("chk_b", _words(100, 30), 0.9, doc_id="doc-2")
    c = _hit("chk_c", _words(500, 30), 0.8, doc_id="doc-3")
    # 1.0 − 0.15 = 0.85 → 0.8 的候选不进 MMR。
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=3)] == [
        "chk_a",
        "chk_b",
    ]


def test_mmr_relevance_band_boundary_is_inclusive():
    a = _hit("chk_a", _words(0, 30), 1.0, doc_id="doc-1")
    b = _hit("chk_b", _words(100, 30), 0.85, doc_id="doc-2")
    assert [hit.chunk_id for hit in mmr_rerank([a, b], limit=2)] == ["chk_a", "chk_b"]


def test_mmr_suppresses_near_duplicate_second_hit():
    a = _hit("chk_a", "alpha beta gamma delta", 1.0, doc_id="doc-1")
    b = _hit("chk_b", "alpha beta gamma delta", 0.95, doc_id="doc-2")
    c = _hit("chk_c", "omega sigma tau upsilon", 0.9, doc_id="doc-3")
    # b 与 a 词面全等：0.7×0.95 − 0.3×1.0 = 0.365 < c 的 0.63。
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=2)] == [
        "chk_a",
        "chk_c",
    ]


def test_mmr_higher_lambda_tracks_fused_order():
    a = _hit("chk_a", "alpha beta gamma", 1.0, doc_id="doc-1")
    b = _hit("chk_b", "alpha beta gamma", 0.95, doc_id="doc-2")
    c = _hit("chk_c", "omega sigma tau", 0.9, doc_id="doc-3")
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=2)] == [
        "chk_a",
        "chk_c",
    ]
    # λ 调高 → 多样性惩罚量级变小，排序更贴 fused。
    assert [hit.chunk_id for hit in mmr_rerank([a, b, c], limit=2, lambda_=0.99)] == [
        "chk_a",
        "chk_b",
    ]


def test_mmr_display_order_is_fused_descending():
    hits = [
        _hit(f"chk_{index}", _words(index * 100, 10), 1.0 - index * 0.01)
        for index in range(5)
    ]
    scores = [hit.score for hit in mmr_rerank(hits, limit=4)]
    assert scores == sorted(scores, reverse=True)


def test_mmr_tie_breaks_by_chunk_id_and_is_order_independent():
    x = _hit("chk_x", "first block", 0.5, doc_id="doc-1")
    y = _hit("chk_y", "second block", 0.5, doc_id="doc-2")
    forward = [hit.chunk_id for hit in mmr_rerank([x, y], limit=2)]
    backward = [hit.chunk_id for hit in mmr_rerank([y, x], limit=2)]
    assert forward == ["chk_x", "chk_y"]
    assert backward == forward


def test_mmr_empty_input_or_zero_limit_returns_empty():
    assert mmr_rerank([], limit=3) == []
    assert mmr_rerank([_hit("chk_a", "content here", 1.0)], limit=0) == []


def test_mmr_respects_limit():
    hits = [_hit(f"chk_{index}", _words(index * 100, 10), 1.0) for index in range(5)]
    assert len(mmr_rerank(hits, limit=3)) == 3


# ---------- DTO 与字段保留 ----------


def test_search_result_defaults():
    result = SearchResult(hits=(_hit("chk_a", "content", 0.5),), mode_used="hybrid")
    assert result.degraded == ()
    assert result.reason is None


def test_search_hit_content_hash_survives_dedup_and_mmr():
    hit = _hit("chk_a", _words(0, 5), 0.5, content_hash="hash-1")
    ranked = mmr_rerank(dedup_partial([hit]), limit=1)
    assert ranked[0].content_hash == "hash-1"
    assert _hit("chk_b", "content").content_hash == ""


def test_fusion_preserves_source_fields_for_citations():
    hit = _hit(
        "chk_a",
        _words(0, 5),
        0.5,
        heading_path="第一章 > 部署",
        page=7,
        locator="p7",
    )
    ranked = mmr_rerank(dedup_partial([hit]), limit=1)
    assert ranked[0].chunk_id == "chk_a"
    assert ranked[0].heading_path == "第一章 > 部署"
    assert ranked[0].page == 7
    assert ranked[0].locator == "p7"
    assert ranked[0].content == _words(0, 5)
