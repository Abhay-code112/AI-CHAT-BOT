#!/bin/sh
# Hosted volumes (Railway, Fly.io, ...) are mounted root-owned and hide the image's /data.
# Start as root only long enough to fix ownership, then drop to the unprivileged "app" user.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data
  chown -R app:app /data 2>/dev/null || true
  exec setpriv --reuid=app --regid=app --init-groups "$@"
fi
exec "$@"
