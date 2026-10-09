# Deploying Aether

One free Oracle Cloud VM runs everything: Caddy (TLS, body limit, security
headers) in front of the API on `127.0.0.1:8000`, Neo4j and Ollama on
localhost. Chat uses Ollama's cloud model; embeddings run on the VM. The
address is `https://<ip-with-dashes>.sslip.io`, so no domain is needed.

## 1. Create the VM

In Oracle Cloud (Always Free):

- Compute → Instance, image **Ubuntu 24.04**, shape **VM.Standard.A1.Flex**
  with 2 OCPU and 12 GB (the Always Free ceiling since 2026-06-15).
- Add your SSH public key. Note the public IP.
- Networking → the instance's subnet → Security List → add ingress rules for
  TCP **80** and **443** from `0.0.0.0/0`.

If the region reports "out of capacity", retry later.

## 2. Copy the code and run setup

The repository is private, so copy it from your machine:

```sh
rsync -a --exclude .git --exclude .venv --exclude .local --exclude .env \
  ./ ubuntu@<ip>:aether/
ssh ubuntu@<ip> sudo bash aether/deploy/setup.sh <ip>
```

`setup.sh` installs Neo4j, Caddy, Ollama and the API, generates the database
password into `/etc/aether/aether.env` (root and the `aether` group only), opens
ports 80/443 in the VM's iptables, and starts everything.

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

Re-run step 2. Data, the database password and the Ollama sign-in are kept.

## Exposure

Anyone can read documents, the graph and claims, and ask `/query` (10 per
minute per IP); only keys an admin creates can write. Never upload private
documents. Request bodies over 2 MB are rejected by Caddy.

Not set up yet: automated backups (Community edition dumps only while Neo4j is
stopped; see [LOCAL_DATABASE.md](LOCAL_DATABASE.md)).
