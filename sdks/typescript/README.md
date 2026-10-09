# Aether TypeScript client

Client for the [Aether](https://github.com/dawitlabs/Aether) API. No
dependencies: uses the built-in `fetch` (Node 18+, Deno, Bun, browsers).

No package to install: copy `src/index.ts` into your project, or import it
from your clone of the repository.

```ts
import { AetherClient } from "./aether/index.ts";

const aether = new AetherClient("http://127.0.0.1:8000", { apiKey: "ae_..." });

const answer = await aether.query("Who discovered the neutron?", "hybrid");
console.log(answer.answer, answer.citations); // no citations = Aether abstained

await aether.proposeClaim("James Chadwick discovered the neutron in 1932.", [
  { textUnitId: answer.citations[0].text_unit_id, excerpt: answer.citations[0].quote },
]);
```

Methods: `query`, `searchEntities`, `neighborhood`, `upload`, `proposeClaim`,
`claim`, `dispute`, `me`. Non-2xx responses throw `AetherError` (`status`,
`error`, `detail`); 429s are retried after `Retry-After` up to `retries` times.

Querying needs no key; uploads and claims need one with `propose`
([AGENTS.md](../../docs/AGENTS.md)).

Develop: `pnpm install && pnpm test && pnpm build`.
