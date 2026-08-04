# Deploying to Hostinger VPS

Step-by-step for a fresh Hostinger VPS. Total time: ~20 minutes.

> **Buying the server is a separate document.** [CLIENT-VPS-SETUP.md](CLIENT-VPS-SETUP.md)
> is written for the client: creating the Hostinger account, choosing the plan and
> OS, and handing over access without a password crossing a chat window. Send them
> that one; this file starts once you can SSH in.

## 0. Create the VPS

In hPanel choose the **Ubuntu 24.04 with Docker** template (under OS → Applications).
If you picked plain Ubuntu, install Docker first:

```bash
curl -fsSL https://get.docker.com | sh
```

## 1. Basic hardening + swap

SSH in as root (`ssh root@YOUR_VPS_IP`), then:

```bash
# firewall: only SSH + web
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable

# 2 GB swap as memory insurance
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

## 2. Get the code onto the VPS

Either push this project to a private Git repo and clone it, or copy it directly
from your PC (run this **on your Windows machine**, PowerShell):

```powershell
scp -r "C:\Cursor Projects\Finance Automation KonaIce" root@YOUR_VPS_IP:/opt/konaice
```

(Delete `backend/.venv` and `frontend/node_modules` first or exclude them — they are
rebuilt inside Docker and only slow down the copy.)

## 3. Configure the environment

```bash
cd /opt/konaice
cp backend/.env.example backend/.env
nano backend/.env
```

Set at minimum:

| Key | Value |
|---|---|
| `SECRET_KEY` | long random string — generate with `openssl rand -hex 32` |
| `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` | your real login |
| `BACKEND_CORS_ORIGINS` | `https://your-domain.com` (or `http://YOUR_VPS_IP`) |
| `CRM_PROVIDER` | `konaos` |
| `KONAOS_*` | copy from your local working `backend/.env` (incl. `KONAOS_SESSION_KEY`) |
| `GPT_API_KEY` | copy from local `.env` |
| `OPENAI_PROVIDER` / `OPENAI_API_KEY` | `live` + your key |
| `PIPELINE_DRY_RUN` | `true` until you're ready for real drafts |

Also export a real Postgres password for compose:

```bash
echo 'POSTGRES_PASSWORD=<random-strong-password>' > .env
```

## 4. Launch

With a domain (point an A record at the VPS IP first — Caddy then gets HTTPS automatically):

```bash
echo 'DOMAIN=ops.your-domain.com' >> .env
docker compose -f docker-compose.prod.yml up -d --build
```

Without a domain yet (plain HTTP on the IP):

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

First build takes ~5 minutes. Then open `https://ops.your-domain.com` (or `http://YOUR_VPS_IP`).

## 5. Verify

```bash
docker compose -f docker-compose.prod.yml ps        # all services Up (healthy)
curl -s localhost/health                            # {"status":"ok",...}
docker compose -f docker-compose.prod.yml logs -f backend   # watch logs
```

Log in, check **API Explorer → KonaOS Session** shows `connected`, run the
pipeline for a date, confirm drafts appear (dry-run).

## 6. Going fully live

When the dry-run drafts look right:

```bash
sed -i 's/PIPELINE_DRY_RUN=true/PIPELINE_DRY_RUN=false/' backend/.env
docker compose -f docker-compose.prod.yml up -d backend worker beat
```

The nightly pipeline (2:00 AM New York) and the daily KonaOS session check
(1:30 AM) run automatically via Celery beat.

## Updating the app later

**Auto-deploy (the normal path).** Every push to `main` triggers GitHub Actions
(`.github/workflows/deploy.yml`), which builds the backend + frontend images and
publishes them to GHCR. Watchtower on the VPS polls every 5 minutes and
auto-pulls + restarts the changed services. **Pushing to main IS the deploy** —
nothing to run on the server. (db/redis/caddy are unlabeled and never touched.)

One-time setup for private GHCR images (already done on the prod VPS):

```bash
# PAT: github.com → Settings → Developer settings → Tokens (classic),
# scope: read:packages only
docker login ghcr.io -u muhammadmohsinkhatri     # paste the PAT as password
cd /opt/konaice && git pull
docker compose -f docker-compose.prod.yml up -d  # pulls images, adds watchtower
```

**Manual fallback** (CI down, or emergency local patch on the VPS):

```bash
cd /opt/konaice
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

Note: compose-file changes (new service, ports, volumes, env_file edits) still
need the manual `git pull` + `up -d` on the VPS — Watchtower only swaps images.

## Backups

Not automatic until you install the cron job — see **[BACKUPS.md](BACKUPS.md)**
for the full picture (three layers, what each protects against, and how to
restore). The short version, once, on the server:

```bash
chmod +x /opt/konaice/deploy/backup.sh /opt/konaice/deploy/restore.sh
( crontab -l 2>/dev/null;   echo '15 3 * * * /opt/konaice/deploy/backup.sh >> /var/log/konaice-backup.log 2>&1' ) | crontab -
/opt/konaice/deploy/backup.sh     # run once by hand and confirm a file appears
```

And put `backend/.env` in a password manager the day you go live. It is
deliberately not in the backups, and without it a restore comes back with all
the data and no way to reach KonaOS.

## Operations cheat-sheet

| Task | Command |
|---|---|
| Restart everything | `docker compose -f docker-compose.prod.yml restart` |
| Backend logs | `docker compose -f docker-compose.prod.yml logs -f backend worker` |
| DB backup, now | `/opt/konaice/deploy/backup.sh` |
| Restore a backup | `/opt/konaice/deploy/restore.sh <file.sql.gz>` — see [BACKUPS.md](BACKUPS.md) |
| New KonaOS session key | paste in dashboard (API Explorer → KonaOS Session) — no restart needed |
| Disk usage | `docker system df` · prune old images: `docker image prune -af` |

## Sizing notes

The compose file caps memory per service (total ~2.1 GB) and runs the Celery
worker at `--concurrency=2`.

**On KVM 2** (2 vCPU / 8 GB) those defaults are right: concurrency matches the
core count and there is comfortable memory headroom.

**On KVM 4** (4 vCPU / 16 GB) they are conservative — half the cores sit idle
during a run. Raising the worker to `--concurrency=4` roughly halves the wall
clock of a large backfill:

```bash
# in docker-compose.prod.yml, the `worker` service command
celery -A app.tasks.celery_app.celery worker --loglevel=info --concurrency=4
```

Left at 2 by default deliberately: the setting is shared with the existing KVM 2
box, and a nightly run of ordinary size finishes comfortably either way. Change
it when a backfill is slow enough to notice, not before.

If you ever see OOM, check `docker stats` — but at this workload you won't.
