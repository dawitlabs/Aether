import assert from "node:assert/strict";
import { test } from "node:test";
import { AetherClient, AetherError } from "../src/index.ts";

type Sent = { url: string; method: string; headers: Record<string, string>; body?: string };

function fakeFetch(responses: Array<[number, unknown, Record<string, string>?]>) {
  const sent: Sent[] = [];
  const fn = (async (url: string, init: RequestInit) => {
    sent.push({ url, method: init.method ?? "GET",
                headers: init.headers as Record<string, string>, body: init.body as string });
    const [status, body, headers] = responses.shift()!; // test supplies one per request
    return new Response(body === null ? null : JSON.stringify(body), { status, headers });
  }) as typeof fetch;
  return { fn, sent };
}

test("query posts JSON with the bearer key under /api/v0", async () => {
  const answer = { answer: "Chadwick.", citations: [], entity_ids: [], community_ids: [], claim_ids: [] };
  const { fn, sent } = fakeFetch([[200, answer]]);
  const aether = new AetherClient("http://aether/", { apiKey: "ae_x", fetch: fn });

  assert.deepEqual(await aether.query("Who found the neutron?", "hybrid"), answer);
  assert.equal(sent[0].url, "http://aether/api/v0/query");
  assert.equal(sent[0].headers.Authorization, "Bearer ae_x");
  assert.deepEqual(JSON.parse(sent[0].body!), { question: "Who found the neutron?", mode: "hybrid" });
});

test("upload sends plain text and encodes the filename", async () => {
  const { fn, sent } = fakeFetch([[201, { id: "d1" }]]);
  await new AetherClient("http://aether", { fetch: fn }).upload("Radium glows.", "a b.txt");
  assert.equal(sent[0].url, "http://aether/api/v0/documents?filename=a%20b.txt");
  assert.equal(sent[0].headers["Content-Type"], "text/plain; charset=utf-8");
  assert.equal(sent[0].body, "Radium glows.");
  assert.equal(sent[0].headers.Authorization, undefined);
});

test("claim evidence is sent in the API's snake_case shape", async () => {
  const { fn, sent } = fakeFetch([[201, { claim: {}, author_id: "a", reviews: [] }]]);
  await new AetherClient("http://aether", { fetch: fn })
    .proposeClaim("Radium glows.", [{ textUnitId: "u1", excerpt: "radium glows" }]);
  assert.deepEqual(JSON.parse(sent[0].body!), {
    statement: "Radium glows.", subject_id: null, confidence: 0.8,
    evidence: [{ text_unit_id: "u1", excerpt: "radium glows" }],
  });
});

test("429 is retried after Retry-After; other errors throw AetherError", async () => {
  const { fn, sent } = fakeFetch([
    [429, { error: "rate_limited" }, { "retry-after": "0.01" }],
    [403, { error: "forbidden", detail: "needs propose" }],
  ]);
  const aether = new AetherClient("http://aether", { fetch: fn });
  await assert.rejects(aether.searchEntities("curie"), (e: unknown) =>
    e instanceof AetherError && e.status === 403 && e.detail === "needs propose");
  assert.equal(sent.length, 2);
});
