"""Manage Aether's local Neo4j instance from the Nix development shell."""

import argparse
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
from datetime import datetime, timezone

from dotenv import dotenv_values
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable, SessionExpired


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "neo4j"
ENV_FILE = ROOT / ".env"
BACKUPS = ROOT / ".local" / "backups"


def settings():
    values = {**dotenv_values(ENV_FILE), **os.environ}
    required = ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD", "NEO4J_DATABASE")
    if any(not values.get(key) for key in required):
        raise RuntimeError("Missing database settings. Run init or configure .env.")
    if values["NEO4J_URI"] != "bolt://127.0.0.1:7687":
        raise RuntimeError("This local runtime tool requires bolt://127.0.0.1:7687.")
    return values


def command(tool, *args, private=False):
    executable = shutil.which(tool)
    if executable is None:
        raise RuntimeError("Neo4j tools are missing. Enter nix develop first.")
    env = {**os.environ, "NEO4J_HOME": str(LOCAL), "NEO4J_CONF": str(LOCAL / "conf")}
    result = subprocess.run(
        [executable, *args], env=env, capture_output=private, text=True
    )
    if result.returncode:
        # Never include the password-bearing argument list in an exception.
        raise RuntimeError(f"{tool} failed (exit {result.returncode}); inspect local logs.")


def initialize():
    distribution = os.environ.get("AETHER_NEO4J_DIST")
    if not distribution:
        raise RuntimeError("Enter the updated Nix shell before initialization.")
    if (LOCAL / ".initialized").exists():
        print("Already initialized; existing data and credentials preserved.")
        return
    if (LOCAL / "data" / "databases").exists():
        raise RuntimeError("Existing database found; refusing to reinitialize credentials.")
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("conf", "data", "logs", "run", "plugins", "import"):
        (LOCAL / name).mkdir(exist_ok=True)
    lib = LOCAL / "lib"
    if not lib.exists():
        lib.symlink_to(Path(distribution) / "lib", target_is_directory=True)
    for name in ("server-logs.xml", "user-logs.xml"):
        source = Path(distribution) / "conf" / name
        if source.exists():
            shutil.copyfile(source, LOCAL / "conf" / name)
    # Keep the JVM options recommended by this exact pinned server release.
    packaged_config = (Path(distribution) / "conf" / "neo4j.conf").read_text()
    jvm_options = "\n".join(
        line for line in packaged_config.splitlines()
        if line.startswith("server.jvm.additional=")
    )
    (LOCAL / "conf" / "neo4j.conf").write_text(
        "server.default_listen_address=127.0.0.1\n"
        "server.default_advertised_address=127.0.0.1\n"
        "server.directories.import=import\n"
        "server.bolt.enabled=true\n"
        "server.bolt.listen_address=127.0.0.1:7687\n"
        "server.http.enabled=true\n"
        "server.http.listen_address=127.0.0.1:7474\n"
        "server.https.enabled=false\n"
        "dbms.security.auth_enabled=true\n"
        "dbms.usage_report.enabled=false\n"
        "dbms.fleet_manager.enabled=false\n"
        "server.fleet_discovery.enabled=false\n"
        "server.memory.heap.initial_size=256m\n"
        "server.memory.heap.max_size=512m\n"
        "server.memory.pagecache.size=256m\n"
        + jvm_options + "\n"
    )
    if not ENV_FILE.exists():
        descriptor = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(
                "NEO4J_URI=bolt://127.0.0.1:7687\n"
                "NEO4J_USERNAME=neo4j\n"
                f"NEO4J_PASSWORD={secrets.token_urlsafe(32)}\n"
                "NEO4J_DATABASE=neo4j\n"
            )
    config = settings()
    if config["NEO4J_USERNAME"] != "neo4j":
        raise RuntimeError("Initial local username must be neo4j.")
    command("neo4j-admin", "dbms", "set-initial-password", config["NEO4J_PASSWORD"], private=True)
    (LOCAL / ".initialized").touch()
    print("Initialized local Neo4j. Credentials are in ignored .env.")


def check(wait=False):
    config = settings()
    deadline = time.monotonic() + (60 if wait else 0)
    with GraphDatabase.driver(
        config["NEO4J_URI"],
        auth=(config["NEO4J_USERNAME"], config["NEO4J_PASSWORD"]),
        connection_timeout=2,
        max_transaction_retry_time=0,
    ) as driver:
        while True:
            try:
                driver.verify_connectivity()
                records, _, _ = driver.execute_query(
                    "RETURN 1 AS ready", database_=config["NEO4J_DATABASE"]
                )
                assert records[0]["ready"] == 1
                print("Neo4j ready: authenticated query succeeded.")
                return
            except AuthError:
                raise RuntimeError("Neo4j authentication failed; check local credentials.") from None
            except (ServiceUnavailable, SessionExpired):
                if time.monotonic() >= deadline:
                    raise RuntimeError("Neo4j is unavailable; inspect .local/neo4j/logs.") from None
                time.sleep(1)


def require_stopped():
    try:
        command("neo4j", "status", private=True)
    except RuntimeError:
        return
    raise RuntimeError("Stop Neo4j first; Community edition dumps and loads offline.")


def backup():
    """Dump the application database to a new timestamped directory."""
    require_stopped()
    target = BACKUPS / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target.mkdir(parents=True, mode=0o700)
    command("neo4j-admin", "database", "dump", settings()["NEO4J_DATABASE"], f"--to-path={target}")
    print(f"Backup written to {target.relative_to(ROOT)}")
    return target


def restore(source):
    """Replace the application database with a dump, keeping a backup of the current data."""
    source = Path(source).resolve()
    database = settings()["NEO4J_DATABASE"]
    if not (source / f"{database}.dump").is_file():
        raise RuntimeError(f"No {database}.dump in {source}")
    print("Backing up current data before restore.")
    backup()
    command(
        "neo4j-admin", "database", "load", database,
        f"--from-path={source}", "--overwrite-destination=true",
    )
    print(f"Restored {database} from {source}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("init", "start", "stop", "status", "check", "backup", "restore")
    )
    parser.add_argument("path", nargs="?", help="backup directory for restore")
    arguments = parser.parse_args()
    action = arguments.action
    if action == "restore" and arguments.path is None:
        parser.error("restore requires a backup directory")
    try:
        if action == "init":
            initialize()
        elif action == "backup":
            backup()
        elif action == "restore":
            restore(arguments.path)
        elif action == "check":
            check()
        else:
            if not (LOCAL / ".initialized").exists():
                raise RuntimeError("Run init first.")
            command("neo4j", action)
            if action == "start":
                check(wait=True)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
