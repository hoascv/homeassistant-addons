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

## Flattening without a schema

The payloads are dynamic: an export holds a map of table name to rows, and any
release may add a column. That looks like a choice between schema inference
(fragile, as above) and parsing on the driver (not Spark at all). Neither is
needed, because of one documented Jackson behaviour:

    asked for a StringType but finding an object or an array, from_json
    re-serialises that value back to its raw JSON text

So `map<string,string>` over `$.tables` yields table name → the rows array as
JSON text, and `array<string>` over that yields one row per element, still as
JSON text. Nothing is inferred, nothing is typed, and every step is a Spark
expression evaluated on the executors.

The one value that has to be read *out* of a row — its key column, whose name
differs per table — is plucked with `element_at` over a `map<string,string>`
view of the row. That view serves the lookup only; `data` keeps the untouched
JSON.

One caveat: each archived response is a single JSON document, so a file cannot
be split and its parse is one task. A bootstrap (one large export) is therefore
parsed by a single executor, while an incremental run spreads across its pages.
Everything after the parse — dedupe, merge, write — is distributed. If a
bootstrap ever outgrows one executor, the fix is for `fetch` to archive JSON
Lines instead of one document per response.

Usage:
    trackers_merge.py <source> <raw_prefix> <lakehouse_root>
e.g. trackers_merge.py gym_tracker s3a://raw/gym_tracker/20260731T210000Z \\
                       s3a://lakehouse
"""
import sys

from delta.tables import DeltaTable
from pyspark.sql import SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql.types import LongType, StringType

# What lands in Delta: the row's payload as JSON plus the change metadata
# needed to merge idempotently and to tell a deleted row from a live one.
# `actor` is user, automation or migration — null for a snapshot, which is a
# state rather than a change, and for changes recorded before the trackers
# began tracking it.
CHANGE_COLUMNS = ("table", "row_id", "data", "seq", "changed_at", "actor", "op")


def _changes(raw):
    """Rows from `/api/changes` pages.

    The envelope's own fields are scalars, so get_json_object reads them
    directly; `$.row` is an object and comes back as the JSON text we store.
    A delete carries a null row, and stays null here.
    """
    return (
        raw.select(F.get_json_object("value", "$.changes").alias("changes"))
        .where(F.col("changes").isNotNull())
        .select(F.explode(F.from_json("changes", "array<string>")).alias("change"))
        .select(
            F.get_json_object("change", "$.table").alias("table"),
            F.get_json_object("change", "$.row_id").alias("row_id"),
            F.get_json_object("change", "$.row").alias("data"),
            F.get_json_object("change", "$.seq").cast(LongType()).alias("seq"),
            F.get_json_object("change", "$.changed_at").alias("changed_at"),
            F.get_json_object("change", "$.actor").alias("actor"),
            F.get_json_object("change", "$.op").alias("op"),
        )
    )


def _snapshot(raw):
    """Rows from an `/api/export` snapshot, carrying the key column per table.

    The export names the key column for each table. Never guess it from the
    payload: the JSON has its keys sorted, so the id is rarely first — and for
    challenge_completions the first key (day) repeats across rows, which would
    collapse 81 rows into 29.
    """
    exports = raw.select(
        F.get_json_object("value", "$.max_seq").cast(LongType()).alias("seq"),
        F.from_json(F.get_json_object("value", "$.tables"), "map<string,string>").alias(
            "tables"
        ),
        F.from_json(F.get_json_object("value", "$.keys"), "map<string,string>").alias(
            "keys"
        ),
    ).where(F.col("tables").isNotNull())

    per_table = (
        exports.select("seq", "keys", F.explode("tables"))
        .withColumnRenamed("key", "table")
        .withColumnRenamed("value", "rows")
    )
    return per_table.select(
        "seq",
        "table",
        F.element_at("keys", F.col("table")).alias("key"),
        F.explode(F.from_json("rows", "array<string>")).alias("data"),
    ).select(
        F.col("table"),
        F.element_at(F.from_json("data", "map<string,string>"), F.col("key")).alias(
            "row_id"
        ),
        F.col("data"),
        F.col("seq"),
        F.lit(None).cast(StringType()).alias("changed_at"),
        F.lit(None).cast(StringType()).alias("actor"),
        # A snapshot is state, not a change, so every row reads as an insert.
        F.lit("I").alias("op"),
        F.col("key"),
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

    # wholetext, because each archived response is one JSON document. The feed
    # is written compactly and so has no embedded newlines, but relying on that
    # would make the read depend on how the archive happens to be formatted.
    raw = spark.read.text(f"{raw_prefix}/*.json", wholetext=True)

    snapshot = _snapshot(raw).cache()
    # An export naming no key for a table is a version mismatch, not a row to
    # skip: without it every row of that table would merge onto a null id.
    unnamed = sorted(
        row["table"]
        for row in snapshot.where(F.col("key").isNull())
        .select("table")
        .distinct()
        .collect()
    )
    if unnamed:
        raise ValueError(
            f"export did not name the key column for {', '.join(unnamed)}; "
            "the add-on needs to be new enough to send `keys`"
        )

    rows = (
        _changes(raw)
        .unionByName(snapshot.drop("key").select(*CHANGE_COLUMNS))
        .cache()
    )

    # The only thing collected to the driver: the table names, because each one
    # is written to its own Delta path. Tens of strings, never the data.
    tables = sorted(row["table"] for row in rows.select("table").distinct().collect())
    if not tables:
        print(f"[trackers_merge] nothing to merge for {source}")
        spark.stop()
        return

    for table in tables:
        target_path = f"{lakehouse_root}/{source}/{table}"
        updates = (
            rows.where(F.col("table") == table)
            .drop("table")
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
