#!/bin/sh
set -e

PUID="${PUID:-0}"
PGID="${PGID:-0}"

if [ "$PUID" != "0" ]; then
    if ! grep -q "^[^:]*:[^:]*:${PUID}:" /etc/passwd; then
        echo "pm:x:${PUID}:${PGID}:pm:/tmp:/sbin/nologin" >> /etc/passwd
    fi
    if ! grep -q "^[^:]*:[^:]*:${PGID}:" /etc/group; then
        echo "pm:x:${PGID}:" >> /etc/group
    fi
    exec su-exec "${PUID}:${PGID}" "$@"
fi

exec "$@"
