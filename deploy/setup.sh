#!/usr/bin/env bash
# Install or update a self-hosted Aether on a fresh Ubuntu 24.04 VM (arm64 or amd64).
#
#   sudo bash deploy/setup.sh <public-ip>
#
# Run from a clone of the repository on the VM (see docs/DEPLOY.md). Safe to
# re-run: it updates the code and restarts the API, keeping data and secrets.
set -euo pipefail

IP=${1:?usage: setup.sh <public-ip>}
HOST="${IP//./-}.sslip.io"
SRC=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=/etc/aether/aether.env

[[ $EUID -eq 0 ]] || { echo "Run as root (sudo)." >&2; exit 1; }

echo "== packages"
install -d -m 0755 /etc/apt/keyrings
if [[ ! -f /etc/apt/sources.list.d/neo4j.list ]]; then
  curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/neo4j.gpg
  echo "deb [signed-by=/etc/apt/keyrings/neo4j.gpg] https://debian.neo4j.com stable latest" \
    > /etc/apt/sources.list.d/neo4j.list
fi
if [[ ! -f /etc/apt/sources.list.d/caddy-stable.list ]]; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --dearmor -o /etc/apt/keyrings/caddy.gpg
  echo "deb [signed-by=/etc/apt/keyrings/caddy.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
    > /etc/apt/sources.list.d/caddy-stable.list
fi
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -yq \
  python3.12-venv openjdk-21-jre-headless neo4j caddy rsync netfilter-persistent
command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh

echo "== secrets"
install -d -m 0750 /etc/aether
id aether >/dev/null 2>&1 || useradd --system --home /var/lib/aether --shell /usr/sbin/nologin aether
if [[ ! -f $ENV_FILE ]]; then
  password=$(openssl rand -base64 33 | tr -d '/+=')
  umask 077
  cat > "$ENV_FILE" <<ENV
NEO4J_URI=bolt://127.0.0.1:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=$password
NEO4J_DATABASE=neo4j
DOCUMENTS_DIR=/var/lib/aether/documents
INDEX_DIR=/var/lib/aether/lancedb
LLM_CACHE_DIR=/var/lib/aether/llm-cache
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=gpt-oss:120b-cloud
EMBED_BASE_URL=http://127.0.0.1:11434/v1
EMBED_MODEL=all-minilm
ENV
  systemctl stop neo4j || true
  neo4j-admin dbms set-initial-password "$password"
  chown -R neo4j:neo4j /var/lib/neo4j
fi
chgrp aether "$ENV_FILE" && chmod 0640 "$ENV_FILE"

echo "== neo4j"
conf=/etc/neo4j/neo4j.conf
set_conf() { sed -i "/^#\?$1=/d" "$conf"; echo "$1=$2" >> "$conf"; }
set_conf server.default_listen_address 127.0.0.1
set_conf server.http.enabled false
set_conf server.memory.heap.initial_size 1g
set_conf server.memory.heap.max_size 1g
set_conf server.memory.pagecache.size 1g
set_conf dbms.usage_report.enabled false
systemctl enable neo4j
systemctl restart neo4j

echo "== ollama"
systemctl enable --now ollama
ollama pull all-minilm

echo "== app"
install -d -o aether -g aether -m 0750 /var/lib/aether
rsync -a --delete --exclude .git --exclude .venv --exclude .local --exclude .env \
  "$SRC"/ /opt/aether/src/
[[ -d /opt/aether/venv ]] || python3.12 -m venv /opt/aether/venv
/opt/aether/venv/bin/pip install -q --upgrade /opt/aether/src
for unit in aether.service aether-backup.service aether-backup.timer; do
  install -m 0644 "/opt/aether/src/deploy/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable aether
systemctl enable --now aether-backup.timer
systemctl restart aether

echo "== caddy"
sed "s/AETHER_HOST/$HOST/" /opt/aether/src/deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl reload-or-restart caddy

echo "== firewall"
# Oracle's Ubuntu images reject everything but SSH in iptables.
for port in 80 443; do
  rule=(INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT)
  iptables -C "${rule[@]}" 2>/dev/null || iptables -I "${rule[@]}"
done
netfilter-persistent save

echo
echo "Aether: https://$HOST  (health: https://$HOST/health)"
echo "Next: docs/DEPLOY.md steps 4-5 (ollama signin, create the admin key)."
