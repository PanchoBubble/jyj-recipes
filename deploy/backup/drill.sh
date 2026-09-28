#!/bin/sh
# Restore drill in a throwaway compose project: seed, back up, destroy, restore, verify.
# Never touches the real stack's volumes (project jyj-drill, own BACKUP_DIR).
set -eu

cd "$(dirname "$0")/../.."
project=jyj-drill
work=$(mktemp -d "${TMPDIR:-/tmp}/jyj-drill.XXXXXX")
env_file="$work/drill.env"
cat >"$env_file" <<ENV
POSTGRES_DB=jyj
POSTGRES_USER=jyj
POSTGRES_PASSWORD=drill-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')
SESSION_SECRET=drill-$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
BACKUP_DIR=$work/backups
BACKUP_UID=$(id -u)
BACKUP_GID=$(id -g)
ENV

compose() { docker compose -p "$project" --env-file "$env_file" "$@"; }
psql_q() { compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -Atc "$1"' _ "$1"; }
photos_sum() {
	compose run --rm --no-deps -T --entrypoint sh backend -c \
		'cd /var/lib/jyj/photos && find . -type f | sort | xargs -r sha256sum && find . | sort | xargs stat -c "%u:%g %a %n"'
}
fingerprint() {
	psql_q "select count(*) from units"
	psql_q "select username || ':' || display_name from users order by id"
	photos_sum
}
cleanup() { compose --profile backup down -v --remove-orphans >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
step() { printf '\n== %s\n' "$*"; }

step "fresh stack ($project)"
compose --profile backup down -v --remove-orphans >/dev/null 2>&1 || true
compose up -d --wait db
compose run --rm --no-deps -T backend alembic upgrade head

step "seed data"
psql_q "insert into users (username, display_name, password_hash) values ('drill', 'Drill User', 'not-a-real-hash')" >/dev/null
compose run --rm --no-deps -T --entrypoint sh backend -c '
	mkdir -p /var/lib/jyj/photos/recipes/1 &&
	head -c 65536 /dev/urandom > /var/lib/jyj/photos/recipes/1/cover.jpg &&
	echo hello > /var/lib/jyj/photos/recipes/1/step-1.jpg'
fingerprint >"$work/before.txt"
cat "$work/before.txt"

step "backup (jyj-backup once)"
compose --profile backup build backup
compose --profile backup run --rm -T backup once
ls -la "$work/backups" "$work/backups/db" "$work/backups/photos"
dump=$(ls -1 "$work"/backups/db/*.dump | tail -n 1)
snap=$(ls -1d "$work"/backups/photos/*Z | tail -n 1)

step "destroy volumes"
compose --profile backup down -v --remove-orphans
compose up -d --wait db
compose create --no-build backend
psql_q "select to_regclass('public.users') is null" | grep -qx t && echo "database is empty"

step "restore refuses without the flag"
if deploy/backup/restore.sh -p "$project" --env-file "$env_file" --db "$dump" --confirm "$project" 2>&1; then
	echo "FAIL: restore ran without --yes-destroy-current-data"; exit 1
fi

step "restore"
deploy/backup/restore.sh -p "$project" --env-file "$env_file" --db "$dump" --photos "$snap" \
	--yes-destroy-current-data --confirm "$project"

step "verify"
fingerprint >"$work/after.txt"
cat "$work/after.txt"
if diff -u "$work/before.txt" "$work/after.txt"; then
	echo "DRILL PASSED: $(basename "$dump") + photos/$(basename "$snap") reproduce the seeded data"
else
	echo "DRILL FAILED"; exit 1
fi

step "labelled backup survives pruning and hardlinks unchanged photos"
compose --profile backup run --rm -T backup once drill
links=$(compose --profile backup run --rm --no-deps -T --entrypoint sh backup -c \
	'stat -c %h /backups/photos/*Z-drill/recipes/1/cover.jpg')
echo "cover.jpg link count: $links"
[ "$links" -ge 2 ] || { echo "DRILL FAILED: photo snapshots are not hardlinked"; exit 1; }
echo "DRILL PASSED"
