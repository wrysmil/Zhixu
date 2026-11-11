var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));

// .ai-runtime-artifacts/verifications/knowledge-experience/fixture-check.ts
var import_strict = __toESM(require("node:assert/strict"));

// .ai-runtime-artifacts/verifications/knowledge-experience/fixture-data.ts
var FIXTURE_TOKEN = "fixture-only-no-credentials";
var FIXTURE_CSRF = "fixture-only-csrf";
var timestamp = "2026-11-11T02:00:00Z";
var metricIds = ["hit_at_k", "precision_at_k", "mrr", "ndcg_at_k", "recall_at_k", "pool_coverage", "unscored_rate"];
var nullMetrics = Object.fromEntries(metricIds.map((id) => [id, null]));
var metrics = { hit_at_k: 1, precision_at_k: 0.5, mrr: 0.75, ndcg_at_k: 0.8, recall_at_k: 0.5, pool_coverage: 0.4, unscored_rate: 0 };
var candidateMetrics = { ...metrics, precision_at_k: 0.25, recall_at_k: null, pool_coverage: null };
var metricSpecs = metricIds.map((id, index) => ({
  metric_id: id,
  label: ["\u547D\u4E2D\u7387", "\u51C6\u786E\u7387", "\u9996\u4E2A\u547D\u4E2D\u5012\u6570", "\u5F52\u4E00\u5316\u6298\u635F\u7D2F\u8BA1\u589E\u76CA", "\u53EC\u56DE\u7387", "\u5019\u9009\u6C60\u76F8\u5173\u5360\u6BD4", "\u672A\u8BC4\u7387"][index],
  formula: ["\u547D\u4E2D\u67E5\u8BE2\u6570 \xF7 \u53EF\u8BC4\u67E5\u8BE2\u6570", "\u76F8\u5173\u5757\u6570(top-k) \xF7 k", "\u5E73\u5747(1 \xF7 \u9996\u4E2A\u76F8\u5173\u5757\u540D\u6B21)", "DCG \xF7 IDCG", "\u76F8\u5173\u5757\u6570(top-k) \xF7 \u76F8\u5173\u5757\u6570(pool)", "\u76F8\u5173\u5757\u6570(pool) \xF7 \u6C60\u5019\u9009\u603B\u6570", "not_assessable \u67E5\u8BE2\u6570 \xF7 \u603B\u67E5\u8BE2\u6570"][index],
  numerator: "\u6D4B\u8BD5\u53E3\u5F84\u5206\u5B50",
  denominator: "\u6D4B\u8BD5\u53E3\u5F84\u5206\u6BCD",
  direction: id === "unscored_rate" ? "lower_is_better" : "higher_is_better",
  description: "\u672C\u5B57\u6BB5\u7531\u5939\u5177\u6A21\u62DF\u540E\u7AEF\u4E0B\u53D1\uFF0C\u4EC5\u4F9B\u754C\u9762\u9A8C\u6536\uFF0C\u4E0D\u4EE3\u8868\u771F\u5B9E\u8BC4\u6D4B\u3002"
}));
function createFixtureData() {
  const kbs = [
    { id: "kb-product", name: "\u4EA7\u54C1\u4E0E\u90E8\u7F72\u624B\u518C", description: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u90E8\u7F72\u3001\u56DE\u6EDA\u4E0E\u6743\u9650\u8BF4\u660E", workspaceId: "fixture-workspace", visibility: "workspace", isEnabled: true, documentCount: 3, createdAt: timestamp, updatedAt: timestamp },
    { id: "kb-team", name: "\u56E2\u961F\u534F\u4F5C\u4E0E\u9879\u76EE\u590D\u76D8\u2014\u2014\u7528\u4E8E\u68C0\u67E5\u957F\u6807\u9898\u7684\u77E5\u8BC6\u5E93", description: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u5168\u5C40\u5171\u4EAB\u8D44\u6599", workspaceId: "fixture-workspace", visibility: "global", isEnabled: true, documentCount: 1, createdAt: timestamp, updatedAt: timestamp },
    { id: "kb-empty", name: "\u5F85\u6574\u7406\u8D44\u6599", description: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u505C\u7528\u4E14\u6682\u65E0\u6587\u6863", workspaceId: "fixture-workspace", visibility: "workspace", isEnabled: false, documentCount: 0, createdAt: timestamp, updatedAt: timestamp }
  ];
  const docs = [
    { id: "doc-ready", kbId: "kb-product", title: "\u90E8\u7F72\u4E0E\u56DE\u6EDA\u64CD\u4F5C\u6307\u5357", originalName: "deployment.md", status: "ready", chunkCount: 2, charCount: 80, mime: "text/markdown", vectorStatus: "ready", createdAt: timestamp, updatedAt: timestamp },
    { id: "doc-failed", kbId: "kb-product", title: "\u65E0\u6CD5\u89E3\u6790\u7684\u6D4B\u8BD5\u9644\u4EF6", originalName: "broken.pdf", status: "failed", chunkCount: 0, charCount: 0, mime: "application/pdf", errorCode: "PARSE_FAILED", error: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u65E0\u6CD5\u89E3\u6790\u6587\u4EF6\uFF0C\u8BF7\u68C0\u67E5\u683C\u5F0F\u540E\u91CD\u8BD5\u3002", createdAt: timestamp, updatedAt: timestamp },
    { id: "doc-processing", kbId: "kb-product", title: "\u6B63\u5728\u5904\u7406\u7684\u6D4B\u8BD5\u6587\u6863", originalName: "processing.txt", status: "processing", chunkCount: 0, charCount: 0, mime: "text/plain", createdAt: timestamp, updatedAt: timestamp },
    { id: "doc-team", kbId: "kb-team", title: "\u56E2\u961F\u534F\u4F5C\u7EA6\u5B9A", originalName: "team.txt", status: "ready", chunkCount: 1, charCount: 25, mime: "text/plain", createdAt: timestamp, updatedAt: timestamp }
  ];
  const chunks = [
    { id: "chunk-deploy", docId: "doc-ready", kbId: "kb-product", index: 0, heading: "\u90E8\u7F72", page: null, locator: "\u7B2C 1 \u8282", content: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u90E8\u7F72\u524D\u68C0\u67E5\u914D\u7F6E\uFF0C\u90E8\u7F72\u5B8C\u6210\u540E\u786E\u8BA4\u5065\u5EB7\u72B6\u6001\u3002", charCount: 28 },
    { id: "chunk-rollback", docId: "doc-ready", kbId: "kb-product", index: 1, heading: "\u56DE\u6EDA", page: 2, locator: "\u7B2C 2 \u9875", content: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u56DE\u6EDA\u90E8\u7F72\u65F6\u5148\u9009\u62E9\u4E0A\u4E00\u7A33\u5B9A\u7248\u672C\uFF0C\u518D\u9A8C\u8BC1\u670D\u52A1\u3002", charCount: 29 },
    { id: "chunk-team", docId: "doc-team", kbId: "kb-team", index: 0, heading: "\u534F\u4F5C", page: null, locator: "\u7B2C 1 \u8282", content: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u56E2\u961F\u534F\u4F5C\u9700\u8981\u8BB0\u5F55\u51B3\u7B56\u5E76\u6CE8\u660E\u8D1F\u8D23\u4EBA\u3002", charCount: 25 }
  ];
  const entities = [
    { id: "entity-deploy", name: "\u90E8\u7F72", type: "process", description: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u53D1\u5E03\u670D\u52A1\u7684\u6D41\u7A0B", mentionCount: 2 },
    { id: "entity-rollback", name: "\u56DE\u6EDA", type: "process", description: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u6062\u590D\u7A33\u5B9A\u7248\u672C", mentionCount: 1 },
    { id: "entity-version", name: "\u7A33\u5B9A\u7248\u672C", type: "concept", description: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u5DF2\u9A8C\u8BC1\u7248\u672C", mentionCount: 1 }
  ];
  const edges = [
    { source: "entity-deploy", target: "entity-rollback", relation: "\u5931\u8D25\u65F6\u6062\u590D", weight: 1 },
    { source: "entity-rollback", target: "entity-version", relation: "\u6062\u590D\u5230", weight: 1 }
  ];
  const cases = [
    { id: "case-expect", query: "\u5982\u4F55\u56DE\u6EDA\u90E8\u7F72\uFF1F", expect_any: ["\u56DE\u6EDA", "\u7A33\u5B9A\u7248\u672C"], kb_ids: ["kb-product"], mode: "hybrid", limit: 5, label_source: "expect", ordinal: 0 },
    { id: "case-judge", query: "\u90E8\u7F72\u524D\u9700\u8981\u68C0\u67E5\u4EC0\u4E48\uFF1F", expect_any: [], kb_ids: ["kb-product"], mode: "semantic", limit: 5, label_source: "judge", ordinal: 1 },
    { id: "case-null", query: "\u4E0D\u5B58\u5728\u7684\u8BC1\u636E\u5982\u4F55\u8BC4\u5206\uFF1F", expect_any: [], kb_ids: ["kb-product"], mode: "keyword", limit: 5, label_source: "judge", ordinal: 2 },
    { id: "case-failed", query: "\u5931\u8D25\u67E5\u8BE2\u5982\u4F55\u663E\u793A\uFF1F", expect_any: ["\u6D4B\u8BD5"], kb_ids: ["kb-product"], mode: "hybrid", limit: 5, label_source: "expect", ordinal: 3 }
  ];
  const datasets = [
    { id: "dataset-main", name: "\u90E8\u7F72\u68C0\u7D22\u9A8C\u6536\u9898\u96C6", notes: "\u6D4B\u8BD5\u6570\u636E\uFF1Aexpect / judge / \u672A\u8BC4\u5206 / \u5931\u8D25", revision: "1", cases, created_at: timestamp, updated_at: timestamp },
    { id: "dataset-empty", name: "\u7A7A\u9898\u96C6", notes: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u7A7A\u72B6\u6001", revision: "1", cases: [], created_at: timestamp, updated_at: timestamp }
  ];
  const groups = (values = metrics) => ({ groups: [
    { label_source: "expect", total_queries: 2, scored_queries: 1, metrics: values },
    { label_source: "judge", total_queries: 2, scored_queries: 1, metrics: { ...values, recall_at_k: null, pool_coverage: null, unscored_rate: 0.5 } }
  ] });
  const runs = ["run-base", "run-candidate"].map((id, index) => ({
    id,
    dataset_id: "dataset-main",
    status: "completed",
    configs: [{ id: "base", name: "\u9ED8\u8BA4\u914D\u7F6E", overrides: { mode: "hybrid", limit: 5 } }],
    judge: { profile_id: "fixture-judge", mock: true },
    budgets: { max_queries: 4 },
    metrics: groups(index ? candidateMetrics : metrics),
    progress: { completed: 4, total: 4 },
    pool: { degraded: [] },
    error: null,
    created_at: index ? "2026-11-11T03:00:00Z" : timestamp,
    finished_at: "2026-11-11T03:01:00Z"
  }));
  function report(id) {
    const run = runs.find((item) => item.id === id);
    const values = id === "run-base" ? metrics : candidateMetrics;
    return { run_id: id, status: run?.status ?? "completed", metrics: run?.metrics ?? groups(), results: [
      { case_id: "case-expect::base", status: "scored", label_source: "expect", metrics: values, hits: [{ chunk_id: "chunk-rollback", rank: 1, score: 0.92, content_hash: "fixture-hash-rollback", grade: 3, reason: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u5305\u542B\u56DE\u6EDA\u6B65\u9AA4" }], error: null },
      { case_id: "case-judge::base", status: "scored", label_source: "judge", metrics: { ...values, recall_at_k: null }, hits: [{ chunk_id: "chunk-deploy", rank: 1, score: 0.85, content_hash: "fixture-hash-deploy", grade: 2, reason: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u9884\u7F6E Judge \u7406\u7531\uFF0C\u6CA1\u6709\u8C03\u7528\u6A21\u578B" }], error: null },
      { case_id: "case-null::base", status: "not_assessable", label_source: "judge", metrics: nullMetrics, hits: [{ chunk_id: "chunk-team", rank: 1, score: 0, content_hash: "fixture-hash-team", grade: null, reason: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u6CA1\u6709\u6807\u6CE8\u8BC1\u636E" }], error: null },
      { case_id: "case-failed::base", status: "failed", label_source: "expect", metrics: nullMetrics, hits: [], error: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u68C0\u7D22\u8D85\u65F6\uFF08\u6A21\u62DF\uFF09" }
    ] };
  }
  return { kbs, docs, chunks, entities, edges, datasets, runs, report };
}

// .ai-runtime-artifacts/verifications/knowledge-experience/fixture-transport.ts
function createFixtureTransport() {
  const data = createFixtureData();
  const audit = [];
  let sequence = 0;
  const controls = { delayMs: 0, failNext: "", conflictNext: false };
  const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const error = (code, status) => json({ error: { code, message: `\u6D4B\u8BD5\u6570\u636E\uFF1A${code}` } }, status);
  const id = (prefix) => `${prefix}-fixture-${++sequence}`;
  const syncCounts = () => data.kbs.forEach((kb) => {
    kb.documentCount = data.docs.filter((doc) => doc.kbId === kb.id).length;
  });
  const wait = async (signal) => {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (controls.delayMs) await new Promise((resolve, reject) => {
      const abort = () => {
        clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      };
      const timer = setTimeout(() => {
        signal?.removeEventListener("abort", abort);
        resolve();
      }, controls.delayMs);
      signal?.addEventListener("abort", abort, { once: true });
    });
  };
  const fetchMock = async (input, init) => {
    const request = new Request(new URL(input instanceof Request ? input.url : String(input), "http://127.0.0.1:5186"), input instanceof Request ? input : void 0);
    const url = new URL(request.url);
    const method = (init?.method ?? request.method).toUpperCase();
    const headers = new Headers(init?.headers ?? request.headers);
    const path = url.pathname;
    const q = url.searchParams;
    audit.push({ kind: "fetch", method, path });
    await wait(init?.signal ?? request.signal);
    const knownHost = url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const knowledge = knownHost && url.port === "5186" && path.startsWith("/api/settings/knowledge/");
    const evaluation = knownHost && url.port === "8790" && path.startsWith("/api/eval/v1/");
    if (!knowledge && !evaluation) throw new Error(`FIXTURE_OUTBOUND_BLOCKED: ${method} ${path}`);
    if (controls.failNext && path.includes(controls.failNext)) {
      controls.failNext = "";
      return error("FIXTURE_INJECTED_FAILURE", 503);
    }
    if (knowledge) {
      if (method !== "GET" || headers.get("Authorization") !== `Bearer ${FIXTURE_TOKEN}`) return error("FIXTURE_AUTH_FAILED", 401);
      const route2 = path.slice("/api/settings/knowledge/".length);
      const kbId = q.get("kbId");
      if (route2 === "kbs") return json({ enabled: true, kbs: data.kbs });
      if (route2 === "stats") return json({ enabled: true, kbs: data.kbs.length, documents: data.docs.length, chunks: data.chunks.length, pending: data.docs.filter((doc) => doc.status === "processing").length, vector: { ok_docs: data.docs.filter((doc) => doc.status === "ready").length }, by_status: Object.fromEntries(["ready", "failed", "processing"].map((status) => [status, data.docs.filter((doc) => doc.status === status).length])), drift: 0 });
      if (route2 === "docs") {
        const docs = data.docs.filter((doc) => (!kbId || doc.kbId === kbId) && (!q.get("status") || doc.status === q.get("status")));
        const offset = Number(q.get("offset") ?? 0), limit = Number(q.get("limit") ?? 20);
        return json({ enabled: true, documents: docs.slice(offset, offset + limit), total: docs.length });
      }
      if (route2 === "doc") {
        const doc = data.docs.find((item) => item.id === q.get("id"));
        if (!doc) return error("FIXTURE_DOC_NOT_FOUND", 404);
        const chunks = data.chunks.filter((chunk) => chunk.docId === doc.id);
        return json({ enabled: true, document: doc, chunks: chunks.slice(0, Number(q.get("chunkLimit") ?? 100)), chunkCount: chunks.length });
      }
      if (route2 === "chunk") return json({ enabled: true, chunk: data.chunks.find((chunk) => chunk.id === q.get("chunkId")) ?? null });
      if (route2 === "search") {
        const query = (q.get("query") ?? "").toLowerCase();
        const selected = q.get("kbIds")?.split(",");
        const hits = data.chunks.filter((chunk) => (!kbId || chunk.kbId === kbId) && (!selected || selected.includes(chunk.kbId)) && `${chunk.heading}${chunk.content}`.toLowerCase().includes(query));
        return json({ enabled: true, modeUsed: q.get("mode") ?? "hybrid", degraded: [], reason: null, routes: ["fixture"], hits: hits.slice(0, Number(q.get("limit") ?? 8)).map((chunk, index) => ({ ...chunk, chunkId: chunk.id, score: 0.92 - index * 0.1 })) });
      }
      const graphEntities = kbId && kbId !== "kb-product" ? [] : data.entities;
      if (route2 === "graph/stats") return json({ enabled: true, backend: "fixture", status: "ready", entities: graphEntities.length, relations: graphEntities.length ? data.edges.length : 0, docsReady: 1, docsPending: 0, docsFailed: 0, docsSkipped: 0, truncatedDocs: 0 });
      if (route2 === "graph/entities") {
        const entities = graphEntities.filter((entity) => (!q.get("q") || entity.name.includes(q.get("q"))) && (!q.get("type") || entity.type === q.get("type")));
        return json({ entities, total: entities.length });
      }
      if (route2 === "graph/entity") {
        const entity = data.entities.find((item) => item.id === q.get("id"));
        if (!entity) return error("FIXTURE_ENTITY_NOT_FOUND", 404);
        return json({ entity, mentions: [{ docId: "doc-ready", docTitle: "\u90E8\u7F72\u4E0E\u56DE\u6EDA\u64CD\u4F5C\u6307\u5357", chunkId: "chunk-rollback", chunkIndex: 1, heading: "\u56DE\u6EDA" }], neighbors: data.edges.filter((edge) => edge.source === entity.id || edge.target === entity.id).map((edge) => ({ entity: data.entities.find((item) => item.id === (edge.source === entity.id ? edge.target : edge.source)), relation: edge.relation, direction: edge.source === entity.id ? "out" : "in", weight: edge.weight })) });
      }
      if (route2 === "graph/subgraph") return json({ nodes: data.entities, edges: data.edges });
      return error("FIXTURE_ROUTE_NOT_IMPLEMENTED", 501);
    }
    const route = path.slice("/api/eval/v1".length);
    if (route === "/auth/session" && method === "GET") return json({ csrf_token: FIXTURE_CSRF });
    if (headers.get("X-CSRF-Token") !== FIXTURE_CSRF) return error("FIXTURE_CSRF_FAILED", 403);
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    if (route === "/knowledge/metrics" && method === "GET") return json({ metrics: metricSpecs });
    if (route === "/knowledge/datasets") {
      if (method === "GET") return json({ datasets: data.datasets });
      if (method === "POST") {
        const dataset = { id: id("dataset"), name: String(body.name ?? "\u65B0\u6D4B\u8BD5\u9898\u96C6"), notes: String(body.notes ?? ""), revision: "1", cases: body.cases ?? [], created_at: "2026-11-11T04:00:00Z", updated_at: "2026-11-11T04:00:00Z" };
        data.datasets.push(dataset);
        return json(dataset, 201);
      }
    }
    const datasetMatch = /^\/knowledge\/datasets\/([^/]+)$/.exec(route);
    if (datasetMatch) {
      const dataset = data.datasets.find((item) => item.id === decodeURIComponent(datasetMatch[1]));
      if (!dataset) return error("FIXTURE_DATASET_NOT_FOUND", 404);
      if (method === "GET") return json(dataset);
      if (method === "DELETE") {
        data.datasets.splice(data.datasets.indexOf(dataset), 1);
        return new Response(null, { status: 204 });
      }
      if (method === "PATCH") {
        if (controls.conflictNext || String(body.base_revision) !== dataset.revision) {
          controls.conflictNext = false;
          return error("REVISION_CONFLICT", 409);
        }
        if (typeof body.name === "string") dataset.name = body.name;
        if (typeof body.notes === "string") dataset.notes = body.notes;
        if (Array.isArray(body.cases)) dataset.cases = body.cases;
        dataset.revision = String(Number(dataset.revision) + 1);
        return json(dataset);
      }
    }
    if (route === "/knowledge/runs") {
      if (method === "GET") return json({ runs: data.runs.filter((run) => !q.get("dataset_id") || run.dataset_id === q.get("dataset_id")) });
      if (method === "POST") {
        const run = structuredClone(data.runs[0]);
        Object.assign(run, { id: id("run"), dataset_id: body.dataset_id, status: "running", created_at: "2026-11-11T04:00:00Z", finished_at: null, progress: { completed: 0, total: 4 } });
        data.runs.push(run);
        return json(run, 201);
      }
    }
    const runMatch = /^\/knowledge\/runs\/([^/]+)(?:\/(report|cancel))?$/.exec(route);
    if (runMatch) {
      const run = data.runs.find((item) => item.id === decodeURIComponent(runMatch[1]));
      if (!run) return error("FIXTURE_RUN_NOT_FOUND", 404);
      if (runMatch[2] === "cancel" && method === "POST") {
        run.status = "cancelled";
        return new Response(null, { status: 204 });
      }
      if (method === "GET") return json(runMatch[2] === "report" ? data.report(run.id) : run);
    }
    if (route === "/knowledge/compare" && method === "GET") {
      const base = data.runs.find((run) => run.id === q.get("base")), candidate = data.runs.find((run) => run.id === q.get("candidate"));
      if (!base || !candidate) return error("FIXTURE_RUN_NOT_FOUND", 404);
      const metricValue = (runId, metric) => runId === "run-base" ? metrics[metric] : candidateMetrics[metric];
      return json({ base: { id: base.id }, candidate: { id: candidate.id }, metrics: ["expect", "judge"].flatMap((label_source) => metricIds.map((metric_id) => {
        const a = label_source === "judge" && metric_id === "recall_at_k" ? null : metricValue(base.id, metric_id);
        const b = label_source === "judge" && metric_id === "recall_at_k" ? null : metricValue(candidate.id, metric_id);
        return { metric_id, label_source, label: metricSpecs.find((spec) => spec.metric_id === metric_id)?.label, direction: metric_id === "unscored_rate" ? "lower_is_better" : "higher_is_better", base: a, candidate: b, delta: a === null || b === null ? null : b - a };
      })), regressions: base.id === candidate.id ? [] : [{ case_id: "case-expect::base", config_id: "base", metric_id: "precision_at_k", base: 0.5, candidate: 0.25, delta: -0.25, reason: "\u6D4B\u8BD5\u6570\u636E\uFF1A\u5019\u9009\u914D\u7F6E\u51C6\u786E\u7387\u4E0B\u964D" }] });
    }
    return error("FIXTURE_ROUTE_NOT_IMPLEMENTED", 501);
  };
  const requestMutation = async (action, payload = {}) => {
    audit.push({ kind: "mutation", method: action, path: "fixture-memory" });
    await wait();
    let result;
    const kb = data.kbs.find((item) => item.id === payload.id);
    const doc = data.docs.find((item) => item.id === payload.id);
    const removeDoc = (docId) => {
      data.docs = data.docs.filter((item) => item.id !== docId);
      data.chunks = data.chunks.filter((item) => item.docId !== docId);
    };
    switch (action) {
      case "knowledge.kb.create": {
        const kbId = id("kb");
        data.kbs.push({ id: kbId, name: String(payload.name), description: String(payload.description ?? ""), workspaceId: "fixture-workspace", visibility: String(payload.visibility ?? "workspace"), isEnabled: true, documentCount: 0 });
        result = { id: kbId };
        break;
      }
      case "knowledge.kb.update":
        if (!kb) throw new Error("FIXTURE_KB_NOT_FOUND");
        for (const key of ["name", "description"]) if (typeof payload[key] === "string") kb[key] = payload[key];
        if (typeof payload.isEnabled === "boolean") kb.isEnabled = payload.isEnabled;
        result = { ok: true, id: kb.id };
        break;
      case "knowledge.kb.delete":
        if (!kb) throw new Error("FIXTURE_KB_NOT_FOUND");
        data.docs.filter((item) => item.kbId === kb.id).forEach((item) => removeDoc(item.id));
        data.kbs.splice(data.kbs.indexOf(kb), 1);
        result = { ok: true, id: kb.id };
        break;
      case "knowledge.doc.delete":
        if (!doc) throw new Error("FIXTURE_DOC_NOT_FOUND");
        removeDoc(doc.id);
        result = { ok: true, id: doc.id };
        break;
      case "knowledge.doc.add":
      case "knowledge.doc.add_file": {
        const newDoc = { id: id("doc"), kbId: String(payload.kbId), title: String(payload.title ?? payload.name ?? "\u6D4B\u8BD5\u8DEF\u5F84\u5BFC\u5165"), originalName: String(payload.name ?? "fixture.txt"), status: "processing", chunkCount: 0, charCount: 0 };
        data.docs.push(newDoc);
        result = { document: newDoc };
        break;
      }
      case "knowledge.doc.reindex":
        if (!doc) throw new Error("FIXTURE_DOC_NOT_FOUND");
        doc.status = "processing";
        doc.error = void 0;
        result = { ok: true, queued: true };
        break;
      case "knowledge.kb.reindex": {
        if (!kb) throw new Error("FIXTURE_KB_NOT_FOUND");
        const docs = data.docs.filter((item) => item.kbId === kb.id);
        docs.forEach((item) => {
          item.status = "processing";
        });
        result = { ok: true, queued: docs.length, documentIds: docs.map((item) => item.id) };
        break;
      }
      case "knowledge.repair":
        result = { ok: true, repaired: 0, fixture: true };
        break;
      case "knowledge.graph.extract":
      case "knowledge.graph.rebuild":
        result = { ok: true, queued: 1, fixture: true };
        break;
      default:
        throw new Error(`FIXTURE_MUTATION_BLOCKED: ${action}`);
    }
    syncCounts();
    return structuredClone(result);
  };
  return { data, audit, controls, fetch: fetchMock, client: { requestMutation } };
}

// .ai-runtime-artifacts/verifications/knowledge-experience/fixture-check.ts
async function main() {
  const fixture = createFixtureTransport();
  const knowledge = "http://127.0.0.1:5186/api/settings/knowledge";
  const evaluation = "http://127.0.0.1:8790/api/eval/v1";
  const auth = { Authorization: `Bearer ${FIXTURE_TOKEN}` };
  const csrf = { "X-CSRF-Token": FIXTURE_CSRF };
  const get = async (url, headers = auth) => (await fixture.fetch(url, { headers })).json();
  import_strict.default.equal((await get(`${knowledge}/kbs`)).kbs.length, 3);
  import_strict.default.equal((await get(`${knowledge}/docs?kbId=kb-product&offset=1&limit=1`)).documents[0].status, "failed");
  import_strict.default.equal((await get(`${knowledge}/doc?id=doc-ready`)).chunks.length, 2);
  import_strict.default.equal((await get(`${knowledge}/search?query=\u56DE\u6EDA&kbId=kb-product`)).hits[0].chunkId, "chunk-rollback");
  import_strict.default.equal((await get(`${knowledge}/search?query=\u65E0\u5339\u914D`)).hits.length, 0);
  import_strict.default.equal((await get(`${knowledge}/graph/entities?kbId=kb-product`)).entities.length, 3);
  import_strict.default.equal((await get(`${evaluation}/auth/session`, {})).csrf_token, FIXTURE_CSRF);
  import_strict.default.equal((await fixture.fetch(`${evaluation}/knowledge/runs`)).status, 403);
  const evalGet = (path) => get(`${evaluation}${path}`, csrf);
  import_strict.default.equal((await evalGet("/knowledge/runs")).runs.length, 2);
  import_strict.default.equal((await evalGet("/knowledge/datasets")).datasets.length, 2);
  const report = await evalGet("/knowledge/runs/run-candidate/report");
  import_strict.default.equal(report.metrics.groups.length, 2);
  import_strict.default.equal(report.results[2].metrics.hit_at_k, null);
  import_strict.default.equal(report.results[2].hits[0].grade, null);
  import_strict.default.equal(report.results[0].label_source, "expect");
  import_strict.default.equal(report.results[1].label_source, "judge");
  const comparison = await evalGet("/knowledge/compare?base=run-base&candidate=run-candidate");
  import_strict.default.equal(comparison.metrics.length, 14);
  import_strict.default.equal(comparison.metrics.find((item) => item.metric_id === "recall_at_k").delta, null);
  import_strict.default.equal(comparison.regressions.length, 1);
  await fixture.client.requestMutation("knowledge.kb.create", { name: "\u65B0\u5EFA\u6D4B\u8BD5\u5E93" });
  import_strict.default.equal((await get(`${knowledge}/kbs`)).kbs.length, 4);
  import_strict.default.equal(createFixtureTransport().data.kbs.length, 3);
  await fixture.client.requestMutation("knowledge.doc.reindex", { id: "doc-failed" });
  import_strict.default.equal(fixture.data.docs.find((doc) => doc.id === "doc-failed")?.status, "processing");
  const response = await fixture.fetch(`${evaluation}/knowledge/datasets/dataset-main`, { method: "PATCH", headers: csrf, body: JSON.stringify({ base_revision: "wrong", name: "\u51B2\u7A81" }) });
  import_strict.default.equal(response.status, 409);
  import_strict.default.equal(fixture.data.datasets[0].name, "\u90E8\u7F72\u68C0\u7D22\u9A8C\u6536\u9898\u96C6");
  fixture.controls.failNext = "/kbs";
  import_strict.default.equal((await fixture.fetch(`${knowledge}/kbs`, { headers: auth })).status, 503);
  import_strict.default.equal((await fixture.fetch(`${knowledge}/kbs`, { headers: auth })).status, 200);
  await import_strict.default.rejects(fixture.fetch("https://example.invalid/api"), /FIXTURE_OUTBOUND_BLOCKED/);
  await import_strict.default.rejects(fixture.fetch("http://127.0.0.1:8765/api/settings/knowledge/kbs"), /FIXTURE_OUTBOUND_BLOCKED/);
  await import_strict.default.rejects(fixture.client.requestMutation("paid.model.call"), /FIXTURE_MUTATION_BLOCKED/);
  console.log("PASS: fixture wire shapes, grouped/null evidence, local mutations, CAS, injected failure and outbound rejection");
}
void main();
