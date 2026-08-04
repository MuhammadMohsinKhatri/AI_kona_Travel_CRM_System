# Backups — what is kept, where, and how to restore

Written to answer three questions directly: how backups are configured, where
they are stored, and what happens if the server fails.

---

## The short version

| | |
|---|---|
| **What is backed up** | The whole Postgres database — every event, invoice, payment, alert, run and user |
| **How often** | Nightly at 03:15 UTC (about 11:15 PM Baltimore) |
| **Kept for** | 14 days of nightly backups, plus the 1st of every month kept indefinitely |
| **Where** | On the server at `/opt/konaice/backups`, **and** offsite if configured (see below) |
| **Worst case data loss** | Up to 24 hours — whatever was recorded since the last nightly run |
| **Time to restore** | Around 10 minutes with the server up; a few hours if rebuilding from nothing |

---

## The three layers, and what each one actually protects against

These are not redundant. Each covers a failure the others do not, and it is
worth knowing which is which.

### 1. Nightly database dump — ours

`deploy/backup.sh`, run by cron. A compressed `pg_dump` of the whole database.

**Protects against:** a bad deploy, a dropped table, a mistaken bulk delete,
corrupted data discovered days later.

**Does not protect against:** losing the server itself, since the backups sit on
it.

### 2. Hostinger snapshots — the provider's

Hostinger takes automatic snapshots of the whole server (weekly on most VPS
plans; check **hPanel → VPS → Backups** for what your plan includes). A snapshot
is the entire machine, not just the database, and restores with one click.

**Protects against:** the server being destroyed, a botched OS upgrade, the disk
failing.

**Does not protect against:** losing access to the Hostinger account, or
Hostinger themselves having a bad day — the snapshots live with them.

You can also take a manual snapshot before any risky change. Worth doing before
a major upgrade.

### 3. Offsite copies — recommended, not yet switched on

Layers 1 and 2 both live inside one Hostinger account. If that account is lost,
compromised, or suspended over a billing problem, both go at once. An offsite
copy is what makes that survivable.

`backup.sh` supports this already. Set a remote and it copies each nightly dump
after writing it:

```bash
# on the server, once
apt install rclone
rclone config          # add a remote: Backblaze B2, AWS S3, Google Drive…
echo 'RCLONE_REMOTE=b2:konaice-backups' > /etc/default/konaice-backup
```

Backblaze B2 is the usual choice — the dumps are small, so this costs cents a
month. **This needs a decision from you** about where the copies go and who
holds that account; it is deliberately not configured by default rather than
quietly sending your financial data somewhere you did not choose.

---

## What is NOT in the nightly backup, and matters

**`backend/.env`** — the configuration file holding the KonaOS API credentials,
the OpenAI key, the database password and the session key. It is excluded on
purpose: sweeping secrets into a nightly archive that then gets copied offsite
is how credentials leak.

It also barely ever changes. **Store one copy in a password manager** the day the
system goes live, and again whenever a key is rotated. Without it, a restore
brings back all your data and then cannot connect to anything.

**The code** — lives in GitHub, so it is already backed up. A rebuild pulls it.

---

## Installing the nightly job

Once, on the server:

```bash
chmod +x /opt/konaice/deploy/backup.sh /opt/konaice/deploy/restore.sh

# 03:15 UTC nightly, logging to syslog so failures are visible in journalctl
( crontab -l 2>/dev/null; \
  echo '15 3 * * * /opt/konaice/deploy/backup.sh >> /var/log/konaice-backup.log 2>&1' \
) | crontab -
```

Check it works before trusting it:

```bash
/opt/konaice/deploy/backup.sh          # run once by hand
ls -lh /opt/konaice/backups/           # a file appears, a few hundred KB up
```

The script exits non-zero on failure so cron mails the error rather than
producing nothing quietly. A backup that fails silently is worse than no backup,
because it is believed.

---

## Restoring

### The database is wrong but the server is fine

Dropped table, bad import, a day's data corrupted:

```bash
ls -lh /opt/konaice/backups/                        # pick one
/opt/konaice/deploy/restore.sh /opt/konaice/backups/konaice-2026-08-03T031500Z.sql.gz
```

It asks for confirmation, takes a safety copy of the current state first, stops
the application, restores, and starts it again. About 10 minutes.

Everything recorded since that backup is lost — so if it is early in the day,
consider whether re-running the pipeline for the affected dates is a better
answer than going back a night.

### The server is gone entirely

1. **Restore the Hostinger snapshot** (hPanel → VPS → Backups). This brings the
   whole machine back including the application and the most recent database it
   had. Usually the fastest route, and often the only step needed.

2. **If there is no usable snapshot**, rebuild:

   ```bash
   # new VPS, Ubuntu 24.04 with Docker
   git clone https://github.com/MuhammadMohsinKhatri/AI_kona_Travel_CRM_System.git /opt/konaice
   cd /opt/konaice
   cp <backend/.env from your password manager> backend/.env
   echo 'POSTGRES_PASSWORD=<from the same place>' > .env
   echo 'DOMAIN=ops.konacatonsville.com' >> .env
   docker compose -f docker-compose.prod.yml up -d
   # then restore the most recent dump you hold
   ./deploy/restore.sh /path/to/konaice-latest.sql.gz
   ```

   Repoint the domain's DNS at the new IP. Allow a few hours, mostly waiting.

### Restoring somewhere else entirely

The dump is ordinary PostgreSQL. It restores into any Postgres 16 — AWS RDS,
Google Cloud SQL, a laptop — with `psql`. Nothing about the backup ties you to
Hostinger or to us.

---

## Testing that any of this works

A backup nobody has restored is a hypothesis.

**Once, after go-live, and then every six months**, restore the previous night's
dump onto a scratch machine — or into a second database on the same server — and
confirm the row counts look right. Ten minutes, and it converts an assumption
into a fact. The failure modes it catches (a dump silently truncated, a missing
`.env`, a Postgres version mismatch) are exactly the ones that otherwise surface
on the worst possible day.

---

## Summary for the record

- **Configured:** nightly `pg_dump` at 03:15 UTC, 14 daily + monthly retention,
  on-server at `/opt/konaice/backups`
- **Provider layer:** Hostinger snapshots of the whole machine, per your plan
- **Offsite:** supported and recommended, awaiting your choice of destination
- **Secrets:** `backend/.env` kept in a password manager, not in the backups
- **Restore:** `deploy/restore.sh`, ~10 minutes, safety copy taken first
- **Recovery point:** at most 24 hours. Tightenable to hourly if that is ever
  too loose — the same script on a shorter schedule.
