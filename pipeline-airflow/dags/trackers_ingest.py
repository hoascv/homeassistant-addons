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
hostname (see `discover_base_url` in jobs/trackers_feed.py), so the trackers
need no published host port and nothing has to be kept in sync when the
repository is re-added or the add-ons are installed locally. Set `<source>_base_url` only to override that —
for a tracker somewhere else entirely.

Airflow Variables of those names work too, but the options are read first and
can't suffer a key with an invisible stray character in it.

A source with no token set fails loudly rather than skipping: a pipeline that is
quietly loading nothing looks exactly like a healthy one.
"""
from __future__ import annotations

import datetime
import json
import sys

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

SOURCES = {
    "gym_tracker": "http://172.30.32.1:8099",
    "coop_tracker": "http://172.30.32.1:8098",
}

log = logging.getLogger(__name__)


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
            if JOBS_DIR not in sys.path:
                sys.path.insert(0, JOBS_DIR)
            from trackers_feed import FeedError, discover_base_url, read_batch  # noqa: PLC0415

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
                base_url = discover_base_url(source) or default_url
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

            try:
                batch = read_batch(source, base_url, token, watermark, archive, log)
            except FeedError as exc:
                # The feed module stays Airflow-free so a test or a notebook can
                # import it; translating here is the entire cost of that.
                raise AirflowFailException(str(exc)) from exc
            # The prefix is this task's business, not the feed's: it is where
            # the archive went, which only the caller that owns the bucket knows.
            batch["prefix"] = f"s3a://{RAW_BUCKET}/{prefix}"
            return batch

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
