# Contributing to Aether

Aether is in its foundation phase.

Read the [current architecture](docs/ARCHITECTURE.md) and
[foundation roadmap](docs/ROADMAP.md) before changing core components.
Neo4j replaces the supplied archives' Kuzu MVP choice; see
[ADR-0001](docs/adr/0001-use-neo4j.md). Follow the
[local database guide](docs/LOCAL_DATABASE.md) to initialize and run Neo4j.

## Development setup

Install Nix with flakes enabled, then enter the project directory.
The current development shell targets `x86_64-linux`.

```bash
nix develop
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

For later terminal sessions, reuse the environment:

```bash
nix develop
source .venv/bin/activate
```

## Automatic environment with direnv

If direnv is installed and hooked into your shell, run this from the project root:

```bash
direnv allow
```

The project's `.envrc` loads the Nix development shell and activates `.venv`,
creating the virtual environment if it does not exist. It does not install Python
dependencies; for a fresh environment, run `python -m pip install -e ".[dev]"`.
After setup, entering the directory loads the environment automatically and
leaving it restores your previous environment. You do not need to run
`nix develop` or manually activate `.venv` when using direnv.

## Running tests

```bash
python -m pytest -q
```

## Making changes

- Keep changes focused on one task.
- Preserve source provenance when working with knowledge models.
- Add or update tests when changing behavior.
- Run the tests before submitting a change.
- Explain what changed, why, and how you tested it.

## Generated files

Do not commit virtual environments, caches, or secrets.
Keep both `flake.nix` and `flake.lock` in version control.
