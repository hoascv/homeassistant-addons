#!/usr/bin/env bash
set -e

OPTIONS=/data/options.json

export MINIO_ROOT_USER="$(jq -r '.root_user' "$OPTIONS")"
export MINIO_ROOT_PASSWORD="$(jq -r '.root_password' "$OPTIONS")"
DATA_DIR=/data/minio
mkdir -p "$DATA_DIR"

echo "[Pipeline MinIO] starting MinIO (S3 API :9000, console :9001)"
minio server "$DATA_DIR" --address ":9000" --console-address ":9001" &
MINIO_PID=$!

# Ensure the requested default buckets exist (idempotent).
BUCKETS="$(jq -r '.default_buckets // ""' "$OPTIONS")"
if [ -n "$BUCKETS" ]; then
  for _ in $(seq 1 30); do
    if mc alias set local http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
  IFS=',' read -ra ARR <<< "$BUCKETS"
  for b in "${ARR[@]}"; do
    b="$(echo "$b" | xargs)"
    [ -z "$b" ] && continue
    mc mb -p "local/$b" >/dev/null 2>&1 || true
    echo "[Pipeline MinIO] ensured bucket: $b"
  done
fi

# The console's own login (root_user/root_password) still gates access
# either way; this only opens a second door in, through Home Assistant's
# sidebar, on top of the one :9001 already provides. Its failure is not the
# S3 service's failure, so nginx runs as a best-effort layer rather than
# something the container's lifecycle is tied to — unlike Pipeline Notebook,
# where nginx is the *only* way in and its death is total failure there.
INGRESS_ENTRY="$(curl -fsSL \
  -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  http://supervisor/addons/self/info 2>/dev/null | jq -r '.data.ingress_entry // empty')"
if [ -z "$INGRESS_ENTRY" ]; then
  echo "[Pipeline MinIO] could not read the ingress path from the Supervisor;"
  echo "[Pipeline MinIO]   the sidebar panel will not work, but :9000/:9001 are unaffected"
else
  sed "s|%%INGRESS_ENTRY%%|${INGRESS_ENTRY}|g" \
      /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf
  mkdir -p /run/nginx
  # Piping `nginx -t` into anything would hide its exit status behind the
  # pipe's last command instead — with set -e active that would silently take
  # the "config is fine" branch even when it isn't. Capturing it as the `if`
  # condition itself is also what keeps set -e from aborting the whole script
  # on a failing test, which a plain assignment would not.
  if NGINX_TEST_OUTPUT="$(nginx -t 2>&1)"; then
    echo "$NGINX_TEST_OUTPUT" | sed 's/^/[Pipeline MinIO] /'
    nginx -g 'daemon off;' &
    echo "[Pipeline MinIO] console also reachable through Home Assistant's sidebar"
  else
    echo "$NGINX_TEST_OUTPUT" | sed 's/^/[Pipeline MinIO] /'
    echo "[Pipeline MinIO] nginx config invalid; the sidebar panel will not work, :9000/:9001 are unaffected"
  fi
fi

# Keep the container tied to MinIO's own process, not nginx's — the sidebar
# panel is a convenience layer on top of the S3 service, not the service
# itself.
wait "$MINIO_PID"
