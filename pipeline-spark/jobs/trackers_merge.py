"""Merge a Gym/Coop Tracker change batch into Delta tables on MinIO.

Reads the raw feed responses Airflow archived under `s3a://raw/<source>/<run>/`
and applies them to one Delta table per source table at
`s3a://lakehouse/<source>/<table>`.

MERGE is the point of using Delta here. The feed is change data: the same row
can be inserted and later updated or deleted, and a batch can contain several
changes to one row. Merging on `row_id`, guarded by `seq`, makes applying a
batch idempotent — re-running after a failure converges on the same state
rather than double-counting.

Deletes are soft. The feed reports them because these apps really do delete
(un-ticking a challenge removes the tick and the workout it logged), and
"logged then taken back" is itself worth analysing, so the row keeps its last
known state with `deleted_at` set.

`data` stays a JSON string rather than exploded into typed columns. Both apps
gain columns regularly — several during a single afternoon — and inferring a
schema per batch would eventually produce two batches that disagree about a
column's type and fail. Parse it downstream with from_json and an explicit
schema for the tables you actually query.

Usage:
    trackers_merge.py <source> <raw_prefix> <lakehouse_root>
e.g. trackers_merge.py gym_tracker s3a://raw/gym_tracker/20260731T210000Z \\
                       s3a://lakehouse
"""
import json
import sys

from delta.tables import DeltaTable
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import (
    LongType,
    StringType,
    StructField,
    StructType,
)

# What lands in Delta: the row's payload as JSON plus the change metadata
# needed to merge idempotently and to tell a deleted row from a live one.
CHANGE_SCHEMA = StructType(
    [
        StructField("row_id", StringType(), False),
        StructField("data", StringType(), True),
        StructField("seq", LongType(), False),
        StructField("changed_at", StringType(), True),
        # user, automation or migration — null for changes recorded before the
        # trackers began tracking it.
        StructField("actor", StringType(), True),
        StructField("op", StringType(), False),
    ]
)


def _rows_from_payload(payload):
    """Flatten one archived response into (table, change-row) pairs.

    Handles both shapes the feed produces: a bootstrap snapshot of whole
    tables, and a page of individual changes.
    """
    if "tables" in payload:  # /api/export
        seq = payload.get("max_seq", 0)
        # The export names the key column per table. Never guess it from the
        # payload: the JSON has its keys sorted, so the id is rarely first —
        # and for challenge_completions the first key (day) repeats across
        # rows, which would merge 81 rows into 29.
        keys = payload.get("keys") or {}
        for table, rows in payload["tables"].items():
            key = keys.get(table)
            if not key:
                raise ValueError(
                    f"export did not name the key column for {table!r}; "
                    "the add-on needs to be new enough to send `keys`"
                )
            for row in rows:
                # A snapshot has no single actor: it is the state, not a change.
                yield table, (str(row[key]), json.dumps(row), seq, None, None, "I")
    else:  # /api/changes
        for change in payload.get("changes", []):
            yield change["table"], (
                str(change["row_id"]),
                json.dumps(change["row"]) if change["row"] is not None else None,
                int(change["seq"]),
                change.get("changed_at"),
                change.get("actor"),
                change["op"],
            )


def main(source, raw_prefix, lakehouse_root):
    spark = (
        SparkSession.builder.appName(f"trackers-merge-{source}")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .getOrCreate()
    )

    # The archived responses are whole JSON documents, so they're read as text
    # and parsed on the driver: a batch is a few hundred rows, and this keeps
    # the flattening in plain Python rather than nested-column gymnastics.
    raw = spark.sparkContext.wholeTextFiles(f"{raw_prefix}/*.json").collect()
    by_table = {}
    for _, text in raw:
        for table, row in _rows_from_payload(json.loads(text)):
            by_table.setdefault(table, []).append(row)

    if not by_table:
        print(f"[trackers_merge] nothing to merge for {source}")
        spark.stop()
        return

    for table, rows in sorted(by_table.items()):
        target_path = f"{lakehouse_root}/{source}/{table}"
        updates = (
            spark.createDataFrame(rows, schema=CHANGE_SCHEMA)
            # Several changes to one row in a batch: MERGE requires at most one
            # source row per key, so keep the latest by seq.
            .withColumn(
                "_rank",
                F.row_number().over(
                    Window.partitionBy("row_id").orderBy(F.col("seq").desc())
                ),
            )
            .filter(F.col("_rank") == 1)
            .drop("_rank")
            .withColumn("deleted_at", F.when(F.col("op") == "D", F.current_timestamp()))
            .withColumn("loaded_at", F.current_timestamp())
            .drop("op")
        )

        if DeltaTable.isDeltaTable(spark, target_path):
            (
                DeltaTable.forPath(spark, target_path)
                .alias("t")
                .merge(updates.alias("s"), "t.row_id = s.row_id")
                # Guarded by seq so replaying an old batch can't undo newer
                # state — which is what makes a re-run safe.
                .whenMatchedUpdateAll(condition="s.seq >= t.seq")
                .whenNotMatchedInsertAll()
                .execute()
            )
            action = "merged"
        else:
            updates.write.format("delta").mode("overwrite").save(target_path)
            action = "created"
        print(f"[trackers_merge] {action} {updates.count()} row(s) into {target_path}")

    spark.stop()


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(
            "usage: trackers_merge.py <source> <raw_prefix> <lakehouse_root>"
        )
    main(sys.argv[1], sys.argv[2], sys.argv[3])
