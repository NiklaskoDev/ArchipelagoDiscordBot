#!/bin/sh
# Container entrypoint: prepares the data folder and drops root before starting the bot.
#
# Unraid's default is the user "nobody" (99) in the group "users" (100), so files in appdata stay accessible from the
# network shares. Override with PUID/PGID. Starting the container with `--user` skips all of this.
set -eu

PUID="${PUID:-99}"
PGID="${PGID:-100}"
DATA_DIR="${DATA_DIR:-/data}"
umask "${UMASK:-022}"
export DATA_DIR HOME=/tmp

case "$PUID$PGID" in
    ''|*[!0-9]*) echo "PUID and PGID must be numbers, got PUID='$PUID' PGID='$PGID'" >&2; exit 1 ;;
esac

mkdir -p "$DATA_DIR"

if [ "$(id -u)" -ne 0 ]; then
    # Already unprivileged (docker run --user ...): we can only use what we are given.
    exec "$@"
fi

if [ "$PUID" -eq 0 ]; then
    echo "WARNING: running as root. Set PUID/PGID to an unprivileged user." >&2
    exec "$@"
fi

# Give the numeric ids a name so every library that looks the user up is happy. The names carry the id, so changing
# PUID/PGID on an existing container never collides. useradd warns that ids below 1000 are unusual; that is the point.
getent group "$PGID" >/dev/null 2>&1 || groupadd -g "$PGID" "apbot-g$PGID" 2>/dev/null \
    || { echo "Could not create a group for PGID $PGID" >&2; exit 1; }
getent passwd "$PUID" >/dev/null 2>&1 || useradd -u "$PUID" -g "$PGID" -d /tmp -M -s /usr/sbin/nologin "apbot-u$PUID" 2>/dev/null \
    || { echo "Could not create a user for PUID $PUID" >&2; exit 1; }

# Only walk the whole tree when something is actually owned by somebody else (first start, restored backups).
if [ -n "$(find "$DATA_DIR" ! -user "$PUID" -print -quit 2>/dev/null)" ] \
   || [ -n "$(find "$DATA_DIR" ! -group "$PGID" -print -quit 2>/dev/null)" ]; then
    echo "Fixing ownership of $DATA_DIR for $PUID:$PGID"
    chown -R "$PUID:$PGID" "$DATA_DIR"
fi

exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
