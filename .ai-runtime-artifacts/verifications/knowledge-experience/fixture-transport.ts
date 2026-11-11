import type { ZhixuClient } from "../../../webui/src/lib/zhixu-client";
import { createFixtureData, FIXTURE_CSRF, FIXTURE_TOKEN, metricIds, metricSpecs, metrics, candidateMetrics } from "./fixture-data";

type Payload = Record<string, unknown>;
export function createFixtureTransport() {
  const data = createFixtureData();
  const audit: Array<{ kind: string; method: string; path: string; query?: string; at?: number }> = [];
  let sequence = 0;
  const controls = { delayMs: 0, failNext: "", conflictNext: false };
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
  const error = (code: string, status: number) => json({ error: { code, message: `测试数据：${code}` } }, status);
  const id = (prefix: string) => `${prefix}-fixture-${++sequence}`;
  const syncCounts = () => data.kbs.forEach((kb) => { kb.documentCount = data.docs.filter((doc) => doc.kbId === kb.id).length; });
  const wait = async (signal?: AbortSignal | null) => {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (controls.delayMs) await new Promise<void>((resolve, reject) => {
      const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
      const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, controls.delayMs);
      signal?.addEventListener("abort", abort, { once: true });
    });
  };
  const fetchMock: typeof fetch = async (input, init) => {
    const request = new Request(new URL(input instanceof Request ? input.url : String(input), "http://127.0.0.1:5186"), input instanceof Request ? input : undefined);
    const url = new URL(request.url);
    const method = (init?.method ?? request.method).toUpperCase();
    const headers = new Headers(init?.headers ?? request.headers);
    const path = url.pathname;
    const q = url.searchParams;
    audit.push({ kind: "fetch", method, path, query: url.search, at: Date.now() }); // Fixture query only; never log bodies, headers or uploaded contents.
    await wait(init?.signal ?? request.signal);
    const knownHost = url.hostname === "127.0.0.1" || url.hostname === "localhost";
    const knowledge = knownHost && url.port === "5186" && path.startsWith("/api/settings/knowledge/");
    const evaluation = knownHost && url.port === "8790" && path.startsWith("/api/eval/v1/");
    if (!knowledge && !evaluation) throw new Error(`FIXTURE_OUTBOUND_BLOCKED: ${method} ${path}`);
    if (controls.failNext && path.includes(controls.failNext)) { controls.failNext = ""; return error("FIXTURE_INJECTED_FAILURE", 503); }
    if (knowledge) {
      if (method !== "GET" || headers.get("Authorization") !== `Bearer ${FIXTURE_TOKEN}`) return error("FIXTURE_AUTH_FAILED", 401);
      const route = path.slice("/api/settings/knowledge/".length);
      const kbId = q.get("kbId");
      if (route === "kbs") return json({ enabled: true, kbs: data.kbs });
      if (route === "stats") return json({ enabled: true, kbs: data.kbs.length, documents: data.docs.length, chunks: data.chunks.length, pending: data.docs.filter((doc) => doc.status === "processing").length, vector: { ok_docs: data.docs.filter((doc) => doc.status === "ready").length }, by_status: Object.fromEntries(["ready", "failed", "processing"].map((status) => [status, data.docs.filter((doc) => doc.status === status).length])), drift: 0 });
      if (route === "docs") {
        const docs = data.docs.filter((doc) => (!kbId || doc.kbId === kbId) && (!q.get("status") || doc.status === q.get("status")));
        const offset = Number(q.get("offset") ?? 0), limit = Number(q.get("limit") ?? 20);
        return json({ enabled: true, documents: docs.slice(offset, offset + limit), total: docs.length });
      }
      if (route === "doc") {
        const doc = data.docs.find((item) => item.id === q.get("id"));
        if (!doc) return error("FIXTURE_DOC_NOT_FOUND", 404);
        const chunks = data.chunks.filter((chunk) => chunk.docId === doc.id);
        return json({ enabled: true, document: doc, chunks: chunks.slice(0, Number(q.get("chunkLimit") ?? 100)), chunkCount: chunks.length });
      }
      if (route === "chunk") return json({ enabled: true, chunk: data.chunks.find((chunk) => chunk.id === q.get("chunkId")) ?? null });
      if (route === "search") {
        const query = (q.get("query") ?? "").toLowerCase();
        const selected = q.get("kbIds")?.split(",");
        const hits = data.chunks.filter((chunk) => (!kbId || chunk.kbId === kbId) && (!selected || selected.includes(chunk.kbId)) && `${chunk.heading}${chunk.content}`.toLowerCase().includes(query));
        return json({ enabled: true, modeUsed: q.get("mode") ?? "hybrid", degraded: [], reason: null, routes: ["fixture"], hits: hits.slice(0, Number(q.get("limit") ?? 8)).map((chunk, index) => ({ ...chunk, chunkId: chunk.id, score: 0.92 - index * 0.1 })) });
      }
      const graphEntities = kbId && kbId !== "kb-product" ? [] : data.entities;
      if (route === "graph/stats") return json({ enabled: true, backend: "fixture", status: "ready", entities: graphEntities.length, relations: graphEntities.length ? data.edges.length : 0, docsReady: 1, docsPending: 0, docsFailed: 0, docsSkipped: 0, truncatedDocs: 0 });
      if (route === "graph/entities") {
        const entities = graphEntities.filter((entity) => (!q.get("q") || entity.name.includes(q.get("q")!)) && (!q.get("type") || entity.type === q.get("type")));
        return json({ entities, total: entities.length });
      }
      if (route === "graph/entity") {
        const entity = data.entities.find((item) => item.id === q.get("id"));
        if (!entity) return error("FIXTURE_ENTITY_NOT_FOUND", 404);
        return json({ entity, mentions: [{ docId: "doc-ready", docTitle: "部署与回滚操作指南", chunkId: "chunk-rollback", chunkIndex: 1, heading: "回滚" }], neighbors: data.edges.filter((edge) => edge.source === entity.id || edge.target === entity.id).map((edge) => ({ entity: data.entities.find((item) => item.id === (edge.source === entity.id ? edge.target : edge.source)), relation: edge.relation, direction: edge.source === entity.id ? "out" : "in", weight: edge.weight })) });
      }
      if (route === "graph/subgraph") return json({ nodes: data.entities, edges: data.edges });
      return error("FIXTURE_ROUTE_NOT_IMPLEMENTED", 501);
    }
    const route = path.slice("/api/eval/v1".length);
    // Return a valid synthetic session first: production ensureSession never reads saved secrets.
    if (route === "/auth/session" && method === "GET") return json({ csrf_token: FIXTURE_CSRF });
    if (headers.get("X-CSRF-Token") !== FIXTURE_CSRF) return error("FIXTURE_CSRF_FAILED", 403);
    const body = init?.body ? JSON.parse(String(init.body)) as Payload : {};
    if (route === "/knowledge/metrics" && method === "GET") return json({ metrics: metricSpecs });
    if (route === "/knowledge/datasets") {
      if (method === "GET") return json({ datasets: data.datasets });
      if (method === "POST") {
        const dataset = { id: id("dataset"), name: String(body.name ?? "新测试题集"), notes: String(body.notes ?? ""), revision: "1", cases: (body.cases ?? []) as typeof data.datasets[number]["cases"], created_at: "2026-11-11T04:00:00Z", updated_at: "2026-11-11T04:00:00Z" };
        data.datasets.push(dataset); return json(dataset, 201);
      }
    }
    const datasetMatch = /^\/knowledge\/datasets\/([^/]+)$/.exec(route);
    if (datasetMatch) {
      const dataset = data.datasets.find((item) => item.id === decodeURIComponent(datasetMatch[1]));
      if (!dataset) return error("FIXTURE_DATASET_NOT_FOUND", 404);
      if (method === "GET") return json(dataset);
      if (method === "DELETE") { data.datasets.splice(data.datasets.indexOf(dataset), 1); return new Response(null, { status: 204 }); }
      if (method === "PATCH") {
        if (controls.conflictNext || String(body.base_revision) !== dataset.revision) { controls.conflictNext = false; return error("REVISION_CONFLICT", 409); }
        if (typeof body.name === "string") dataset.name = body.name;
        if (typeof body.notes === "string") dataset.notes = body.notes;
        if (Array.isArray(body.cases)) dataset.cases = body.cases as typeof dataset.cases;
        dataset.revision = String(Number(dataset.revision) + 1); return json(dataset);
      }
    }
    if (route === "/knowledge/runs") {
      if (method === "GET") return json({ runs: data.runs.filter((run) => !q.get("dataset_id") || run.dataset_id === q.get("dataset_id")) });
      if (method === "POST") {
        const run = structuredClone(data.runs[0]);
        Object.assign(run, { id: id("run"), dataset_id: body.dataset_id, status: "running", created_at: "2026-11-11T04:00:00Z", finished_at: null, progress: { completed: 0, total: 4 } });
        data.runs.push(run); return json(run, 201);
      }
    }
    const runMatch = /^\/knowledge\/runs\/([^/]+)(?:\/(report|cancel))?$/.exec(route);
    if (runMatch) {
      const run = data.runs.find((item) => item.id === decodeURIComponent(runMatch[1]));
      if (!run) return error("FIXTURE_RUN_NOT_FOUND", 404);
      if (runMatch[2] === "cancel" && method === "POST") { run.status = "cancelled"; return new Response(null, { status: 204 }); }
      if (method === "GET") return json(runMatch[2] === "report" ? data.report(run.id) : run);
    }
    if (route === "/knowledge/compare" && method === "GET") {
      const base = data.runs.find((run) => run.id === q.get("base")), candidate = data.runs.find((run) => run.id === q.get("candidate"));
      if (!base || !candidate) return error("FIXTURE_RUN_NOT_FOUND", 404);
      const metricValue = (runId: string, metric: typeof metricIds[number]): number | null => runId === "run-base" ? metrics[metric] : candidateMetrics[metric];
      return json({ base: { id: base.id }, candidate: { id: candidate.id }, metrics: ["expect", "judge"].flatMap((label_source) => metricIds.map((metric_id) => {
        const a = label_source === "judge" && metric_id === "recall_at_k" ? null : metricValue(base.id, metric_id);
        const b = label_source === "judge" && metric_id === "recall_at_k" ? null : metricValue(candidate.id, metric_id);
        return { metric_id, label_source, label: metricSpecs.find((spec) => spec.metric_id === metric_id)?.label, direction: metric_id === "unscored_rate" ? "lower_is_better" : "higher_is_better", base: a, candidate: b, delta: a === null || b === null ? null : b - a };
      })), regressions: base.id === candidate.id ? [] : [{ case_id: "case-expect::base", config_id: "base", metric_id: "precision_at_k", base: 0.5, candidate: 0.25, delta: -0.25, reason: "测试数据：候选配置准确率下降" }] });
    }
    return error("FIXTURE_ROUTE_NOT_IMPLEMENTED", 501);
  };
  const requestMutation = async <T>(action: string, payload: Payload = {}): Promise<T> => {
    audit.push({ kind: "mutation", method: action, path: "fixture-memory" });
    await wait();
    let result: unknown;
    const kb = data.kbs.find((item) => item.id === payload.id);
    const doc = data.docs.find((item) => item.id === payload.id);
    const removeDoc = (docId: string) => { data.docs = data.docs.filter((item) => item.id !== docId); data.chunks = data.chunks.filter((item) => item.docId !== docId); };
    switch (action) {
      case "knowledge.kb.create": {
        const kbId = id("kb"); data.kbs.push({ id: kbId, name: String(payload.name), description: String(payload.description ?? ""), workspaceId: "fixture-workspace", visibility: String(payload.visibility ?? "workspace"), isEnabled: true, documentCount: 0 }); result = { id: kbId }; break;
      }
      case "knowledge.kb.update":
        if (!kb) throw new Error("FIXTURE_KB_NOT_FOUND");
        for (const key of ["name", "description"] as const) if (typeof payload[key] === "string") kb[key] = payload[key] as string;
        if (typeof payload.isEnabled === "boolean") kb.isEnabled = payload.isEnabled;
        result = { ok: true, id: kb.id }; break;
      case "knowledge.kb.delete":
        if (!kb) throw new Error("FIXTURE_KB_NOT_FOUND");
        data.docs.filter((item) => item.kbId === kb.id).forEach((item) => removeDoc(item.id));
        data.kbs.splice(data.kbs.indexOf(kb), 1); result = { ok: true, id: kb.id }; break;
      case "knowledge.doc.delete":
        if (!doc) throw new Error("FIXTURE_DOC_NOT_FOUND");
        removeDoc(doc.id); result = { ok: true, id: doc.id }; break;
      case "knowledge.doc.add":
      case "knowledge.doc.add_file": {
        // Only use submitted names; never read a server path or parse uploaded bytes.
        const newDoc = { id: id("doc"), kbId: String(payload.kbId), title: String(payload.title ?? payload.name ?? "测试路径导入"), originalName: String(payload.name ?? "fixture.txt"), status: "processing", chunkCount: 0, charCount: 0 };
        data.docs.push(newDoc); result = { document: newDoc }; break;
      }
      case "knowledge.doc.reindex":
        if (!doc) throw new Error("FIXTURE_DOC_NOT_FOUND");
        doc.status = "processing"; doc.error = undefined; result = { ok: true, queued: true }; break;
      case "knowledge.kb.reindex": {
        if (!kb) throw new Error("FIXTURE_KB_NOT_FOUND");
        const docs = data.docs.filter((item) => item.kbId === kb.id); docs.forEach((item) => { item.status = "processing"; }); result = { ok: true, queued: docs.length, documentIds: docs.map((item) => item.id) }; break;
      }
      case "knowledge.repair": result = { ok: true, repaired: 0, fixture: true }; break;
      case "knowledge.graph.extract":
      case "knowledge.graph.rebuild": result = { ok: true, queued: 1, fixture: true }; break;
      default: throw new Error(`FIXTURE_MUTATION_BLOCKED: ${action}`);
    }
    syncCounts(); return structuredClone(result) as T;
  };
  return { data, audit, controls, fetch: fetchMock, client: { requestMutation } as unknown as ZhixuClient };
}

export function installFixtureTransport() {
  const fixture = createFixtureTransport();
  window.fetch = fixture.fetch;
  // Close alternative application transports. Vite's already-created HMR socket is untouched.
  window.XMLHttpRequest = class { constructor() { throw new Error("FIXTURE_XHR_BLOCKED"); } } as unknown as typeof XMLHttpRequest;
  window.WebSocket = class { constructor() { throw new Error("FIXTURE_WEBSOCKET_BLOCKED"); } } as unknown as typeof WebSocket;
  window.EventSource = class { constructor() { throw new Error("FIXTURE_EVENTSOURCE_BLOCKED"); } } as unknown as typeof EventSource;
  navigator.sendBeacon = () => false;
  Object.defineProperty(window, "__knowledgeFixture", { value: fixture, configurable: true });
  return fixture;
}
