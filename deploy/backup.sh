#!/usr/bin/env bash
# Nightly backup, run by aether-backup.timer. Neo4j Community dumps only while
# stopped, so the API and database are down for the minute or so this takes.
# The graph and the vector index are captured together so they stay in step.
set -euo pipefail

KEEP=7
ROOT=/var/backups/aether
dest="$ROOT/$(date -u +%Y%m%dT%H%M%SZ)"

install -d -m 0700 "$ROOT"
install -d -m 0700 -o neo4j -g neo4j "$dest"
trap 'systemctl start neo4j aether' EXIT
systemctl stop aether neo4j
runuser -u neo4j -- neo4j-admin database dump neo4j --to-path="$dest"
tar -C /var/lib/aether --exclude=./llm-cache -czf "$dest/aether-files.tar.gz" .

find "$ROOT" -mindepth 1 -maxdepth 1 -type d | sort | head -n -"$KEEP" | xargs -r rm -rf
echo "backup written to $dest"
