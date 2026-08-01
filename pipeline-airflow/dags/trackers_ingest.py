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

Setup: publish each tracker's port (add-on → Network), set an `api_token` in
its configuration, then set these Airflow Variables:

    gym_tracker_base_url    http://172.30.32.1:8099
    gym_tracker_api_token   <the token>
    coop_tracker_base_url   http://172.30.32.1:8098
    coop_tracker_api_token  <the token>

A source with no token set is skipped, so one tracker can run without the other.
"""
from __future__ import annotations

import datetime
import json
import urllib.request

import logging

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException
from airflow.models import Variable
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
from airflow.providers.postgres.hooks.postgres import PostgresHook

PG_CONN_ID = "pipeline_pg"
MINIO_CONN_ID = "minio_default"
SPARK_CONN_ID = "spark_default"
RAW_BUCKET = "raw"
LAKEHOUSE_ROOT = "s3a://lakehouse"
META_SCHEMA = "pipeline_meta"
MERGE_JOB = "/opt/pipeline/jobs/trackers_merge.py"

# One request per page; the feed caps this itself. Bounded so a very stale
# watermark can't turn a single run into an unbounded loop.
PAGE_SIZE = 1000
MAX_PAGES = 50
HTTP_TIMEOUT = 30

SOURCES = {
    "gym_tracker": "http://172.30.32.1:8099",
    "coop_tracker": "http://172.30.32.1:8098",
}

log = logging.getLogger(__name__)


def _get(base_url: str, token: str, path: str) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
        return json.loads(response.read().decode())


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
            token = Variable.get(f"{source}_api_token", default_var="").strip()
            if not token:
                # Fails rather than skips. A skip leaves the run green, and a
                # pipeline that is quietly loading nothing looks exactly like a
                # healthy one — the single state most worth noticing.
                raise AirflowFailException(
                    f"Airflow Variable {source}_api_token is not set, so there is nothing "
                    f"to authenticate with. Set it to the api_token from the {source} "
                    f"add-on's configuration, and {source}_base_url to the address its "
                    "port is published on."
                )
            base_url = Variable.get(f"{source}_base_url", default_var=default_url)

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

            s3 = S3Hook(aws_conn_id=MINIO_CONN_ID)
            if not s3.check_for_bucket(RAW_BUCKET):
                s3.create_bucket(bucket_name=RAW_BUCKET)
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
            probe = _get(base_url, token, f"/api/changes?since={watermark}&limit=1")
            if watermark == 0 or probe.get("full_reload_required"):
                snapshot = _get(base_url, token, "/api/export")
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
                page = _get(base_url, token, f"/api/changes?since={since}&limit={PAGE_SIZE}")
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

        merge = SparkSubmitOperator(
            task_id="merge_into_delta",
            conn_id=SPARK_CONN_ID,
            application=MERGE_JOB,
            deploy_mode="cluster",
            name=f"trackers-merge-{source}",
            application_args=[source, "{{ ti.xcom_pull(task_ids='fetch')['prefix'] }}", LAKEHOUSE_ROOT],
        )

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

        gate >> merge >> advance_watermark(batch)

    return ingest()


for _source, _url in SOURCES.items():
    build_dag(_source, _url)
