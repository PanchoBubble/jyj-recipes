#!/bin/sh
# jyj-backup schedule         run nightly at BACKUP_CRON (HH:MM, container TZ)
# jyj-backup once [label]     one backup now; labelled backups are never pruned
set -eu
umask 077

ROOT=/backups
PHOTOS_SRC=/photos
KEEP_DAILY=${BACKUP_KEEP_DAILY:-7}
KEEP_WEEKLY=${BACKUP_KEEP_WEEKLY:-4}
OWNER="${BACKUP_UID:-1000}:${BACKUP_GID:-1000}"
STAMP_RE='[0-9]{8}T[0-9]{6}Z'

log() { printf '%s jyj-backup: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*" >&2; exit 1; }

# Strip one leading zero so ash arithmetic doesn't read 08/09 as octal.
num() { v=${1#0}; echo "${v:-0}"; }

run_once() {
	label=${1:-}
	case $label in
	'') suffix='' ;;
	*[!a-z0-9-]*) die "label must be [a-z0-9-]: $label" ;;
	*) suffix="-$label" ;;
	esac
	stamp=$(date -u +%Y%m%dT%H%M%SZ)
	mkdir -p "$ROOT/db" "$ROOT/photos"
	chmod 700 "$ROOT" "$ROOT/db" "$ROOT/photos"
	rm -rf "$ROOT"/db/*.partial "$ROOT"/photos/*.partial

	dump="$ROOT/db/jyj-db-$stamp$suffix.dump"
	log "pg_dump $PGDATABASE -> $dump"
	pg_dump -Fc -Z 6 -f "$dump.partial"
	pg_restore --list "$dump.partial" >/dev/null || die "dump failed pg_restore --list"
	chmod 600 "$dump.partial"
	chown "$OWNER" "$dump.partial"
	mv "$dump.partial" "$dump"

	# Hardlink unchanged photos against the newest snapshot: each night costs only new files.
	snap="$ROOT/photos/$stamp$suffix"
	prev=$(ls -1 "$ROOT/photos" | grep -E "^$STAMP_RE(-[a-z0-9-]+)?\$" | sort | tail -n 1 || true)
	log "photos -> $snap${prev:+ (link-dest $prev)}"
	rsync -a --delete --numeric-ids --chown="$OWNER" \
		--chmod=Du=rwx,Dgo=,Fu=rw,Fgo= \
		${prev:+--link-dest="../$prev"} \
		"$PHOTOS_SRC/" "$snap.partial/"
	mv "$snap.partial" "$snap"
	chown "$OWNER" "$ROOT" "$ROOT/db" "$ROOT/photos"

	prune "$ROOT/db" "^jyj-db-$STAMP_RE\\.dump\$"
	prune "$ROOT/photos" "^$STAMP_RE\$"
	log "done $stamp$suffix ($(du -sh "$dump" | cut -f1) db)"
}

# Keep the newest backup of each of the last KEEP_DAILY days and of each of the
# last KEEP_WEEKLY ISO weeks; delete the rest. Labelled backups never match.
prune() {
	dir=$1 pattern=$2
	days='' weeks='' nd=0 nw=0
	for name in $(ls -1 "$dir" | grep -E "$pattern" | sort -r); do
		s=$(echo "$name" | grep -oE "$STAMP_RE")
		day=${s%%T*}
		week=$(date -u -d "$(echo "$day" | sed 's/^\(....\)\(..\)\(..\)$/\1-\2-\3/')" +%G-%V)
		keep=0
		case " $days " in *" $day "*) ;; *)
			if [ "$nd" -lt "$KEEP_DAILY" ]; then days="$days $day" nd=$((nd + 1)) keep=1; fi ;;
		esac
		case " $weeks " in *" $week "*) ;; *)
			if [ "$nw" -lt "$KEEP_WEEKLY" ]; then weeks="$weeks $week" nw=$((nw + 1)) keep=1; fi ;;
		esac
		if [ "$keep" = 0 ]; then
			log "prune $dir/$name"
			rm -rf "${dir:?}/$name"
		fi
	done
}

schedule() {
	case ${BACKUP_CRON:=03:30} in
	[01][0-9]:[0-5][0-9] | 2[0-3]:[0-5][0-9]) ;;
	*) die "BACKUP_CRON must be HH:MM (24h), got: $BACKUP_CRON" ;;
	esac
	target=$(($(num "${BACKUP_CRON%:*}") * 3600 + $(num "${BACKUP_CRON#*:}") * 60))
	trap 'log "stopping"; exit 0' TERM INT
	log "scheduled daily at $BACKUP_CRON ($(date +%Z)), keep $KEEP_DAILY daily / $KEEP_WEEKLY weekly"
	while :; do
		now=$(date +%H:%M:%S)
		h=${now%%:*} rest=${now#*:}
		secs=$(($(num "$h") * 3600 + $(num "${rest%:*}") * 60 + $(num "${rest#*:}")))
		wait_s=$((target - secs))
		[ "$wait_s" -le 0 ] && wait_s=$((wait_s + 86400))
		log "next run in ${wait_s}s"
		sleep "$wait_s" &
		wait $! || true
		# Child process: set -e is ignored inside a function on the left of ||.
		"$0" once || log "ERROR: backup failed, retrying at next schedule"
	done
}

[ "$KEEP_DAILY" -ge 1 ] 2>/dev/null || die "BACKUP_KEEP_DAILY must be >= 1"
[ "$KEEP_WEEKLY" -ge 0 ] 2>/dev/null || die "BACKUP_KEEP_WEEKLY must be >= 0"
[ -d "$PHOTOS_SRC" ] || die "$PHOTOS_SRC not mounted"

case ${1:-schedule} in
schedule) schedule ;;
once) run_once "${2:-}" ;;
*) die "usage: jyj-backup schedule | once [label]" ;;
esac
