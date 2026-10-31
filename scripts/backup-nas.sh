#!/bin/sh
# Example: back up the database to a NAS (NFS mount), run daily by a systemd timer or cron
# (see README.md, "Backup and restore"). Uses "flask backup" in a one-off container, so the copy
# is consistent while the app is running. Backups older than KEEP_DAYS are deleted.
#
#   BACKUP_DIR=/mnt/nas/helvax   target directory (must be a mount point)
#   KEEP_DAYS=30                 age in days after which backups are deleted
#   REQUIRE_MOUNT=1              0 allows a plain local directory (testing)
#   AGE_RECIPIENT=age1...        encrypt with age (https://age-encryption.org) before the file leaves
#                                this host; the NAS only ever sees helvax-*.db.age. Needs the `age` command.
#   PUID=1000                    the app user in the container (writes the plain backup)
set -eu

BACKUP_DIR="${BACKUP_DIR:-/mnt/nas/helvax}"
KEEP_DAYS="${KEEP_DAYS:-30}"
REQUIRE_MOUNT="${REQUIRE_MOUNT:-1}"
AGE_RECIPIENT="${AGE_RECIPIENT:-}"
PUID="${PUID:-1000}"

cd "$(dirname "$0")/.."  # where docker-compose.yml is

# Never write into the empty local mount directory when the NAS is not mounted
# (accessing it first triggers a systemd automount)
ls "$BACKUP_DIR" >/dev/null
if [ "$REQUIRE_MOUNT" = 1 ] && ! mountpoint -q "$BACKUP_DIR"; then
    echo "error: $BACKUP_DIR is not mounted" >&2
    exit 1
fi
if [ -n "$AGE_RECIPIENT" ] && ! command -v age >/dev/null; then
    echo "error: AGE_RECIPIENT is set, but the age command is missing" >&2
    exit 1
fi

name="helvax-$(date +%Y-%m-%d_%H%M%S).db"
work=""
cleanup() {
    rm -f "$BACKUP_DIR/.$name.part" "$BACKUP_DIR/.$name.age.part"
    if [ -n "$work" ]; then rm -rf "$work"; fi
}
trap cleanup EXIT
trap 'exit 1' INT TERM

# Write under a temporary name and rename on success: an aborted run never leaves a broken
# file that looks like the newest backup (and the cleanup below does not match it).
# umask 077: backups contain health data and password hashes (a chmod from the host can
# fail on NFS with root_squash, the files belong to the app user)
if [ -z "$AGE_RECIPIENT" ]; then
    docker compose run --rm --no-deps -T -v "$BACKUP_DIR:/backup" notifier \
        sh -c 'umask 077 && exec flask --app helvax backup "/backup/$1"' sh ".$name.part"
    mv "$BACKUP_DIR/.$name.part" "$BACKUP_DIR/$name"
    final="$name"
else
    # The plain copy stays in a private folder on this host; only the encrypted file goes to the NAS
    work="$(mktemp -d)"
    chown "$PUID" "$work"
    docker compose run --rm --no-deps -T -v "$work:/backup" notifier \
        sh -c 'umask 077 && exec flask --app helvax backup "/backup/$1"' sh "$name"
    age -r "$AGE_RECIPIENT" -o "$BACKUP_DIR/.$name.age.part" "$work/$name"
    mv "$BACKUP_DIR/.$name.age.part" "$BACKUP_DIR/$name.age"
    final="$name.age"
fi

# Only after a successful backup, so failing runs never delete the last good copies
find "$BACKUP_DIR" -maxdepth 1 \( -name 'helvax-*.db' -o -name 'helvax-*.db.age' \) -mtime +"$KEEP_DAYS" -delete
echo "backup written: $BACKUP_DIR/$final"
