"""检索融合口径探针：Jaccard 分布、RRF 归一化、MMR 量纲。

复跑：`.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-retrieval-fusion/probe_fusion.py`
"""

from __future__ import annotations

import random

from nanobot.knowledge.tokenizer import tokenize_index

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
K = 60
W_VEC = 0.7
W_KEY = 0.3


def shingles(text: str) -> set[str]:
    return set(tokenize_index(text).split())


def jaccard(left: str, right: str) -> float:
    a, b = shingles(left), shingles(right)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def containment(left: str, right: str) -> float:
    a, b = shingles(left), shingles(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def synthetic_cjk(length: int, seed: int) -> str:
    """高熵中文串：模拟真实文档的 token 多样性（近似真实 prose 的 token 密度）。"""
    random.seed(seed)
    return "".join(chr(random.randint(0x4E00, 0x9FA5)) for _ in range(length))


def fused_score(routes: dict[str, int], active: tuple[str, ...]) -> float:
    """routes: 命中该块的路由及其名次；active: 本次参与融合的路由集合。"""
    base = {"v": W_VEC, "k": W_KEY}
    total = sum(base[r] for r in active)
    weights = {r: base[r] / total for r in active}
    score = sum(weights[r] / (K + routes[r]) for r in routes if r in active)
    ceiling = sum(weights[r] / (K + 1) for r in active)
    return score / ceiling


def probe_jaccard() -> None:
    core = synthetic_cjk(CHUNK_SIZE, 1)
    neighbor = core[CHUNK_SIZE - CHUNK_OVERLAP :] + synthetic_cjk(CHUNK_SIZE - CHUNK_OVERLAP, 2)
    unrelated = synthetic_cjk(CHUNK_SIZE, 3)
    half_shared = core[: CHUNK_SIZE // 2] + synthetic_cjk(CHUNK_SIZE // 2, 4)
    contained = core[: CHUNK_SIZE // 4]  # 摘要：正文的前 1/4，独有 token 为 0

    print("[E1] token 集合与相似度分布")
    for name, text in (("base", core), ("neighbor", neighbor), ("unrelated", unrelated)):
        print(f"  tokens({name}) = {len(shingles(text))}")
    print(f"  jaccard(neighbor, base)      = {jaccard(neighbor, core):.4f}")
    print(f"  containment(neighbor, base)  = {containment(neighbor, core):.4f}")
    print(f"  jaccard(half_shared, base)   = {jaccard(half_shared, core):.4f}")
    print(f"  containment(half_shared, base)= {containment(half_shared, core):.4f}")
    print(f"  jaccard(unrelated, base)     = {jaccard(unrelated, core):.4f}")
    print(f"  containment(unrelated, base) = {containment(unrelated, core):.4f}")
    print(f"  jaccard(contained, base)     = {jaccard(contained, core):.4f}")
    print(f"  containment(contained, base) = {containment(contained, core):.4f}")
    print("  → 包含率口径对分词粒度不敏感：只有真包含才接近 1.0")


def probe_rrf() -> None:
    print("[E3] 有效权重归一化下的 fused")
    both = ("v", "k")
    print(f"  两路 rank1                 = {fused_score({'v': 1, 'k': 1}, both):.4f}")
    print(f"  向量 rank1 + 关键词 rank2  = {fused_score({'v': 1, 'k': 2}, both):.4f}")
    print(f"  两路 rank2                 = {fused_score({'v': 2, 'k': 2}, both):.4f}")
    print(f"  仅向量 rank1（两路参与）   = {fused_score({'v': 1}, both):.4f}")
    print(f"  仅关键词 rank1（两路参与） = {fused_score({'k': 1}, both):.4f}")
    print(f"  仅向量 rank20（两路参与）  = {fused_score({'v': 20}, both):.4f}")
    print(f"  仅关键词 rank20（两路参与）= {fused_score({'k': 20}, both):.4f}")
    print(f"  单路关键词 rank1           = {fused_score({'k': 1}, ('k',)):.4f}")
    print(f"  单路关键词 rank10          = {fused_score({'k': 10}, ('k',)):.4f}")
    print(f"  单路关键词 rank50          = {fused_score({'k': 50}, ('k',)):.4f}")
    print("  → 固定分母下单路 top1 只有 0.7/0.3；有效权重归一化后为 1.0")


def probe_mmr() -> None:
    print("[E2] MMR 量纲")
    lam = 0.7
    for floor in (0.25, 0.35):
        penalty = (1 - lam) * floor
        print(f"  邻块相似度下限 {floor}: 惩罚项最大 = {penalty:.4f}")
    print(f"  rel 0.98 且 sim 0.90 → {0.7 * 0.98 - 0.3 * 0.9:.4f}")
    print(f"  rel 0.95 且 sim 0.00 → {0.7 * 0.95 - 0.3 * 0.0:.4f}")
    print("  → 多样性惩罚需配相关性带；邻块下限取 0.25（介于 0.091 与 0.336 之间）")


if __name__ == "__main__":
    probe_jaccard()
    probe_rrf()
    probe_mmr()
