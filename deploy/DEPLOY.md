# Deploying to Hostinger VPS (KVM 2)

Step-by-step for a fresh Hostinger KVM 2 (2 vCPU / 8 GB RAM). Total time: ~20 minutes.

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

```bash
cd /opt/konaice
git pull                          # or re-scp the changed files
docker compose -f docker-compose.prod.yml up -d --build
```

## Operations cheat-sheet

| Task | Command |
|---|---|
| Restart everything | `docker compose -f docker-compose.prod.yml restart` |
| Backend logs | `docker compose -f docker-compose.prod.yml logs -f backend worker` |
| DB backup | `docker compose -f docker-compose.prod.yml exec db pg_dump -U konaice konaice > backup_$(date +%F).sql` |
| New KonaOS session key | paste in dashboard (API Explorer → KonaOS Session) — no restart needed |
| Disk usage | `docker system df` · prune old images: `docker image prune -af` |

## Sizing notes (KVM 2: 2 vCPU / 8 GB)

The compose file caps memory per service (total ~2.1 GB) — comfortable headroom.
Celery worker runs `--concurrency=2` to match the 2 vCPUs. If you ever see OOM,
check `docker stats` — but at this workload you won't.
