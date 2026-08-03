"""Incremental load of Gym Tracker and Coop Tracker into Delta tables.

Both add-ons expose a change feed: `/api/export` for a full snapshot with the
sequence it corresponds to, and `/api/changes?since=` for everything after a
watermark. Each change carries the row's current state, or null for a delete —
the part a "last modified" column could never express, and these apps really do
delete (un-ticking a challenge removes the tick *and* the workout it logged).

One DAG per source, each:

  1. **fetch** — read the watermark, pull a bootstrap or a set of delta pages,
     and archive every raw response to MinIO before anything interprets it. If
     the feed reports the watermark has aged out of its retention window, fall
     back to a bootstrap.
  2. **merge** — submit the Spark job that MERGEs the batch into
     `s3a://lakehouse/<source>/<table>`. Merging on `row_id` guarded by `seq`
     makes this idempotent, so a retry converges rather than double-counting.
  3. **advance_watermark** — only after the merge succeeds. A failure therefore
     re-runs the batch instead of stepping over it.

Setup, per tracker: set an `api_token` in its configuration, and put the same
value in the matching option on the Pipeline Airflow add-on —
`gym_tracker_api_token` / `coop_tracker_api_token`.

That is the whole of it. The address is worked out from this add-on's own
hostname (see `_discover_base_url`), so the trackers need no published host port
and nothing has to be kept in sync when the repository is re-added or the
add-ons are installed locally. Set `<source>_base_url` only to override that —
for a tracker somewhere else entirely.

Airflow Variables of those names work too, but the options are read first and
can't suffer a key with an invisible stray character in it.

A source with no token set fails loudly rather than skipping: a pipeline that is
quietly loading nothing looks exactly like a healthy one.
"""
from __future__ import annotations

import datetime
import json
import socket
import sys
import urllib.error
import urllib.request

import logging

# airflow.sdk, not airflow.models/decorators/exceptions. In Airflow 3 a task
# runs in a worker process with no metadata-database access, and the legacy
# `airflow.models.Variable.get` cannot see Variables from there — it warns, then
# quietly returns the default, so a token that is set reads back as unset. The
# SDK's Variable resolves through the execution API and does see them.
from airflow.sdk import Variable, dag, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.postgres.hooks.postgres import PostgresHook

PG_CONN_ID = "pipeline_pg"
MINIO_CONN_ID = "minio_default"
RAW_BUCKET = "raw"
LAKEHOUSE_BUCKET = "lakehouse"
LAKEHOUSE_ROOT = f"s3a://{LAKEHOUSE_BUCKET}"
META_SCHEMA = "pipeline_meta"
# Where the merge module lives. Not on the DAGs path on purpose: importing it
# pulls in pyspark, and the dag-processor re-parses this folder constantly.
JOBS_DIR = "/opt/pipeline/jobs"
# The Spark Connect endpoint of the Pipeline Spark add-on. Overridable with a
# `spark_connect_url` Variable for a cluster somewhere else.
SPARK_CONNECT_URL = "sc://172.30.32.1:15002"

# One request per page; the feed caps this itself. Bounded so a very stale
# watermark can't turn a single run into an unbounded loop.
PAGE_SIZE = 1000
MAX_PAGES = 50
HTTP_TIMEOUT = 30

SOURCES = {
    "gym_tracker": "http://172.30.32.1:8099",
    "coop_tracker": "http://172.30.32.1:8098",
}

# Both trackers listen on this inside their own containers, always — it is only
# the *host* port that has to differ, and reaching them by hostname sidesteps
# host ports entirely.
TRACKER_PORT = 8099
OUR_SLUG = "pipeline-airflow"

log = logging.getLogger(__name__)


def _discover_base_url(source: str) -> str | None:
    """Where the tracker add-on lives, worked out rather than configured.

    Add-ons on the Supervisor network resolve each other by hostname, and every
    add-on from one repository shares a prefix: this container is
    `<prefix>-pipeline-airflow`, so the trackers are `<prefix>-gym-tracker` and
    `<prefix>-coop-tracker`. Taking the prefix from our *own* hostname means it
    survives the things that would otherwise silently break a pinned address —
    the prefix is the repository's hash, so it changes if the repository is
    re-added, and it is `local` for a locally installed copy. Either way we move
    with it, because we are named the same way.

    It also means the trackers need no published host port at all, so their API
    is never exposed on the LAN with only an api_token in front of it.

    Returns None when the hostname doesn't look like an add-on's, e.g. running
    outside Supervisor; the caller then falls back to the configured default.
    """
    host = socket.gethostname().split(".")[0]
    suffix = f"-{OUR_SLUG}"
    if not host.endswith(suffix) or host == suffix:
        return None
    prefix = host[: -len(suffix)]
    return f"http://{prefix}-{source.replace('_', '-')}:{TRACKER_PORT}"


def _get(source: str, base_url: str, token: str, path: str) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # 401 is worth naming: it means we reached the add-on and it rejected
        # the token, which is a different fix from not reaching it at all.
        if exc.code in (401, 403):
            raise AirflowFailException(
                f"{url} rejected the token ({exc.code}). The {source}_api_token option "
                f"must match the api_token in the {source} add-on's own configuration."
            ) from exc
        raise AirflowFailException(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        # "Connection refused" on its own doesn't say what was dialled, which is
        # exactly the thing you need to know.
        raise AirflowFailException(
            f"Could not reach {url} ({exc.reason}). Check the {source} add-on is running. "
            f"If {source}_base_url is set, it must name a host port that add-on actually "
            "publishes under its Network settings; leaving the option blank is usually "
            "better, as the address is then derived from this add-on's own hostname and "
            "needs no published port at all."
        ) from exc


def _ensure_meta(cursor) -> None:
    cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {META_SCHEMA}")
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {META_SCHEMA}.source_watermark (
            source     text PRIMARY KEY,
            seq        bigint NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def build_dag(source: str, default_url: str):
    @dag(
        dag_id=f"{source}_ingest",
        schedule="@hourly",
        start_date=datetime.datetime(2026, 1, 1),
        catchup=False,
        max_active_runs=1,  # the watermark makes overlapping runs meaningless
        tags=["trackers", "delta", source],
        doc_md=__doc__,
    )
    def ingest():
        @task
        def fetch() -> dict:
            # `missing` and `set but empty` are different problems with the same
            # symptom, and telling them apart matters: a Variable that is listed
            # in the UI but reads as missing here means the *key* differs from
            # what is asked for — a trailing space in the name is invisible in
            # the list and defeats an exact lookup. A default would flatten both
            # cases into one unhelpful message.
            key = f"{source}_api_token"
            sentinel = object()
            raw_token = Variable.get(key, default=sentinel)
            if raw_token is sentinel:
                # Fails rather than skips. A skip leaves the run green, and a
                # pipeline that is quietly loading nothing looks exactly like a
                # healthy one — the single state most worth noticing.
                raise AirflowFailException(
                    f"No Airflow Variable named exactly {key!r} exists. The reliable place "
                    f"to set it is the Pipeline Airflow add-on's own configuration: put the "
                    f"{source} add-on's api_token in the {key} option and restart. That is "
                    "read ahead of the web UI's Variables, so it also works when a Variable "
                    "typed into the UI has a stray character in its key — which looks "
                    "identical in the list and cannot be looked up."
                )
            token = (raw_token or "").strip()
            if not token:
                raise AirflowFailException(
                    f"Airflow Variable {key!r} exists but is empty. Set it to the api_token "
                    f"from the {source} add-on's configuration."
                )
            # Configured wins; otherwise find it. Left blank on purpose is the
            # normal case, and means "work it out" rather than "no address".
            base_url = (Variable.get(f"{source}_base_url", default="") or "").strip()
            if base_url:
                log.info("%s: using the configured address %s", source, base_url)
            else:
                base_url = _discover_base_url(source) or default_url
                log.info("%s: no address configured, using %s", source, base_url)

            pg = PostgresHook(postgres_conn_id=PG_CONN_ID)
            with pg.get_conn() as connection, connection.cursor() as cursor:
                _ensure_meta(cursor)
                cursor.execute(
                    f"SELECT seq FROM {META_SCHEMA}.source_watermark WHERE source = %s",
                    (source,),
                )
                row = cursor.fetchone()
                watermark = int(row[0]) if row else 0
                connection.commit()

            # Both buckets, not just the one this task writes to. Spark creates
            # objects but never buckets, so a missing `lakehouse` surfaces much
            # later as an S3A UnknownStoreException from inside the merge, long
            # after the batch has been fetched and archived.
            s3 = S3Hook(aws_conn_id=MINIO_CONN_ID)
            for bucket in (RAW_BUCKET, LAKEHOUSE_BUCKET):
                if not s3.check_for_bucket(bucket):
                    log.info("creating bucket %s", bucket)
                    s3.create_bucket(bucket_name=bucket)
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            prefix = f"{source}/{stamp}"

            def archive(name: str, payload: dict) -> None:
                s3.load_string(
                    json.dumps(payload, separators=(",", ":")),
                    key=f"{prefix}/{name}",
                    bucket_name=RAW_BUCKET,
                    replace=True,
                )

            # A first run, or a watermark the feed can no longer bridge.
            probe = _get(source, base_url, token, f"/api/changes?since={watermark}&limit=1")
            if watermark == 0 or probe.get("full_reload_required"):
                snapshot = _get(source, base_url, token, "/api/export")
                archive("export.json", snapshot)
                rows = sum(len(v) for v in snapshot["tables"].values())
                log.info(
                    "bootstrap: %s rows across %s tables, up to seq %s (watermark was %s)",
                    rows, len(snapshot["tables"]), snapshot["max_seq"], watermark,
                )
                return {
                    "mode": "bootstrap",
                    "prefix": f"s3a://{RAW_BUCKET}/{prefix}",
                    "max_seq": snapshot["max_seq"],
                    "from_seq": watermark,
                    "pages": 1,
                }

            since, pages, changes_seen = watermark, 0, 0
            while pages < MAX_PAGES:
                page = _get(source, base_url, token, f"/api/changes?since={since}&limit={PAGE_SIZE}")
                changes = page.get("changes") or []
                if not changes:
                    break
                archive(f"changes-{since}.json", page)
                since = changes[-1]["seq"]
                changes_seen += len(changes)
                pages += 1
                if len(changes) < PAGE_SIZE:
                    break
            log.info(
                "incremental: %s change(s) over %s page(s), seq %s -> %s",
                changes_seen, pages, watermark, since,
            )
            return {
                "mode": "incremental",
                "prefix": f"s3a://{RAW_BUCKET}/{prefix}",
                "max_seq": since,
                "from_seq": watermark,
                "pages": pages,
            }

        @task.short_circuit
        def has_work(batch: dict) -> bool:
            # Nothing new is a healthy outcome, not a problem — skip the Spark
            # submit rather than pay for a cluster round trip to merge an empty
            # batch. Said out loud so a run that does nothing says so.
            if batch["pages"]:
                return True
            log.info(
                "nothing new since seq %s — no merge to run", batch["from_seq"]
            )
            return False

        batch = fetch()
        gate = has_work(batch)

        @task(task_id="merge_into_delta")
        def merge_into_delta(batch: dict) -> list:
            # Spark Connect. No spark-submit, no local driver: this task is a
            # gRPC client and the driver runs in the Pipeline Spark add-on,
            # which is where the Delta jars and the MinIO credentials already
            # live. Standalone cannot run a PySpark driver in cluster mode, and
            # the alternative — a client-mode driver in *this* container — put
            # the driver's heap beside the scheduler and required the executors
            # to call back across the add-on boundary.
            #
            # Imported inside the task so the dag-processor doesn't pay for
            # pyspark on every parse.
            if JOBS_DIR not in sys.path:
                sys.path.insert(0, JOBS_DIR)
            from trackers_merge import merge_batch  # noqa: PLC0415
            from pyspark.sql import SparkSession  # noqa: PLC0415

            remote = Variable.get("spark_connect_url", default=SPARK_CONNECT_URL)
            spark = (
                SparkSession.builder.appName(f"trackers-merge-{source}")
                .remote(remote)
                .getOrCreate()
            )
            try:
                written = merge_batch(spark, source, batch["prefix"], LAKEHOUSE_ROOT)
            finally:
                spark.stop()

            # The job's own prints go to the Connect server's log, not here, so
            # the run summary is reported from the client side instead.
            if not written:
                log.info("nothing to merge for %s", source)
            for entry in written:
                log.info(
                    "%s %s row(s) into %s/%s/%s",
                    entry["action"], entry["rows"], LAKEHOUSE_ROOT, source, entry["table"],
                )
            return written

        @task
        def advance_watermark(batch: dict) -> dict:
            # Deliberately after the merge: if that fails, the watermark stays
            # put and the next run re-reads the same batch, which the merge is
            # idempotent against.
            pg = PostgresHook(postgres_conn_id=PG_CONN_ID)
            with pg.get_conn() as connection, connection.cursor() as cursor:
                _ensure_meta(cursor)
                cursor.execute(
                    f"INSERT INTO {META_SCHEMA}.source_watermark (source, seq, updated_at) "
                    "VALUES (%s, %s, now()) ON CONFLICT (source) DO UPDATE SET "
                    "seq = EXCLUDED.seq, updated_at = now()",
                    (source, batch["max_seq"]),
                )
                connection.commit()
            log.info(
                "%s complete: %s, watermark %s -> %s",
                source, batch["mode"], batch["from_seq"], batch["max_seq"],
            )
            return {"source": source, "seq": batch["max_seq"], "mode": batch["mode"]}

        gate >> merge_into_delta(batch) >> advance_watermark(batch)

    return ingest()


for _source, _url in SOURCES.items():
    build_dag(_source, _url)
