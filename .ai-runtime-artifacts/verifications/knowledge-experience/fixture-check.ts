// Standalone transport checks; deliberately does not exercise unfinished page interactions.
import assert from "node:assert/strict";
import { createFixtureTransport } from "./fixture-transport";
import { FIXTURE_TOKEN, FIXTURE_CSRF } from "./fixture-data";

async function main() {
  const fixture = createFixtureTransport();
  const knowledge = "http://127.0.0.1:5186/api/settings/knowledge";
  const evaluation = "http://127.0.0.1:8790/api/eval/v1";
  const auth = { Authorization: `Bearer ${FIXTURE_TOKEN}` };
  const csrf = { "X-CSRF-Token": FIXTURE_CSRF };
  const get = async (url: string, headers = auth) => (await fixture.fetch(url, { headers })).json();
  // Arrange / Act / Assert: real wire responses, null evidence and grouped comparisons.
  assert.equal((await get(`${knowledge}/kbs`)).kbs.length, 3);
  assert.equal((await get(`${knowledge}/docs?kbId=kb-product&offset=1&limit=1`)).documents[0].status, "failed");
  assert.equal((await get(`${knowledge}/doc?id=doc-ready`)).chunks.length, 2);
  assert.equal((await get(`${knowledge}/search?query=回滚&kbId=kb-product`)).hits[0].chunkId, "chunk-rollback");
  assert.equal((await get(`${knowledge}/search?query=无匹配`)).hits.length, 0);
  assert.equal((await get(`${knowledge}/graph/entities?kbId=kb-product`)).entities.length, 3);
  assert.equal((await get(`${evaluation}/auth/session`, {} as typeof auth)).csrf_token, FIXTURE_CSRF);
  assert.equal((await fixture.fetch(`${evaluation}/knowledge/runs`)).status, 403);
  const evalGet = (path: string) => get(`${evaluation}${path}`, csrf as unknown as typeof auth);
  assert.equal((await evalGet("/knowledge/runs")).runs.length, 2);
  assert.equal((await evalGet("/knowledge/datasets")).datasets.length, 2);
  const report = await evalGet("/knowledge/runs/run-candidate/report");
  assert.equal(report.metrics.groups.length, 2);
  assert.equal(report.results[2].metrics.hit_at_k, null);
  assert.equal(report.results[2].hits[0].grade, null);
  assert.equal(report.results[0].label_source, "expect");
  assert.equal(report.results[1].label_source, "judge");
  const comparison = await evalGet("/knowledge/compare?base=run-base&candidate=run-candidate");
  assert.equal(comparison.metrics.length, 14);
  assert.equal(comparison.metrics.find((item: { metric_id: string }) => item.metric_id === "recall_at_k").delta, null);
  assert.equal(comparison.regressions.length, 1);
  // Mutations update only this instance, no file/credential/model service exists here.
  await fixture.client.requestMutation("knowledge.kb.create", { name: "新建测试库" });
  assert.equal((await get(`${knowledge}/kbs`)).kbs.length, 4);
  assert.equal(createFixtureTransport().data.kbs.length, 3);
  await fixture.client.requestMutation("knowledge.doc.reindex", { id: "doc-failed" });
  assert.equal(fixture.data.docs.find((doc) => doc.id === "doc-failed")?.status, "processing");
  const response = await fixture.fetch(`${evaluation}/knowledge/datasets/dataset-main`, { method: "PATCH", headers: csrf, body: JSON.stringify({ base_revision: "wrong", name: "冲突" }) });
  assert.equal(response.status, 409);
  assert.equal(fixture.data.datasets[0].name, "部署检索验收题集");
  fixture.controls.failNext = "/kbs";
  assert.equal((await fixture.fetch(`${knowledge}/kbs`, { headers: auth })).status, 503);
  assert.equal((await fixture.fetch(`${knowledge}/kbs`, { headers: auth })).status, 200);
  await assert.rejects(fixture.fetch("https://example.invalid/api"), /FIXTURE_OUTBOUND_BLOCKED/);
  await assert.rejects(fixture.fetch("http://127.0.0.1:8765/api/settings/knowledge/kbs"), /FIXTURE_OUTBOUND_BLOCKED/);
  await assert.rejects(fixture.client.requestMutation("paid.model.call"), /FIXTURE_MUTATION_BLOCKED/);
  console.log("PASS: fixture wire shapes, grouped/null evidence, local mutations, CAS, injected failure and outbound rejection");
}
void main();
