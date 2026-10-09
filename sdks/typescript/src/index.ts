/**
 * Aether API client. No dependencies: uses the built-in fetch (Node 18+,
 * Deno, Bun, browsers). Mirrors examples/agent_client.py.
 *
 *   const aether = new AetherClient("http://127.0.0.1:8000", { apiKey: "ae_..." });
 *   const answer = await aether.query("Who discovered the neutron?", "hybrid");
 */

export type Mode = "local" | "global" | "hybrid";

export interface Citation {
  text_unit_id: string;
  document_id: string;
  quote: string;
}

export interface Answer {
  /** null when the graph has nothing to answer from. */
  answer: string | null;
  /** Each quote was verified verbatim against its passage; empty means abstained. */
  citations: Citation[];
  entity_ids: string[];
  community_ids: string[];
  claim_ids: string[];
}

export interface Entity {
  id: string;
  name: string;
  type: string;
  description: string;
  aliases: string[];
  confidence: number;
  status: string;
}

export interface TextUnit {
  id: string;
  text: string;
  source_document_id: string;
}

export interface Relationship {
  id: string;
  source_id: string;
  target_id: string;
  type: string;
  description: string;
  weight: number;
}

export interface Neighborhood {
  entity: Entity;
  neighbors: Entity[];
  relationships: Relationship[];
  text_units: TextUnit[];
}

export interface Document {
  id: string;
  filename: string | null;
  size_bytes: number;
  content_hash: string;
  text_unit_count: number;
}

export interface Claim {
  id: string;
  statement: string;
  confidence: number;
  status: string;
}

export interface ClaimOut {
  claim: Claim;
  author_id: string;
  reviews: unknown[];
}

/** A verbatim excerpt from a passage, as evidence for or against a claim. */
export interface Evidence {
  textUnitId: string;
  excerpt: string;
}

export class AetherError extends Error {
  readonly status: number;
  readonly error: string;
  readonly detail: unknown;

  constructor(status: number, error: string, detail: unknown) {
    super(`${status} ${error}: ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    this.name = "AetherError";
    this.status = status;
    this.error = error;
    this.detail = detail;
  }
}

export interface ClientOptions {
  apiKey?: string;
  /** Times to retry a 429 after its Retry-After delay. Default 3. */
  retries?: number;
  fetch?: typeof fetch;
}

const sleep = (seconds: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, seconds * 1000));

const toEvidence = (items: Evidence[]) =>
  items.map((e) => ({ text_unit_id: e.textUnitId, excerpt: e.excerpt }));

export class AetherClient {
  readonly base: string;
  private readonly apiKey: string | undefined;
  private readonly retries: number;
  private readonly fetchFn: typeof fetch;

  constructor(baseUrl: string, options: ClientOptions = {}) {
    this.base = `${baseUrl.replace(/\/+$/, "")}/api/v0`;
    this.apiKey = options.apiKey;
    this.retries = options.retries ?? 3;
    this.fetchFn = options.fetch ?? fetch;
  }

  async request<T>(method: string, path: string, body?: unknown,
                   contentType = "application/json"): Promise<T> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    let data: string | undefined;
    if (body !== undefined) {
      headers["Content-Type"] = contentType;
      data = typeof body === "string" ? body : JSON.stringify(body);
    }
    for (let attempt = 0; ; attempt++) {
      const response = await this.fetchFn(this.base + path, { method, headers, body: data });
      const retryAfter = Number(response.headers.get("retry-after"));
      if (response.status === 429 && attempt < this.retries && retryAfter > 0) {
        await sleep(retryAfter);
        continue;
      }
      const raw = await response.text();
      const payload: unknown = raw ? JSON.parse(raw) : null;
      if (!response.ok) {
        const err = (payload ?? {}) as { error?: string; detail?: unknown };
        throw new AetherError(response.status, err.error ?? "error", err.detail);
      }
      return payload as T;
    }
  }

  query(question: string, mode: Mode = "local"): Promise<Answer> {
    return this.request("POST", "/query", { question, mode });
  }

  searchEntities(name: string): Promise<Entity[]> {
    return this.request("GET", `/entities?name=${encodeURIComponent(name)}`);
  }

  neighborhood(entityId: string): Promise<Neighborhood> {
    return this.request("GET", `/entities/${encodeURIComponent(entityId)}/neighborhood`);
  }

  /** Needs a key with `propose`. Re-uploading the same bytes returns the original. */
  upload(text: string, filename?: string): Promise<Document> {
    const path = filename ? `/documents?filename=${encodeURIComponent(filename)}` : "/documents";
    return this.request("POST", path, text, "text/plain; charset=utf-8");
  }

  /** Needs a key with `propose`. Affects answers only after a human accepts it. */
  proposeClaim(statement: string, evidence: Evidence[],
               options: { subjectId?: string; confidence?: number } = {}): Promise<ClaimOut> {
    return this.request("POST", "/claims", {
      statement,
      subject_id: options.subjectId ?? null,
      confidence: options.confidence ?? 0.8,
      evidence: toEvidence(evidence),
    });
  }

  claim(claimId: string): Promise<ClaimOut> {
    return this.request("GET", `/claims/${encodeURIComponent(claimId)}`);
  }

  dispute(claimId: string, reason: string, counterEvidence: Evidence[]): Promise<ClaimOut> {
    return this.request("POST", `/claims/${encodeURIComponent(claimId)}/dispute`, {
      reason,
      counter_evidence: toEvidence(counterEvidence),
    });
  }

  me(): Promise<unknown> {
    return this.request("GET", "/contributors/me");
  }
}
