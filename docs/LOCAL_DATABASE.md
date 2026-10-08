# Local Neo4j development

The locked Nix input supplies Neo4j Community 2026.09.0 and its Java 21 runtime.
The Python driver is pinned to 6.4.0. The first Nix download includes Java and
may take several minutes.

## Setup

From the project root, enter the updated environment and install dependencies:

```bash
nix develop
source .venv/bin/activate
python -m pip install -e ".[dev]"
python scripts/local_neo4j.py init
python scripts/local_neo4j.py start
python scripts/local_neo4j.py check
```

Direnv users can reload their environment instead of opening another Nix shell.
The database does not start automatically when entering the directory.

Initialization creates a random password in `.env` with owner-only permissions
if that file does not exist. Existing `.env` files are preserved and must supply
the settings shown in `.env.example`. The initial username is `neo4j`.
Initialization is repeatable and does not reset an initialized database.

The management script reads `.env`; process environment variables take
precedence. Direnv does not export these credentials automatically. Editing the
password in `.env` does not change an existing database password.

## Daily commands

```bash
python scripts/local_neo4j.py start
python scripts/local_neo4j.py status
python scripts/local_neo4j.py check
python scripts/local_neo4j.py stop
```

`start` waits for an authenticated query to succeed. `status` checks the process;
`check` verifies a usable database connection. Start/stop affect only this
project's local Neo4j home. Leaving the shell does not stop Neo4j.

Browser: http://127.0.0.1:7474

Bolt: `bolt://127.0.0.1:7687`

Both listeners bind to loopback. Authentication stays enabled. Fleet discovery,
Fleet Manager, and usage reporting are disabled. The local runtime
uses 256 MiB initial heap, 512 MiB maximum heap, and 256 MiB page cache, plus JVM
and operating-system overhead. Stop other services using these ports before
starting this instance; the script does not manage other database installations.

## Files

- `.env`: local connection credentials; never commit.
- `.local/neo4j/data/`: persistent databases and transactions.
- `.local/neo4j/conf/`: generated local configuration.
- `.local/neo4j/logs/`: startup and runtime logs.
- `.local/neo4j/run/`: process state.

The `.local/` tree is ignored by Git. Do not delete it to fix a connection error:
it contains your data. Check logs and credentials first. Runtime upgrades need
an explicit compatibility review; do not repoint the library link against an
existing database casually.

## Runtime verification

With the local instance running:

```bash
python scripts/check_neo4j_restart.py
```

This checks that invalid credentials fail, writes a uniquely identified probe,
stops and starts the local server, retrieves the probe, and deletes only that
probe afterward. It leaves the server running. Do not run it during other local
database work. If restart fails, the probe may remain until manually removed;
the script does not delete application records.

This demonstrates database durability, not Aether model persistence. The graph
schema, repository methods, and backup/restore procedure are subsequent tasks.

## References

- [Neo4j file locations](https://neo4j.com/docs/operations-manual/current/configuration/file-locations/)
- [Initial password setup](https://neo4j.com/docs/operations-manual/current/configuration/set-initial-password/)
