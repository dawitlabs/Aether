# ADR-0001: Use Neo4j for graph persistence

Status: Accepted; implementation pending

Date: 2026-10-08

## Context

The supplied Aether documents selected embedded Kuzu for the MVP and listed
Neo4j as an alternative. Kuzu's official repository was archived on October 10,
2025. Its existing releases remain usable, but choosing it now would make an
archived upstream project a new core dependency.

Aether currently has Python models and tests, but no database implementation or
persisted application data. This is a change of plan, not a data migration.

## Decision

Use a local Neo4j Community instance as the foundation graph database, accessed
through the official Python driver. Keep Nix for the development toolchain,
Python/Pydantic for domain models, FastAPI for the planned API, and LanceDB for
the planned embedding index.

Build one modular Python application. Neo4j runs as a separate process with
persistent local storage. Do not split the application into network services
during the foundation phase.

Validate a compatible Neo4j server, Java runtime, and Python driver combination
in the next implementation step, then pin the chosen versions. Prefer a local
Nix-managed runtime consistent with the supplied Nix-first development policy.
At decision time, the flake did not yet provide or start Neo4j.

## Alternatives

- Keep Kuzu: simplest embedded development experience and closest to the old
  roadmap, but accepts an archived core dependency.
- Use Neo4j: already contemplated by the architecture and has an official Python
  driver; adds a database process, credentials, resource usage, and operational
  work. Selected for ongoing development.

## Consequences

- Database setup, readiness, credentials, backups, and restore checks become
  explicit tasks. Entering a Nix shell alone does not start the database.
- Persistence is isolated behind a repository module; models remain independent
  of the database driver.
- Real database integration tests must prove transactions and durable reads.
- The planned LanceDB index is derived data. Neo4j and LanceDB writes are not
  assumed to share a transaction; index failures must be recoverable.
- No automatic Kuzu-to-Neo4j migration is promised or needed at this stage.

## References

Implementation update (2026-10-08): the local runtime is now implemented and
verified with a restart durability check; model persistence is still pending.
See the [runtime guide](../LOCAL_DATABASE.md) for versions and commands.

- [Kuzu archival announcement](https://github.com/kuzudb/kuzu)
- [Official Neo4j Python driver](https://neo4j.com/docs/python-manual/current/)
- [Current architecture](../ARCHITECTURE.md)
- [Foundation roadmap](../ROADMAP.md)

This decision supersedes the Kuzu MVP choice in the supplied system archive's
`03-ARCHITECTURE.md`, `06-TECH-STACK.md`, `07-ROADMAP.md`, and `08-AGENT-GUIDE.md`,
and the Kuzu starting-stack recommendation in the GraphRAG archive.
