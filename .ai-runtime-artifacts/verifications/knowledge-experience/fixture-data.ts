import type { KnowledgeKb, KnowledgeDoc, KnowledgeChunk, KnowledgeGraphEntity } from "../../../webui/src/lib/api";

export const FIXTURE_TOKEN = "fixture-only-no-credentials";
export const FIXTURE_CSRF = "fixture-only-csrf";
const timestamp = "2026-11-11T02:00:00Z";
export const metricIds = ["hit_at_k", "precision_at_k", "mrr", "ndcg_at_k", "recall_at_k", "pool_coverage", "unscored_rate"] as const;
export const nullMetrics = Object.fromEntries(metricIds.map((id) => [id, null]));
export const metrics = { hit_at_k: 1, precision_at_k: 0.5, mrr: 0.75, ndcg_at_k: 0.8, recall_at_k: 0.5, pool_coverage: 0.4, unscored_rate: 0 };
export const candidateMetrics = { ...metrics, precision_at_k: 0.25, recall_at_k: null, pool_coverage: null };
export const metricSpecs = metricIds.map((id, index) => ({
  metric_id: id,
  label: ["命中率", "准确率", "首个命中倒数", "归一化折损累计增益", "召回率", "候选池相关占比", "未评率"][index],
  formula: ["命中查询数 ÷ 可评查询数", "相关块数(top-k) ÷ k", "平均(1 ÷ 首个相关块名次)", "DCG ÷ IDCG", "相关块数(top-k) ÷ 相关块数(pool)", "相关块数(pool) ÷ 池候选总数", "not_assessable 查询数 ÷ 总查询数"][index],
  numerator: "测试口径分子", denominator: "测试口径分母",
  direction: id === "unscored_rate" ? "lower_is_better" : "higher_is_better",
  description: "本字段由夹具模拟后端下发，仅供界面验收，不代表真实评测。",
}));

export function createFixtureData() {
  const kbs: KnowledgeKb[] = [
    { id: "kb-product", name: "产品与部署手册", description: "测试数据：部署、回滚与权限说明", workspaceId: "fixture-workspace", visibility: "workspace", isEnabled: true, documentCount: 3, createdAt: timestamp, updatedAt: timestamp },
    { id: "kb-team", name: "团队协作与项目复盘——用于检查长标题的知识库", description: "测试数据：全局共享资料", workspaceId: "fixture-workspace", visibility: "global", isEnabled: true, documentCount: 1, createdAt: timestamp, updatedAt: timestamp },
    { id: "kb-empty", name: "待整理资料", description: "测试数据：停用且暂无文档", workspaceId: "fixture-workspace", visibility: "workspace", isEnabled: false, documentCount: 0, createdAt: timestamp, updatedAt: timestamp },
  ];
  const docs: KnowledgeDoc[] = [
    { id: "doc-ready", kbId: "kb-product", title: "部署与回滚操作指南", originalName: "deployment.md", status: "ready", chunkCount: 2, charCount: 80, mime: "text/markdown", vectorStatus: "ready", createdAt: timestamp, updatedAt: timestamp },
    { id: "doc-failed", kbId: "kb-product", title: "无法解析的测试附件", originalName: "broken.pdf", status: "failed", chunkCount: 0, charCount: 0, mime: "application/pdf", errorCode: "PARSE_FAILED", error: "测试数据：无法解析文件，请检查格式后重试。", createdAt: timestamp, updatedAt: timestamp },
    { id: "doc-processing", kbId: "kb-product", title: "正在处理的测试文档", originalName: "processing.txt", status: "processing", chunkCount: 0, charCount: 0, mime: "text/plain", createdAt: timestamp, updatedAt: timestamp },
    { id: "doc-team", kbId: "kb-team", title: "团队协作约定", originalName: "team.txt", status: "ready", chunkCount: 1, charCount: 25, mime: "text/plain", createdAt: timestamp, updatedAt: timestamp },
  ];
  const chunks: KnowledgeChunk[] = [
    { id: "chunk-deploy", docId: "doc-ready", kbId: "kb-product", index: 0, heading: "部署", page: null, locator: "第 1 节", content: "测试数据：部署前检查配置，部署完成后确认健康状态。", charCount: 28 },
    { id: "chunk-rollback", docId: "doc-ready", kbId: "kb-product", index: 1, heading: "回滚", page: 2, locator: "第 2 页", content: "测试数据：回滚部署时先选择上一稳定版本，再验证服务。", charCount: 29 },
    { id: "chunk-team", docId: "doc-team", kbId: "kb-team", index: 0, heading: "协作", page: null, locator: "第 1 节", content: "测试数据：团队协作需要记录决策并注明负责人。", charCount: 25 },
  ];
  const entities: KnowledgeGraphEntity[] = [
    { id: "entity-deploy", name: "部署", type: "process", description: "测试数据：发布服务的流程", mentionCount: 2 },
    { id: "entity-rollback", name: "回滚", type: "process", description: "测试数据：恢复稳定版本", mentionCount: 1 },
    { id: "entity-version", name: "稳定版本", type: "concept", description: "测试数据：已验证版本", mentionCount: 1 },
  ];
  const edges = [
    { source: "entity-deploy", target: "entity-rollback", relation: "失败时恢复", weight: 1 },
    { source: "entity-rollback", target: "entity-version", relation: "恢复到", weight: 1 },
  ];
  const cases = [
    { id: "case-expect", query: "如何回滚部署？", expect_any: ["回滚", "稳定版本"], kb_ids: ["kb-product"], mode: "hybrid", limit: 5, label_source: "expect", ordinal: 0 },
    { id: "case-judge", query: "部署前需要检查什么？", expect_any: [], kb_ids: ["kb-product"], mode: "semantic", limit: 5, label_source: "judge", ordinal: 1 },
    { id: "case-null", query: "不存在的证据如何评分？", expect_any: [], kb_ids: ["kb-product"], mode: "keyword", limit: 5, label_source: "judge", ordinal: 2 },
    { id: "case-failed", query: "失败查询如何显示？", expect_any: ["测试"], kb_ids: ["kb-product"], mode: "hybrid", limit: 5, label_source: "expect", ordinal: 3 },
  ];
  const datasets = [
    { id: "dataset-main", name: "部署检索验收题集", notes: "测试数据：expect / judge / 未评分 / 失败", revision: "1", cases, created_at: timestamp, updated_at: timestamp },
    { id: "dataset-empty", name: "空题集", notes: "测试数据：空状态", revision: "1", cases: [] as typeof cases, created_at: timestamp, updated_at: timestamp },
  ];
  const groups = (values: Record<string, number | null> = metrics) => ({ groups: [
    { label_source: "expect", total_queries: 2, scored_queries: 1, metrics: values },
    { label_source: "judge", total_queries: 2, scored_queries: 1, metrics: { ...values, recall_at_k: null, pool_coverage: null, unscored_rate: 0.5 } },
  ] });
  const runs = ["run-base", "run-candidate"].map((id, index) => ({
    id, dataset_id: "dataset-main", status: "completed", configs: [{ id: "base", name: "默认配置", overrides: { mode: "hybrid", limit: 5 } }], judge: { profile_id: "fixture-judge", mock: true }, budgets: { max_queries: 4 },
    metrics: groups(index ? candidateMetrics : metrics), progress: { completed: 4, total: 4 }, pool: { degraded: [] as string[] }, error: null as string | null,
    created_at: index ? "2026-11-11T03:00:00Z" : timestamp, finished_at: "2026-11-11T03:01:00Z",
  }));
  function report(id: string) {
    const run = runs.find((item) => item.id === id);
    const values = id === "run-base" ? metrics : candidateMetrics;
    return { run_id: id, status: run?.status ?? "completed", metrics: run?.metrics ?? groups(), results: [
      { case_id: "case-expect::base", status: "scored", label_source: "expect", metrics: values, hits: [{ chunk_id: "chunk-rollback", rank: 1, score: 0.92, content_hash: "fixture-hash-rollback", grade: 3, reason: "测试数据：包含回滚步骤" }], error: null },
      { case_id: "case-judge::base", status: "scored", label_source: "judge", metrics: { ...values, recall_at_k: null }, hits: [{ chunk_id: "chunk-deploy", rank: 1, score: 0.85, content_hash: "fixture-hash-deploy", grade: 2, reason: "测试数据：预置 Judge 理由，没有调用模型" }], error: null },
      { case_id: "case-null::base", status: "not_assessable", label_source: "judge", metrics: nullMetrics, hits: [{ chunk_id: "chunk-team", rank: 1, score: 0, content_hash: "fixture-hash-team", grade: null, reason: "测试数据：没有标注证据" }], error: null },
      { case_id: "case-failed::base", status: "failed", label_source: "expect", metrics: nullMetrics, hits: [], error: "测试数据：检索超时（模拟）" },
    ] };
  }
  return { kbs, docs, chunks, entities, edges, datasets, runs, report };
}
