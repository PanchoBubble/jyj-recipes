#!/bin/sh
# Restore a jyj-backup dump and/or photo snapshot into a running compose stack.
# Destroys the current database and photos; see docs/BACKUP.md.
set -eu

usage() {
	cat >&2 <<'USAGE'
usage: deploy/backup/restore.sh --yes-destroy-current-data [--db FILE.dump] [--photos SNAPSHOT_DIR]
                                [-p PROJECT] [--env-file FILE] [--confirm PROJECT]
  --db        pg_dump -Fc file from BACKUP_DIR/db
  --photos    snapshot directory from BACKUP_DIR/photos
  -p          compose project (default: jyj, or COMPOSE_PROJECT_NAME)
  --confirm   answer the confirmation prompt non-interactively (must equal the project name)
USAGE
	exit 2
}

die() { echo "restore: $*" >&2; exit 1; }

cd "$(dirname "$0")/../.."

project=${COMPOSE_PROJECT_NAME:-jyj}
env_file='' db_file='' photos_dir='' confirm='' destroy=0
while [ $# -gt 0 ]; do
	case $1 in
	--db) db_file=${2:?}; shift 2 ;;
	--photos) photos_dir=${2:?}; shift 2 ;;
	-p) project=${2:?}; shift 2 ;;
	--env-file) env_file=${2:?}; shift 2 ;;
	--confirm) confirm=${2:?}; shift 2 ;;
	--yes-destroy-current-data) destroy=1; shift ;;
	*) usage ;;
	esac
done

[ -n "$db_file$photos_dir" ] || usage
[ "$destroy" = 1 ] || die "refusing without --yes-destroy-current-data (this replaces the live database/photos)"
[ -z "$db_file" ] || [ -f "$db_file" ] || die "no such dump: $db_file"
[ -z "$photos_dir" ] || [ -d "$photos_dir" ] || die "no such snapshot dir: $photos_dir"

compose() { docker compose -p "$project" ${env_file:+--env-file "$env_file"} "$@"; }

running=$(compose ps --services --status running)
echo "$running" | grep -qx db || die "db service of project '$project' is not running: docker compose -p $project up -d --wait db"

if [ -n "$db_file" ]; then
	compose exec -T db pg_restore --list <"$db_file" >/dev/null || die "$db_file is not a valid pg_dump -Fc file"
fi
if [ -n "$photos_dir" ]; then
	photos_dir=$(cd "$photos_dir" && pwd)
	photos_vol=$(docker volume ls -q \
		--filter "label=com.docker.compose.project=$project" \
		--filter "label=com.docker.compose.volume=photos")
	[ -n "$photos_vol" ] || die "photos volume of project '$project' not found: docker compose -p $project create backend"
fi

echo "About to restore into compose project '$project':"
[ -z "$db_file" ] || echo "  DROP and recreate the database from $db_file"
[ -z "$photos_dir" ] || echo "  REPLACE all files in volume $photos_vol with $photos_dir (extra files deleted)"
if [ -z "$confirm" ]; then
	[ -r /dev/tty ] || die "no tty for the confirmation prompt; pass --confirm $project"
	printf "Type the project name (%s) to continue: " "$project" >/dev/tty
	read -r confirm </dev/tty
fi
[ "$confirm" = "$project" ] || die "confirmation did not match, nothing changed"

stopped=''
for svc in backend web backup; do
	if echo "$running" | grep -qx "$svc"; then stopped="$stopped $svc"; fi
done
if [ -n "$stopped" ]; then
	echo "stopping:$stopped"
	# shellcheck disable=SC2086
	compose --profile backup stop $stopped
fi

if [ -n "$db_file" ]; then
	echo "restoring database"
	compose exec -T db sh -eu -c '
		dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB"
		createdb -U "$POSTGRES_USER" -O "$POSTGRES_USER" "$POSTGRES_DB"'
	compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --role="$POSTGRES_USER" --single-transaction --exit-on-error' <"$db_file"
fi

if [ -n "$photos_dir" ]; then
	echo "restoring photos"
	docker image inspect jyj-backup >/dev/null 2>&1 || compose --profile backup build backup
	# Owned by the backend's app user (uid 10001 in backend/Dockerfile).
	docker run --rm --network none --entrypoint rsync \
		-v "$photos_vol:/photos" -v "$photos_dir:/src:ro" jyj-backup \
		-a --delete --numeric-ids --chown="${PHOTOS_UID:-10001}:${PHOTOS_GID:-10001}" --chmod=D755,F644 /src/ /photos/
fi

if [ -n "$stopped" ]; then
	echo "starting:$stopped"
	# shellcheck disable=SC2086
	compose --profile backup start $stopped
fi
echo "restore complete"
