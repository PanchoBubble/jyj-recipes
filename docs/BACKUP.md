# Backups

A `backup` compose service (profile `backup`) runs nightly on the Pi and writes into `BACKUP_DIR`, which should be a path on the external disk.

## Setup

In `.env`:

```sh
COMPOSE_PROFILES=backup          # `make up` / `docker compose up` now also start the backup service
BACKUP_DIR=/mnt/usb/jyj-backups  # host path; default ./backups (git-ignored)
BACKUP_CRON=03:30                # HH:MM, 24h, in TZ
TZ=Europe/Madrid                 # default UTC
BACKUP_KEEP_DAILY=7
BACKUP_KEEP_WEEKLY=4
BACKUP_UID=1000                  # `id -u` of the Pi user that should own the files
BACKUP_GID=1000
```

Then `make up`. Check it scheduled: `docker compose logs backup` shows `next run in ...s`.

`BACKUP_CRON` is a plain daily time, not a cron expression. On DST change days the run may be an hour off.

## What is backed up

| Data | How | Where |
|---|---|---|
| Postgres (recipes, pantry, users, sessions) | `pg_dump -Fc`, verified with `pg_restore --list` | `BACKUP_DIR/db/jyj-db-<UTC stamp>.dump` |
| Photos volume (mounted read-only) | `rsync -a --delete` snapshot, unchanged files hardlinked to the previous snapshot | `BACKUP_DIR/photos/<UTC stamp>/` |

Stamps look like `20260928T033000Z`. Files are written as `*.partial` and renamed when complete, so a crash never leaves a half file under a real name. Directories are `0700`, dump and photo files `0600`, owned by `BACKUP_UID:BACKUP_GID`.

Photo snapshots cost only the new or changed photos each night because of hardlinks. `du -sh BACKUP_DIR/photos` counts shared files once.

## What is not backed up, and why

- **`codex-home`**: holds the Codex CLI login token (owned by the Codex CLI, see docs/CODEX.md). A copy on a USB disk is a credential lying around. After a rebuild, log in again with `docker compose exec backend codex login --device-auth`.
- **`caddy-data`**: holds Caddy's internal root CA **private key**. Copying it off the Pi means anyone with the backup can mint certificates every household device trusts. Losing it is cheap: Caddy generates a new CA on first start, then run `make ca-export` and reinstall the new root on each device (docs/TLS.md). If you would rather not reinstall, back it up by hand, **to encrypted storage only**, and treat that file like a password:
  `docker run --rm -v jyj_caddy-data:/data:ro alpine tar -C /data -cf - caddy/pki | gpg -c > caddy-pki.tar.gpg`
- **`caddy-config`**, **`whisper-models`**: regenerated or re-downloaded automatically.
- **`.env`**: keep your own copy in a password manager. It holds `POSTGRES_PASSWORD` and `SESSION_SECRET`. A restore works with any `.env`; a new `SESSION_SECRET` only logs everyone out.

The dump itself contains password hashes (argon2id) and session token hashes. Treat `BACKUP_DIR` as private.

## Retention

After every run, for unlabelled backups:

- keep the newest backup of each of the last `BACKUP_KEEP_DAILY` days that have one,
- plus the newest of each of the last `BACKUP_KEEP_WEEKLY` ISO weeks,
- delete the rest.

Default is 7 + 4, so at most 11 backups, spanning about a month.

Manual backups: `make backup-now` makes an ordinary backup (it supersedes that day's older one). `make backup-now label=pre-upgrade` writes `...Z-pre-upgrade` and is **never pruned**; delete those yourself.

## Chat retention

Chat text is kept 90 days. The `chat-purge` service (always on, not behind a profile) runs `jyj chat purge` when the stack starts and then every 24 hours: it deletes messages older than 90 days and conversations idle that long, closes proposals nobody can reach any more, and keeps the `chat_actions` audit rows. Each run logs one line:

```sh
docker compose logs chat-purge
docker compose exec backend jyj chat purge --days 30   # one-off run, shorter window
```

A failed run is logged and retried on the next cycle. Backups taken before a purge still hold the older text until they are pruned (about a month with the defaults above).

## Copy off the Pi

The external disk protects against SD-card death, not against theft, fire or the disk failing. Copy offsite now and then, from another machine:

```sh
rsync -aH --delete pi@recipes.home.arpa:/mnt/usb/jyj-backups/ ~/jyj-backups/
```

`-H` keeps the photo hardlinks, otherwise each snapshot is a full copy. Keep the copy on an encrypted disk.

## Restore

Restore replaces the live database and photos. It needs the db service running, refuses without `--yes-destroy-current-data`, and asks you to type the project name.

```sh
docker compose up -d --wait db     # fresh Pi: also `docker compose create backend` so the photos volume exists
make restore db=/mnt/usb/jyj-backups/db/jyj-db-20260928T033000Z.dump \
             photos=/mnt/usb/jyj-backups/photos/20260928T033000Z force=yes
```

Either `db=` or `photos=` can be given alone. What it does:

1. validates the dump with `pg_restore --list`,
2. stops backend, web and backup if running,
3. `dropdb --force` + `createdb`, then `pg_restore --single-transaction --exit-on-error`,
4. rsyncs the snapshot into the photos volume (`--delete`, owner `10001:10001`, the backend's app user),
5. starts the stopped services again.

Direct use: `deploy/backup/restore.sh --help`. `-p PROJECT` targets another compose project.

If the backup came from an older app version, run `docker compose run --rm migrate` after restoring.

## Restore drill

`make backup-drill` (about 30s) runs `deploy/backup/drill.sh` in a throwaway compose project `jyj-drill` with its own temp `BACKUP_DIR`. It never touches the `jyj` project's volumes:

1. fresh `db`, `alembic upgrade head` (seeds units),
2. inserts a user with psql and writes two photos into the photos volume,
3. records a fingerprint: unit count, users, photo sha256 + owner/mode,
4. `jyj-backup once`,
5. `down -v` (volumes gone), fresh empty stack,
6. checks restore refuses without the flag, then restores,
7. compares the fingerprint, prints `DRILL PASSED`,
8. takes a labelled backup and checks it survived pruning and hardlinks the unchanged photos,
9. `down -v` and removes the temp dir.

Run it after changing anything here, and on the Pi after setup. Every few months, also restore a real nightly backup into `-p jyj-drill` by hand and spot-check it with `docker compose -p jyj-drill exec db psql -U jyj jyj`.
