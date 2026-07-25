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

# Keep the container tied to the MinIO process.
wait "$MINIO_PID"
