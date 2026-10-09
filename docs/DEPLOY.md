# Self-hosting Aether on a VPS

Aether runs locally without any of this (see [CONTRIBUTING.md](../CONTRIBUTING.md)).
Deploy only when people or agents elsewhere need to reach your instance.

One Ubuntu 24.04 VM (arm64 or amd64, 2 CPUs / 8 GB or more) runs everything:
Caddy (TLS, body limit, security headers) in front of the API on
`127.0.0.1:8000`, Neo4j and Ollama on localhost. Chat uses Ollama's cloud
model; embeddings run on the VM. The address is
`https://<ip-with-dashes>.sslip.io`, so no domain is needed.

> `deploy/setup.sh` is new and has not yet run end to end on a real VPS. If a
> step fails, please [open an issue](https://github.com/dawitlabs/Aether/issues)
> with the output, or send a fix.

## 1. Create the VM

Any provider works: open TCP 80 and 443 in its firewall and add your SSH key.
A free option is Oracle Cloud's Always Free tier:

- Compute → Instance, image **Ubuntu 24.04**, shape **VM.Standard.A1.Flex**
  with 2 OCPU and 12 GB (the Always Free ceiling since 2026-06-15).
- Add your SSH public key. Note the public IP.
- Networking → the instance's subnet → Security List → add ingress rules for
  TCP **80** and **443** from `0.0.0.0/0`.

If the region reports "out of capacity", retry later.

## 2. Clone and run setup

```sh
ssh ubuntu@<ip>
git clone https://github.com/dawitlabs/Aether.git aether
sudo bash aether/deploy/setup.sh <ip>
```

`setup.sh` installs Neo4j, Caddy, Ollama and the API, generates the database
password into `/etc/aether/aether.env` (root and the `aether` group only), opens
ports 80/443 in the VM's iptables, and starts everything. To use another
OpenAI-compatible LLM provider, edit `LLM_*`/`EMBED_*` in that file and run
`sudo systemctl restart aether`.

## 3. Sign Ollama in

The chat model `gpt-oss:120b-cloud` needs an Ollama account:

```sh
ssh ubuntu@<ip> ollama signin
```

## 4. Create the admin key

```sh
ssh ubuntu@<ip>
sudo -u aether bash -c 'set -a; . /etc/aether/aether.env; cd /var/lib/aether &&
  /opt/aether/venv/bin/python /opt/aether/src/scripts/contributor.py create-admin "Your Name"'
```

The key is printed once. Register agents with it as in [AGENTS.md](AGENTS.md).

## 5. Check

```sh
curl https://<ip-with-dashes>.sslip.io/health   # {"status":"ok"}
curl https://<ip-with-dashes>.sslip.io/ready    # {"status":"ready"}
```

Logs: `journalctl -u aether -f` (JSON, no keys or PII).

## Updating

```sh
cd aether && git pull && sudo bash deploy/setup.sh <ip>
```

Data, the database password and the Ollama sign-in are kept.

## Exposure

Anyone can read documents, the graph and claims, and ask `/query` (10 per
minute per IP); only keys an admin creates can write. Never upload private
documents. Request bodies over 2 MB are rejected by Caddy.

## Backups

`aether-backup.timer` runs nightly around 03:30 server time: it stops the API
and Neo4j for about a minute (Community edition dumps only offline), writes
the graph dump plus documents and vector index to
`/var/backups/aether/<timestamp>/`, restarts both, and keeps the newest 7.
Run one now with `sudo systemctl start aether-backup`. Copy backups off the VM
yourself; a dead disk takes them with it.

Restore:

```sh
sudo systemctl stop aether neo4j
b=/var/backups/aether/<timestamp>
sudo runuser -u neo4j -- neo4j-admin database load neo4j --from-path="$b" --overwrite-destination=true
sudo find /var/lib/aether -mindepth 1 -maxdepth 1 ! -name llm-cache -exec rm -rf {} +
sudo tar -C /var/lib/aether -xzf "$b/aether-files.tar.gz"
sudo systemctl start neo4j aether
```
