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

Those rules cover the domain layout (`WEB_PORT=80` / `TLS_PORT=443`). On the
8081 layout, add it for consistency:

```bash
ufw allow 8081/tcp
```

**But do not rely on ufw to close a Docker port.** Docker publishes ports with
DNAT rules in the `nat` table that are evaluated before ufw's INPUT chain, so
anything in `ports:` is reachable from the internet whether ufw lists it or not
— and conversely, `ufw deny 8081` would not close it. What actually keeps the
database and Redis private is that this compose file never publishes them: they
are reachable only on the internal Docker network. Check exposure with
`docker compose -f docker-compose.prod.yml ps`, which shows the real published
set, rather than inferring it from `ufw status`.

## 2. Get the code onto the VPS

Clone it. The server only needs the compose file, the Caddyfile and the backup
scripts — the application itself arrives as prebuilt images from GHCR — but a
real checkout is what makes `git pull` updates possible later.

```bash
git clone https://github.com/MuhammadMohsinKhatri/AI_kona_Travel_CRM_System.git /opt/konaice
```

If the repository has been made private again, that clone will prompt for a
password and fail (GitHub stopped accepting account passwords over HTTPS in
2021). Use a read-only deploy key instead — generate it **on the VPS**, so the
private half never travels:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/konaice_deploy -N ''
cat ~/.ssh/konaice_deploy.pub     # add this in GitHub: repo -> Settings -> Deploy keys
printf 'Host github.com\n  IdentityFile ~/.ssh/konaice_deploy\n  IdentitiesOnly yes\n' >> ~/.ssh/config
git clone git@github.com:MuhammadMohsinKhatri/AI_kona_Travel_CRM_System.git /opt/konaice
```

Copying the tree from your PC with `scp -r` also works, but leaves a directory
that is not a git checkout, so every later update becomes another manual copy.
If you do it, exclude `backend/.venv` and `frontend/node_modules` — they are
rebuilt inside Docker and only slow the transfer.

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

**With a domain** — create the DNS A record *first* and wait for it to resolve.
Caddy proves it controls the name over ports 80 and 443; if the record does not
point here yet, the certificate request fails and Caddy retries with a backoff,
so a premature launch means an unreachable site for several minutes rather than
an instant error.

```bash
cat >> .env <<'EOF'
DOMAIN=ops.your-domain.com
WEB_PORT=80
TLS_PORT=443
EOF
docker compose -f docker-compose.prod.yml config | grep -E 'DOMAIN|published'   # confirm before launching
docker compose -f docker-compose.prod.yml up -d
```

Verify the record has propagated before that last command:

```bash
getent hosts ops.your-domain.com     # must print this server's IP
```

**Without a domain** — plain HTTP on port 8081, no certificate:

```bash
docker compose -f docker-compose.prod.yml up -d
```

Images come from GHCR (both packages are public, so no `docker login`), which
takes a couple of minutes. Add `--build` only to compile locally instead — needed
if you are testing an uncommitted change, and it takes ~5 minutes.

Then open `https://ops.your-domain.com` (or `http://YOUR_VPS_IP:8081`).

## 5. Verify

```bash
docker compose -f docker-compose.prod.yml ps                 # all services Up (healthy)
curl -s localhost/health                                     # port-80 layout
curl -s localhost:8081/health                                # 8081 layout
docker compose -f docker-compose.prod.yml logs -f backend    # watch logs
```

`/health` reports a `build` field — the short commit the running backend image
was built from. That is the only reliable way to tell which code is live, since
a backend-only change moves no frontend asset and nothing else observable.

Log in, check **API Explorer → KonaOS Session** shows `connected`, run the
pipeline for a date, confirm drafts appear (dry-run).

## 6. Moving an existing server to a new one

Skip this section for a first-time install. It applies when a live box already
exists and this new one replaces it.

**The hazard, first.** Two armed instances pointed at the same KonaOS account
will both write. It is not only the nightly run: `rerun-changed-events` re-runs
recently-changed events **every hour**, and `app/tasks/celery_app.py` records an
invoice-duplication bug that is guarded against, not root-caused. Duplicate
drafts against real client invoices are painful to unpick by hand. So the new
box stays disarmed until the old one is switched off — never both at once.

### 6a. Bring the new box up disarmed

In the new box's `backend/.env`, before the first launch:

```
PIPELINE_DRY_RUN=true
```

Set it explicitly. `pipeline_dry_run` defaults to **False** in `app/config.py`,
so a `.env` that simply omits the key comes up *armed* — the safe state is the
one you have to ask for. Confirm it took effect by reading `pipeline_dry_run`
back from `/health` rather than assuming.

A dry run computes everything and writes nothing to KonaOS, which is exactly
what you want while validating. Belt and braces, keep the scheduler down too:

```bash
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml stop beat
```

`beat` is the only thing that fires timed work; with it stopped, nothing runs
unless you click it. The worker stays up so manual runs still work.

### 6b. Copy the database across

On the **old** box, take a fresh dump:

```bash
/opt/konaice/deploy/backup.sh && ls -lh /opt/konaice/backups | tail -3
```

On the **new** box, pull that file over and restore it. You will be prompted for
the old server's root password — type it yourself; it does not need to be shared
with anyone:

```bash
mkdir -p /opt/konaice/backups
scp root@OLD_VPS_IP:/opt/konaice/backups/konaice-<stamp>.sql.gz /opt/konaice/backups/
chmod +x /opt/konaice/deploy/restore.sh
/opt/konaice/deploy/restore.sh /opt/konaice/backups/konaice-<stamp>.sql.gz
```

The restore drops and recreates every table in the dump, which on a
freshly-created database is harmless — there is nothing to lose yet. It also
brings the **old admin logins** with it, so log in with the existing
credentials, not new ones. `seed_admin` in `app/bootstrap.py` looks for a user
matching `FIRST_ADMIN_EMAIL` specifically: if the restored data already has that
address it changes nothing (your old password stands, and editing
`FIRST_ADMIN_PASSWORD` will not reset it), and if you set a different address
you simply get one extra admin account alongside the restored ones.

### 6c. Carry over the secrets

`backend/.env` is deliberately excluded from backups, so it does not travel with
the dump. Print it on the old box and paste it into `nano` on the new one:

```bash
cat /opt/konaice/backend/.env          # on the OLD box
```

Carry every value over verbatim **except** these, which are per-server:

| Key | New value |
|---|---|
| `BACKEND_CORS_ORIGINS` | the new address — `https://ops.your-domain.com` |
| `PIPELINE_DRY_RUN` | `true` until cutover |

`KONAOS_SESSION_KEY` is worth carrying over even though it will be refreshed
automatically — it saves a login round trip on first start. The `konaos_cache`
volume is *not* migrated and does not need to be; it is rebuilt on demand.

Store this file in a password manager the same day. Without it a restore comes
back with all the data and no way to reach KonaOS.

### 6d. Validate on real data, still disarmed

With the old box still serving live, run a date through the new box in dry-run
and compare against what the old one produced for that same date. Check event
counts, invoice totals and the classification of a min-guarantee event. This is
also the moment to satisfy yourself about the two bugs listed in
`celery_app.py`, since a dry run writes nothing.

### 6e. Cut over — old box off first

Order matters. Disarm the old box **before** arming the new one:

```bash
# on the OLD box — stop all writers, leave the data intact
cd /opt/konaice && docker compose -f docker-compose.prod.yml stop backend worker beat
```

Then, on the new box:

```bash
sed -i 's/PIPELINE_DRY_RUN=true/PIPELINE_DRY_RUN=false/' backend/.env
docker compose -f docker-compose.prod.yml up -d backend worker beat
curl -s localhost/health        # pipeline_dry_run should now be false
```

If anything looks wrong, the old box is one `start` away from being live again —
which is why it is stopped rather than destroyed. Keep it for a week, take a
final dump before cancelling it, and only then let the plan lapse.

Take a Hostinger snapshot of the new box once it is serving traffic
correctly — see [BACKUPS.md](BACKUPS.md).

## 7. Going fully live

For a first-time install, when the dry-run drafts look right:

```bash
sed -i 's/PIPELINE_DRY_RUN=true/PIPELINE_DRY_RUN=false/' backend/.env
docker compose -f docker-compose.prod.yml up -d backend worker beat
```

Celery beat then runs, all times America/New_York: the KonaOS session check at
23:00, the nightly pipeline at 23:30, changed-event re-runs hourly at :15,
invoice-id backfill hourly at :45, and awaiting-cash flagging at 09:00.

## Updating the app later

**Auto-deploy (the normal path).** Every push to `main` triggers GitHub Actions
(`.github/workflows/deploy.yml`), which builds the backend + frontend images and
publishes them to GHCR. Watchtower on the VPS polls every 5 minutes and
auto-pulls + restarts the changed services. **Pushing to main IS the deploy** —
nothing to run on the server. (db/redis/caddy are unlabeled and never touched.)

Both GHCR packages are currently **public**, so nothing needs a login: the VPS
and Watchtower pull anonymously. Verify rather than assume, from any machine:

```bash
docker manifest inspect ghcr.io/muhammadmohsinkhatri/konaice-backend:latest >/dev/null && echo public
```

If either package is ever switched back to private, Watchtower stops deploying
and the failure is quiet — it logs the pull error and keeps the old container
running, so the app stays up on stale code. Restore it with a read-only token:

```bash
# PAT: github.com → Settings → Developer settings → Tokens (classic),
# scope: read:packages only
docker login ghcr.io -u muhammadmohsinkhatri     # paste the PAT as password
```

Then uncomment the `config.json` mount on the `watchtower` service in
`docker-compose.prod.yml` — without it Watchtower keeps pulling anonymously even
though the host is logged in. Mount it only *after* the login has created the
file; mounting a path that does not exist makes Docker create a directory there.

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

## Sharing a box with another ingress

If ports 80/443 already belong to someone else's reverse proxy (another team's
Traefik or Caddy on a shared VPS), `deploy/Caddyfile` never gets a hostname —
konaice stays on `WEB_PORT`/`TLS_PORT` (see Step 4) and real HTTPS has to come
from the *other* proxy routing to us by container name instead.

That needs two things, in order:

**1. Join frontend to the other proxy's Docker network.** A one-off `docker
network connect` would be silently undone the next time Watchtower recreates
the container for a new image, so this is a compose overlay instead —
[deploy/docker-compose.shared-ingress.yml](docker-compose.shared-ingress.yml).
Only `frontend` needs to join: its nginx already forwards `/api`, `/health`,
`/openapi.json` and `/docs` to the backend internally (`frontend/nginx.conf`),
same as `deploy/Caddyfile`'s own blanket `reverse_proxy frontend:80`.

```bash
# find the other proxy's network name first
docker network ls
SHARED_INGRESS_NETWORK=web docker compose -f docker-compose.prod.yml \
    -f deploy/docker-compose.shared-ingress.yml up -d frontend
docker inspect konaice-frontend-1 --format \
    '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'   # both networks should list
```

Run that same `up -d` with both `-f` flags any time frontend needs recreating
by hand — Watchtower-triggered updates preserve whatever networks the running
container already has, so this is only for manual recreates.

**2. Add a site block to the other proxy.** This means editing config that
belongs to another application's ingress — confirm you actually have the
authority to change it before touching anything, since a mistake here can
take that team's app down too, not just konaice's.

```bash
# back up first — this file is someone else's live routing config
cp /opt/proxy/Caddyfile /opt/proxy/Caddyfile.bak-$(date -u +%Y%m%d%H%M%SZ)

cat >> /opt/proxy/Caddyfile <<'EOF'

# ------------------------------------------------------------------ konaice
finance.example.com {
        import common
        reverse_proxy konaice-frontend-1:80
}
EOF

docker exec proxy-caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec proxy-caddy caddy reload   --config /etc/caddy/Caddyfile --adapter caddyfile
```

`caddy reload` is graceful — existing connections to the other app are not
dropped. Validate before every reload regardless; a syntax error in `reload`
fails closed and takes the whole proxy down, not just the new block.

Confirm both sides still work:

```bash
curl -sI https://finance.example.com | head -3      # new site, real cert
curl -sI https://<the-other-app's-existing-domain> | head -3   # unaffected
```

Then update `BACKEND_CORS_ORIGINS` in `backend/.env` — the browser-visible
origin is now `https://finance.example.com` with no port, not the `:8081`
address, and CORS is origin-exact:

```bash
sed -i 's|^BACKEND_CORS_ORIGINS=.*|BACKEND_CORS_ORIGINS=https://finance.example.com,http://<vps-ip>:8081|' backend/.env
docker compose -f docker-compose.prod.yml up -d backend
```

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
