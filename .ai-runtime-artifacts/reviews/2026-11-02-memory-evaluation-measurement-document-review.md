---
artifact: document-review
route: document-review -> writing-plans + api-and-interface-design
skills: [document-review, writing-plans, api-and-interface-design, verification-before-completion]
skills_evidence:
  - .claude/skills/document-review/SKILL.md
  - .claude/skills/writing-plans/SKILL.md
  - C:/Users/Huangqh/.codex/plugins/cache/agent-skills/agent-skills/0.6.15/skills/api-and-interface-design/SKILL.md
  - .claude/skills/verification-before-completion/SKILL.md
source:
  - AGENTS.md
  - harness-kit/core/routing.md
  - .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-plan.md
  - .ai-runtime-artifacts/plans/2026-11-02-memory-evaluation-platform-dispatch.md
  - .ai-runtime-artifacts/contracts/2026-11-02-contract-memory-evaluation.md
created_at: 2026-11-02
status: revised
approved: false
---

# 记忆评测计划审查：输入、输出、算法与升降判定

## 1. 文档类型与审查规则

对象为实施计划及配套技术契约，按document-review的design优先规则加载design.md，同时加载plan.md和review-checklist.md。只审查和修订文档，不修改业务代码、不执行计划、不委派实现或审查Agent。

## 2. 审查结论

上一版有执行流程和指标列表，但不足以让不同实施者计算出同一分数。本轮已把以下缺口写入contract§8，并同步到plan的WU-01/05/08/11及dispatch：

| 优先级 | 原缺口 | 后果 | 本次修订 |
| --- | --- | --- | --- |
| P1 | 事实“匹配”只有概念，未指定算法 | 同义、否定、时态、重复导致TP不稳定 | 版本化规范化+等价判定+最大二分匹配 |
| P1 | gold未区分完整抽取集合与单题答案 | 额外记住的合法事实可能被错判FP | annotation_scope；targeted不输出全量抽取Precision/F1 |
| P1 | 主指标macro的层级未定 | 多题场景、重复次数会改变权重 | probe→trial→scene固定等权，strict pass另列 |
| P1 | 统计显著与实际采用混用 | 极小提升或安全退化被当整体改善 | statistical_direction/adoption_decision分开 |
| P1 | 未明确回答正确是否代表记忆有效 | 模型猜中或常识答对也可能被归因记忆 | 另记grounded_memory_success与无记忆配对增益 |
| P2 | 环境准备未单列首个门禁 | 编码时才发现依赖/配置未就绪 | M0环境、隔离、已有测试、真实profile可用性分离 |

完整性：由“基本完整”改为“测量协议完整”；逻辑清晰度：原“基本清晰”，本次明确可评分条件、分母、聚合与决策；环境准备：计划已明确，实际就绪检查仍在实现前执行。计划尚未批准，不因此自动进入实现。

## 3. 输入什么

| 输入 | 提供者与例子 | 交给谁 |
| --- | --- | --- |
| 测试场景 | 内置题库/公开集loader/用户导入；历史会话、时间、scope、问题、更新/删除/重启事件 | 被测系统执行端 |
| 标准结果gold | 已确认的原子事实、答案条件、来源、期望缺失、允许证据组；标注是否完整 | 仅评分端 |
| 候选版本 | 当前/新参数、新算法代码摘要、生成与抽取模型、prompt、检索/索引设置 | 隔离runtime与manifest |
| 实验协议 | 三轨选择、重复次数、预算、变化项、主要指标、校准和采用policy | 调度与比较模块 |

用户无需从空白写题库：首版提供20条开发冒烟、120条保留回归及独立校准集合。公开集按锁定的已发布标注与原评分协议；产品集标注者确认gold；自动生成的gold不能直接当已确认。

一个完整场景：11月1日告知喜欢加糖拿铁，11月2日改为无糖美式；存两个真实会话边界并等待记忆处理；新会话问“现在点什么”“昨天喜欢什么”“没告诉过你的生日是哪天”。另有checkpoint分支测试忘记偏好/重建/跨scope。gold分别要求使用当前值、历史值、正确拒答；它不出现在query或可读文件里。

长期测试用显式时钟模拟，不一定真等一天；重启测试需要重新加载磁盘状态。模拟一天也不能直接证明真实长期运行的所有故障都覆盖。

## 4. 输出什么

输出有三层，不只一个分数：

1. 逐题证据：历史与状态、抽出了哪些事实、何时更新、召回哪些结果、哪些进入真实上下文、回答是什么、每条判据为何通过/失败/未知。
2. 实验报告：各指标的值/分子/分母/覆盖率，分类表现、重复稳定性、耗时/成本，技术失败及未评项目。
3. 版本比较：冻结条件、逐题改善和退化、主指标差值与区间、关键违规、统计方向与采用建议。历史报告可复算，但重判产生新评分版本，不覆盖原快照。

例：某题应需要3条证据，top5找到了2条，实际只注入1条，最终关键事实答错，则分别为Recall@5=2/3、注入覆盖=1/3、qa_correct=0。报告首先定位为召回不全及注入损失，再看回答；不能统一归因为“模型不聪明”。

## 5. 根据什么算法计算

### 5.1 抽取记忆：一对一匹配与P/R/F1

对已确认完整的目标集合，先规范化主体/属性/值/时态/scope；等价关系用确定规则或校准后的语义评分器。对等价边做最大基数二分图匹配，避免一个事实重复贡献分数。比如P1可对应G1/G2，P2只能对应G1，应匹配P1-G2、P2-G1，不能先贪心用P1占G1而少算一个TP。

TP=正确匹配事实数；FP=不符合完整抽取目标的输出数；FN=应记未记数。

- Precision=TP/(TP+FP)，表示记下来的内容中多少符合目标。
- Recall=TP/(TP+FN)，表示应该记下来的内容中记住多少。
- F1=2TP/(2TP+FP+FN)，兼顾两者。

示例：应记10条，系统输出12条，正确匹配8条，则TP=8、FP=4、FN=2；P=66.67%、R=80%、F1=72.73%。假设前提是抽取目标标注完整；只提供某题答案时不能直接这么算。

P/R/F公式与[scikit-learn官方定义](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.precision_recall_fscore_support.html)一致；最大匹配的定义可参见[NetworkX官方文档](https://networkx.org/documentation/stable/reference/algorithms/generated/networkx.algorithms.bipartite.matching.hopcroft_karp_matching.html)。规范化与判分policy是本项目设计，不声称上述库自动解决记忆语义问题，也不因此引入新依赖。

### 5.2 检索与注入：证据覆盖及排序

设本题必要证据为G，top-k实际覆盖为C：Recall@k=|C∩G|/|G|。MRR为第一条相关结果排名倒数的多题均值，例如第2位命中为1/2。多跳另检查完整证据集，命中一条不等于足够回答。

实际返回precision分母是返回条数；必须知道各返回项是否相关才计分，否则不能把未标项直接当无关。注入覆盖用真实送给模型的上下文计算，因此召回率高但被预算裁掉，仍可显示注入覆盖低。

### 5.3 最终回答：规则优先、语义判定校准

最终二值正确要求必需事实均正确、时间与scope匹配、无关键矛盾。部分正确给事实覆盖诊断，但qa_correct为0。没有答案时承认信息不足是正确；有答案却一直拒答是错误。缺材料/未校准的自由文本判断为未知，不能硬给0或1。

“推荐无糖美式，同时也可以继续按旧习惯加糖”不能因为命中“无糖美式”关键词就通过。0–4语义量表用于分析错误程度，不能直接冒充二值准确率。

grounded_memory_success要求正确答案有必要的合法记忆证据可用；它仍不揭示模型内部因果。进一步看同一生成模型、同一问题在“有持久记忆”和“无持久记忆”的配对差值Memory Lift，才有记忆净收益的实验参考。

### 5.4 所有指标及其方向

| 指标组 | 计算结果 | 趋势 |
| --- | --- | --- |
| 抽取P/R/F1 | 上述匹配计数 | 高为好 |
| 无依据记忆率 | 无源会话支持的新增事实/全部可判新增事实 | 低为好 |
| Evidence Recall@k、MRR | 证据覆盖、首个相关排名 | 高为好 |
| 注入覆盖 | 实际上下文覆盖的必要证据/所需证据 | 高为好 |
| QA与证据支持成功 | 正确probe比例；正确且证据充分的probe比例 | 高为好 |
| 偏好/更新/时序/跨会话/重启 | 相应适用子集中的正确比例 | 高为好 |
| 过期事实误用 | 将失效值断言为当前值的probe/可判当前状态probe | 低为好 |
| 正确拒答/误拒答 | 无答案题中正确拒答；有答案题中错误拒答 | 前者高为好，后者低为好 |
| 遗忘行为/状态成功 | 全部删除后探测无暴露；再加活动存储与重建检查通过 | 高为好 |
| scope泄漏 | 候选/注入/回答任一阶段越界的场景/可观察隔离场景 | 低为好，确认违规为关键失败 |
| 重复稳定性 | K次均完全通过的场景/完整可判K次的场景 | 高为好 |
| Memory Lift | 有记忆-无记忆的配对QA差值 | 正值为收益参考 |
| 延迟/Token/嵌入/费用/字节 | 分阶段统计p50/p95、合计及有效样本数 | 需在质量条件下比较，低不自动代表能力好 |
| 运行与评分覆盖 | 已执行/计划、可评分/计划及失败分布 | 必须与能力分一起看 |

Agent通用层保持任务完成、约束、工具决策/参数、回答质量、稳定性、效率、安全与覆盖。工具决策准确率需要标注正确决策点；没有标注只能报告执行成功率，不能把二者混称。记忆专项结果不自动证明整个Agent在所有任务上更强。

## 6. 多题、多次运行如何合成主要指标

采用场景macro，避免长场景的题更多就占据更大权重。每个场景先平均其probe正确值，再平均K次重复，最后对N个场景等权平均。严格“所有题和硬约束都通过”的场景通过率另列。

单次示例：A场景2题全对，场景分1；B场景4题对1题，场景分0.25。场景macro=(1+0.25)/2=62.5%；题级micro=3/6=50%。两者都展示，主指标固定前者，不能挑高的报告。

120个场景×3次代表360次trial；若两个版本则720次，若加无记忆第三组则1080次。重复用于看波动，不使独立场景数从120变成360。若共享同一原对话/高度重复的事实模板，区间分析还需按共同cluster_id合并。

只有所有计划题/重复的所需分数可判，主要分才完整。若服务失败导致漏题，可以显示已评分子集的描述值和成功/planned交付比例，但不据子集宣布总体提升。按策略不记某内容且正常回答错了属于能力失败，不当作技术异常剔除。

## 7. 怎么判断提升、降低、持平或证据不足

比较条件先固定：数据/gold/轨道/评分版本/非被测模型和预算环境；只让声明的记忆算法参数变化。评分器更新时双方用同版材料重判；没有材料则不可比。历史模型服务换了版本，需重新解释变化或重跑基线。

主要差值Δ=100×(新版本场景macro-旧版本场景macro)，单位百分点。先对相同场景配对，再对cluster整体重采样，2000轮、seed42、type7的2.5%/97.5%分位数作为95%区间。C<20只描述差值；门槛本身不保证统计充分。

[SciPy官方文档](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html)说明配对重采样和区间方法；cluster分组和本项目默认参数是设计选择。percentile是首版明确版本的工程诊断方法，不宣称在小样本/稀疏数据下有精确95%覆盖保证；以后切换basic/BCa等方法必须变更版本并统一复算。

| 观察 | 结论 |
| --- | --- |
| 差值区间全部>0 | 统计方向支持提升 |
| 差值区间全部<0 | 统计方向支持降低 |
| 区间跨0/无区间 | 尚不能确认，不能叫确定持平 |
| 区间下界还超过预定minimum_effect_pp，且门禁/分项/成本全部通过 | 可建议采用 |
| 正确率提高但泄漏/删除复活已确认 | 阻止采用，均分不能抵消 |
| 主要指标缺题、未校准或比较条件不一致 | 不完整/待复核/不可比 |
| 质量在预设非劣范围内、成本显著降低 | 可表述质量未见超容忍度退化且效率改善，不叫记忆能力确定提升 |

minimum_effect_pp和分项容忍度由项目在实验前选择，例如“至少有2个百分点的有意义收益”；2不是行业标准，也不是当前自动默认值。统计方向与采用建议分别展示，没有门槛配置只给诊断报告。

以下代码仅复算合成数据与区间示例，不是平台实现或真实模型实验：

```python
import math
import random

def percentile_type7(values: list[float], p: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * p
    left = math.floor(position)
    right = math.ceil(position)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)

def paired_cluster_interval(rows: list[tuple[str, float, float]]) -> dict:
    if not rows:
        return {"delta_pp": None, "clusters": 0, "interval_pp": None}
    clusters: dict[str, list[float]] = {}
    for cluster, baseline, candidate in rows:
        clusters.setdefault(cluster, []).append(candidate - baseline)
    point = 100 * sum(candidate - baseline for _, baseline, candidate in rows) / len(rows)
    keys = sorted(clusters)
    if len(keys) < 20:
        return {"delta_pp": point, "clusters": len(keys), "interval_pp": None}
    rng = random.Random(42)
    statistics = []
    for _ in range(2000):
        sampled = [value for _ in keys for value in clusters[rng.choice(keys)]]
        statistics.append(100 * sum(sampled) / len(sampled))
    bounds = [percentile_type7(statistics, 0.025), percentile_type7(statistics, 0.975)]
    return {"delta_pp": point, "clusters": len(keys), "interval_pp": bounds}

def synthetic_pairs() -> list[tuple[str, float, float]]:
    pairs = [(1.0, 1.0)] * 86 + [(0.0, 1.0)] * 16 + [(1.0, 0.0)] * 4 + [(0.0, 0.0)] * 14
    return [(f"cluster-{index:03}", old, new) for index, (old, new) in enumerate(pairs)]
```

合成例有120个独立cluster：旧版90/120=75%，新版102/120=85%，16题从错到对、4题从对到错，其余不变，差值+10个百分点。本轮执行上述代码得到95%示例区间[+3.3125,+16.6667]个百分点。即使方向支持提升，依然要看实际收益门槛、分项与成本，不能仅靠总分；这组数字不是知序模型成绩。

## 8. 为什么有些变化只改善中间指标

- 抽取Recall上升、最终QA不变：记得更多，但可能未检索/注入/使用，不能说用户体验已提升。
- 召回上升、无依据回答或scope泄漏增加：更多上下文引入干扰/越界，需要报告退化，不能只宣传召回收益。
- QA上升、grounded证据没有增加：可能是reader或猜测能力影响，需看无记忆基线与配置变化。
- QA基本相同、成本更低：属于效率改善；若证明非劣条件满足，可建议采用，但结论不同于能力提升。
- 更新题变好但历史时序题变差：更新算法可能覆盖掉历史，需要分项展示。

## 9. 验证范围与后续

文档修订后检查YAML/引用、Python示例语法，并执行手算公式、场景macro/micro、最大匹配反例及合成bootstrap。没有运行真实模型、完整基准、软件回归或浏览器测试，不声称知序已经提升/降低。

本轮验证输出：四份文档UTF-8无BOM、YAML与本地引用/skill路径/阶段状态/空白检查通过；11个Python代码块通过ast.parse；P/R/F1手算、62.5%场景macro与50%题级micro、二分图最大匹配2条、配对bootstrap的少于20cluster/零差值/负差值边界均通过。12个WU、60个Step保持，git diff --check无错误，git diff --name-only无已跟踪源码变化。以上只验证文档及数学示例，TDD gate为N/A（无生产代码）。

plan的12个WU仍是工程工作包，不能把60个Step理解为60×几分钟的总工期；执行时按其测试矩阵逐条完成RED/GREEN并记账。本轮修订不授权实现。

## Next

- 用户可继续调整测试口径或数据范围。
- 认可修订后的plan/contract后说“开始实现”，才进入业务实现；审查通过不替用户批准计划。
