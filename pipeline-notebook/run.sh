#!/usr/bin/env bash
set -e

OPTIONS=/data/options.json
opt() { jq -r "$1" "$OPTIONS"; }

NOTEBOOK_ROOT=/share/pipeline-airflow
LIB_DIR="$NOTEBOOK_ROOT/lib"

# Ingress hands requests to us under a generated path, and JupyterLab builds its
# own asset URLs from base_url — get that wrong and the page loads as a blank
# frame with 404s for every script. The path is only knowable at runtime, so ask
# the Supervisor for it rather than guessing.
INGRESS_ENTRY="$(curl -fsSL \
  -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
  http://supervisor/addons/self/info 2>/dev/null | jq -r '.data.ingress_entry // empty')"
if [ -z "$INGRESS_ENTRY" ]; then
  echo "[Pipeline Notebook] could not read the ingress path from the Supervisor;"
  echo "[Pipeline Notebook]   the UI will not load until this works"
  INGRESS_ENTRY="/"
fi

# The pipeline's own modules, published by the Pipeline Airflow add-on and
# refreshed there on every start. On PYTHONPATH so a notebook imports exactly
# what the scheduler runs, with no sys.path line to remember.
export PYTHONPATH="$LIB_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Settings and workspaces under /data so a restart doesn't reset the layout.
mkdir -p /data/user-settings /data/workspaces

if [ ! -d "$NOTEBOOK_ROOT" ]; then
  echo "[Pipeline Notebook] $NOTEBOOK_ROOT does not exist yet."
  echo "[Pipeline Notebook]   Start the Pipeline Airflow add-on once: it creates"
  echo "[Pipeline Notebook]   dags/, lib/ and notebooks/ there."
  mkdir -p "$NOTEBOOK_ROOT"
fi

# Everything a notebook needs to reach the rest of the stack, so a fresh
# notebook can query the lakehouse without being told any addresses.
export SPARK_CONNECT_URL="$(opt '.spark_connect_url // "sc://172.30.32.1:15002"')"
export MINIO_ENDPOINT="$(opt '.minio_endpoint // "http://172.30.32.1:9000"')"
export MINIO_ACCESS_KEY="$(opt '.minio_access_key')"
export MINIO_SECRET_KEY="$(opt '.minio_secret_key')"
export GYM_TRACKER_API_TOKEN="$(opt '.gym_tracker_api_token // ""')"
export COOP_TRACKER_API_TOKEN="$(opt '.coop_tracker_api_token // ""')"

echo "[Pipeline Notebook] serving $NOTEBOOK_ROOT at ingress path $INGRESS_ENTRY"
echo "[Pipeline Notebook] lib on PYTHONPATH: $LIB_DIR"

# Authentication is Home Assistant's, not Jupyter's. `ingress: true` with no
# `ports:` section means the only route in is through Home Assistant, which has
# already authenticated the user before the request reaches this container; a
# second Jupyter login would add a password to manage without adding protection.
# This mirrors how the community JupyterLab add-on is configured. It is only
# sound *because* the port is unpublished — if a `ports:` entry is ever added
# here, a token becomes mandatory, since a notebook server executes arbitrary
# code as root on the Home Assistant host.
exec jupyter lab \
  --ip=0.0.0.0 \
  --port=8888 \
  --no-browser \
  --allow-root \
  --ServerApp.base_url="$INGRESS_ENTRY" \
  --ServerApp.root_dir="$NOTEBOOK_ROOT" \
  --ServerApp.token='' \
  --ServerApp.password='' \
  --ServerApp.allow_origin='*' \
  --ServerApp.allow_remote_access=True \
  --ServerApp.trust_xheaders=True \
  --LabApp.user_settings_dir=/data/user-settings \
  --LabApp.workspaces_dir=/data/workspaces
