---
artifact: verification
route: superpowers:verification-before-completion
scope: knowledge-retrieval-fusion
created_at: 2026-11-09
status: pass
spec: ../specs/2026-11-09-knowledge-retrieval-spec.md
---

# 检索融合口径探针记录（2026-11-09）

## 1. 目的

为检索 spec 的融合与排序口径提供可复跑证据，支撑四项结论：

- E1：字面相似度必须看**包含率**，不能只看 Jaccard → 去重判据用包含率口径。
- E2：MMR 的多样性惩罚量纲 → 相关性带的必要性。
- E3：RRF 单路天花板（0.7 / 0.3）是 **WeKnora 的刻意设计**，不改代数，改走三条互斥归一化路径。
- E4：与 WeKnora 原理 9 篇的逐条对照（见检索 spec §1.1）。

## 2. 复跑方式

```bash
.venv/bin/python .ai-runtime-artifacts/verifications/2026-11-09-knowledge-retrieval-fusion/probe_fusion.py
```

环境：`.venv`（Python 3.13.9）；使用仓库已实现的 `nanobot/knowledge/tokenizer.py`（`unigram+bigram`）。
文本为高熵中文串（`chr(0x4E00–0x9FA5)` 随机串），用于模拟真实 prose 的 token 多样性。

## 3. 输出

```
[E1] token 集合与相似度分布
  tokens(base) = 1578
  tokens(neighbor) = 1584
  tokens(unrelated) = 1586
  jaccard(neighbor, base)      = 0.0907
  containment(neighbor, base)  = 0.1667
  jaccard(half_shared, base)   = 0.3360
  containment(half_shared, base)= 0.5044
  jaccard(unrelated, base)     = 0.0089
  containment(unrelated, base) = 0.0177
  jaccard(contained, base)     = 0.2522
  containment(contained, base) = 1.0000
  → 包含率口径对分词粒度不敏感：只有真包含才接近 1.0
[E3] 有效权重归一化下的 fused
  两路 rank1                 = 1.0000
  向量 rank1 + 关键词 rank2  = 0.9952
  两路 rank2                 = 0.9839
  仅向量 rank1（两路参与）   = 0.7000
  仅关键词 rank1（两路参与） = 0.3000
  仅向量 rank20（两路参与）  = 0.5338
  仅关键词 rank20（两路参与）= 0.2288
  单路关键词 rank1           = 1.0000
  单路关键词 rank10          = 0.8714
  单路关键词 rank50          = 0.5545
  → 固定分母下单路 top1 只有 0.7/0.3；有效权重归一化后为 1.0
[E2] MMR 量纲
  邻块相似度下限 0.25: 惩罚项最大 = 0.0750
  邻块相似度下限 0.35: 惩罚项最大 = 0.1050
  rel 0.98 且 sim 0.90 → 0.4160
  rel 0.95 且 sim 0.00 → 0.6650
  → 多样性惩罚需配相关性带；邻块下限取 0.25（介于 0.091 与 0.336 之间）
```

> 注：E3 段落中的「有效权重归一化」是探针的对照算例（用于展示固定分母与重归一化的差别）；
> **最终采纳的口径是 WeKnora `06 §2.2` 的三条互斥路径**——单路不用 RRF，用该路原生分（检索 spec §5.2）。
> 探针保留该对照算例，便于复核「固定分母 vs 重归一化」的数值差异。

## 4. 结论

| 编号 | 结论 | 影响 |
| --- | --- | --- |
| E1 | 邻块 Jaccard 0.091 / 包含率 0.167；共享一半 0.336 / 0.504；无关 0.009 / 0.018；**真包含 0.252 / 1.000** | 真包含的 Jaccard 仅 0.252 → 纯 Jaccard 阈值（0.6 或 0.85）都抓不到；**去重必须用包含率口径**（WeKnora「独有 token ≤2%」的等价表达）。overlap 邻块不属去重范畴，由邻块规则处理 |
| E2 | 0.03 的相关性差距会被 0.90 的相似度惩罚完全吞掉（0.416 < 0.665） | MMR 必须配相关性带 `fused ≥ fused_max − 0.15`；邻块下限取 0.25（惩罚上限 0.075） |
| E3 | 固定分母下单路 top1 只有 0.700 / 0.300 | **这是 WeKnora `03 §5.5` 的刻意设计**（关键词路只补不主导），不改为「按参与路由重归一化」；改为单路走原生分 |
| E4 | 与 WeKnora 9 篇原理存在 3 处差距（归一化、去重判据、漏读 06/07/08） | 已按检索 spec §1.1 逐条登记并修正 |

## 5. 未覆盖（后续）

- 真实语料（非合成高熵串）下的分布未测；合成串词汇重复度更低，实际包含率可能与探针略有出入。
- `dedup_containment=0.98` / `dedup_unique_ratio=0.02` 在真实语料上的标定留给 golden queries（检索 spec §10、§12 待确认 4）。
- MMR 在真实检索结果集上的收益未测（需 golden queries 集）。
- Chroma 侧 top-N 的实际距离分布未测（依赖具体 embedding 模型）。
