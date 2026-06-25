# Migrating RunCore from Render to an AWS EC2 VM

This guide moves RunCore off Render onto a single AWS EC2 instance running the
app + **Caddy** (automatic HTTPS) via Docker Compose, with the database as
**SQLite on a persistent EBS volume**. It is intentionally simple and cheap; you
can graduate to RDS Postgres later with **zero code changes** (the app already
reads `DATABASE_URL`).

> **Cost estimate:** a `t3.small` (2 vCPU, 2 GB) + 20 GB EBS ≈ **$15–20/month**.
> Start with `t3.micro` if you only need light traffic.

---

## 0. Before you start

- A domain you control (e.g. `runcore.io`) — needed for HTTPS. You'll point an
  A record at the VM's public IP.
- An AWS account with console access.
- The repo pushed to GitHub (done — `git pull` on the VM fetches it).

---

## 1. Launch the EC2 instance

1. EC2 → **Launch instance**.
2. **AMI:** Ubuntu Server 24.04 LTS (x86_64).
3. **Type:** `t3.small` (recommended) or `t3.micro`.
4. **Key pair:** create/download one (you'll SSH with it).
5. **Network / Security group** — create one allowing inbound:
   - SSH `22` from **your IP only**
   - HTTP `80` from anywhere (`0.0.0.0/0`)
   - HTTPS `443` from anywhere (`0.0.0.0/0`)
6. **Storage:** 20 GB gp3 root volume is fine to start. (Optional: attach a
   separate EBS volume for `/data` so the DB survives instance replacement — see
   §6.)
7. Launch, then note the **public IPv4 address**.

Point your domain's **A record** at that IP (`runcore.example.com → 1.2.3.4`).

---

## 2. Connect & install Docker

```bash
ssh -i your-key.pem ubuntu@<PUBLIC_IP>

# Docker + compose plugin
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git sqlite3
sudo usermod -aG docker ubuntu
exec sudo su - ubuntu        # re-login so the docker group applies
```

---

## 3. Get the code

```bash
sudo mkdir -p /opt/runcore && sudo chown ubuntu /opt/runcore
git clone https://github.com/ptpaulinho/RunCore.git /opt/runcore
cd /opt/runcore
```

> **Security:** use a fresh deploy token or SSH deploy key — do **not** bake a
> personal access token into the remote URL on the server.

---

## 4. Configure environment

```bash
cp deploy/.env.example deploy/.env
nano deploy/.env
```

Set at least:

| Variable | Value |
|---|---|
| `RUNCORE_PUBLIC_URL` | `https://runcore.example.com` |
| `RUNCORE_DOMAIN` | `runcore.example.com` (host only) |
| `RUNCORE_SMTP_*` | your SMTP relay / SES creds (optional — leave blank to disable email) |

`deploy/.env` is gitignored and never leaves the VM.

---

## 5. Start the stack

```bash
mkdir -p /opt/runcore/data            # SQLite DB lives here
./deploy/deploy.sh
```

This builds the image and starts `app` + `caddy`. Caddy automatically obtains a
Let's Encrypt certificate for `RUNCORE_DOMAIN` (port 80/443 must be open and the
DNS A record must already point here).

Verify:

```bash
curl -s https://runcore.example.com/health    # -> {"status":"ok",...}
```

Open `https://runcore.example.com/start` in a browser.

---

## 6. Persist the database across instance replacement (optional but recommended)

The DB is at `/opt/runcore/data/cloud.db`. To make it survive terminating the
instance, put `/opt/runcore/data` on a **dedicated EBS volume**:

```bash
# After attaching a new EBS volume as /dev/xvdf in the console:
sudo mkfs -t ext4 /dev/xvdf            # ONLY the first time (destroys data)
sudo mkdir -p /opt/runcore/data
echo '/dev/xvdf /opt/runcore/data ext4 defaults,nofail 0 2' | sudo tee -a /etc/fstab
sudo mount -a
sudo chown -R ubuntu /opt/runcore/data
```

Now you can stop/replace the EC2 instance and re-attach the volume without
losing tenants or certifications.

---

## 7. Migrate existing data from Render

Render's free tier used an ephemeral `/tmp/cloud.db`, so there is usually
**nothing to migrate** (data resets on each deploy). If you had a persistent
disk:

1. Download the old `cloud.db` from Render (Shell tab: `cat`/`scp`, or a disk
   snapshot).
2. Copy it to the VM and place it at `/opt/runcore/data/cloud.db` **before**
   first start. The schema auto-migrates on boot (`init_db` adds new
   columns/tables).

---

## 8. Backups

```bash
chmod +x deploy/backup-db.sh
# Test once:
RUNCORE_DB_PATH=/opt/runcore/data/cloud.db ./deploy/backup-db.sh
# Schedule every 6h (optionally set BACKUP_S3_BUCKET in the crontab line):
( crontab -l 2>/dev/null; echo "0 */6 * * * RUNCORE_DB_PATH=/opt/runcore/data/cloud.db /opt/runcore/deploy/backup-db.sh >> /var/log/runcore-backup.log 2>&1" ) | crontab -
```

For S3 upload, attach an IAM role to the instance with `s3:PutObject` on your
bucket and set `BACKUP_S3_BUCKET=s3://your-bucket`.

---

## 9. Updating the deployment

```bash
cd /opt/runcore && ./deploy/deploy.sh     # git pull + rebuild + restart
```

---

## 10. Decommission Render

Once the VM serves traffic on your domain and you've verified
`/health`, `/start`, register/login, and a certification run:

1. Repoint DNS fully to the EC2 IP (remove any Render CNAME).
2. In Render, **suspend** the service first (keep it a few days as a fallback),
   then delete it.

---

## Bare-metal (no Docker) alternative

If you'd rather not use Docker:

```bash
cd /opt/runcore
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[server]"
sudo cp deploy/runcore.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now runcore
```

Then install Caddy natively (`sudo apt install caddy`) and use a Caddyfile that
reverse-proxies `127.0.0.1:8765`.

---

## Later: switch to managed Postgres (no code change)

Provision an RDS Postgres instance, then set in `deploy/.env`:

```
DATABASE_URL=postgresql://user:pass@your-rds-endpoint:5432/runcore
```

Restart the stack. `storage.py` detects `DATABASE_URL` and uses Postgres
automatically. Migrate rows with `pg_loader`/a one-off script if needed.

---

## Environment variables reference

| Variable | Purpose | Required |
|---|---|---|
| `RUNCORE_ENV=production` | binds to `0.0.0.0` | yes (set in compose) |
| `PORT` | app port (default 8765) | no |
| `RUNCORE_DB_PATH` | SQLite path | yes (set to `/data/cloud.db`) |
| `DATABASE_URL` | use Postgres instead of SQLite | no |
| `RUNCORE_PUBLIC_URL` | links/badges in emails & UI | recommended |
| `RUNCORE_DOMAIN` | domain Caddy gets a cert for | yes (for HTTPS) |
| `RUNCORE_SMTP_HOST/PORT/USER/PASS/FROM/TLS` | outbound email | no (no-op if unset) |
| `ADMIN_TOKEN` | protect `/cloud/tenants` admin endpoints | recommended |
