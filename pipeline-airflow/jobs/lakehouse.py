"""Reading the tracker Delta tables as something you can actually analyse.

The merge keeps each row's payload as a JSON *string* on purpose: both trackers
gain columns regularly, and inferring a schema per batch would eventually
produce two batches that disagree about a type and fail the write. The cost is
paid here instead, once, with an explicit schema per table — which is the right
place, because a reader can ignore columns it doesn't care about and tolerate
ones that don't exist yet.

    from lakehouse import table, tables
    workouts = table(spark, "gym_tracker", "workout_logs")
    workouts.orderBy("ts", ascending=False).show()

With the optional Pipeline Metastore add-on running, `register(spark)` gives
the same tables names, and SQL becomes an alternative to the functions here:

    SELECT count(*) FROM gym_tracker.workout_logs_typed

Every table also carries the change metadata the merge wrote: `seq`,
`changed_at`, `actor` (user / automation / migration) and `deleted_at`. By
default `table()` gives you the live rows with the payload flattened; pass
`include_deleted=True` to see what was taken back, which is often the
interesting question — un-ticking a challenge really does delete the workout it
logged.

A column added to a tracker after this file was written simply won't appear
until it is added below; nothing breaks, and `raw()` always has the untouched
JSON if you need something in a hurry.
"""
from __future__ import annotations

import os

from pyspark.sql import functions as F

# Where the Delta tables live. Overridable two ways, because both come up: set
# LAKEHOUSE_ROOT in the environment (the Notebook add-on could), or reassign
# `lakehouse.LAKEHOUSE_ROOT` in a session — pointing at a local copy while
# trying something out is the common case. Resolved per call rather than
# captured as a default argument, or reassigning it would silently do nothing.
LAKEHOUSE_ROOT = os.environ.get("LAKEHOUSE_ROOT", "s3a://lakehouse")


def _root(root=None):
    return root or LAKEHOUSE_ROOT


# Taken from the trackers' own SQLite schemas. SQLite's INTEGER/REAL/TEXT map to
# long/double/string; anything absent from a row parses as null rather than
# failing, which is what makes this survive the apps changing.
SCHEMAS = {
    "gym_tracker": {
        "workout_logs": (
            "id long, ts string, exercise_id long, sets long, reps long, "
            "weight_kg double, duration_sec long, notes string, source string, "
            "challenge_item_id long, hr_avg long, hr_max long, hr_min long, "
            "hr_samples long, hr_synced_at string, hr_attempts long, "
            "ts_exact long, session_start string, session_end string"
        ),
        "weight_logs": (
            "id long, ts string, weight_kg double, body_fat_pct double, "
            "notes string, device string, ts_exact long"
        ),
        "exercises": (
            "id long, name string, equipment string, category string, "
            "is_custom long, archived long, notes string, measure string, "
            "created_at string, updated_at string"
        ),
        "challenges": (
            "id long, name string, start_date string, end_date string, "
            "sort_order long, archived long, created_at string, updated_at string, "
            "repeat_of long, schedule_kind string, schedule_interval long, "
            "schedule_weekdays string"
        ),
        "challenge_items": (
            "id long, label string, sort_order long, archived long, "
            "item_type string, exercise_id long, supplement_id long, "
            "target_sets long, target_reps long, target_seconds long, dose string, "
            "challenge_id long, archived_at string, moved_from long, "
            "joined_on string, created_at string, updated_at string"
        ),
        "challenge_completions": "id long, item_id long, day string, ts string",
        "garmin_daily": (
            "day string, sleep_seconds long, sleep_deep_seconds long, "
            "sleep_light_seconds long, sleep_rem_seconds long, "
            "sleep_awake_seconds long, sleep_score long, stress_avg long, "
            "stress_max long, body_battery_high long, body_battery_low long, "
            "body_battery_charged long, body_battery_drained long, "
            "resting_hr long, synced_at string"
        ),
        "garmin_activities": (
            "activity_id long, start_time string, activity_type string, "
            "name string, duration_sec long, distance_m double, calories long, "
            "avg_hr long, max_hr long, synced_at string"
        ),
        "supplements": (
            "id long, name string, dose string, dose_amount double, "
            "dose_unit string, quantity double, timing string, brand string, "
            "is_custom long, archived long, created_at string, updated_at string"
        ),
        "goal": (
            "id long, target_date string, target_weight_kg double, "
            "target_body_fat_pct double, start_date string, start_weight_kg double"
        ),
        "goal_history": (
            "id long, changed_at string, source string, target_date string, "
            "target_weight_kg double, target_body_fat_pct double, "
            "start_date string, start_weight_kg double"
        ),
    },
    "coop_tracker": {
        # `type` distinguishes an egg collection from a feed/expense entry, so
        # most questions start by filtering on it.
        "logs": (
            "id long, type string, ts string, count long, food_type string, "
            "amount string, notes string, price double, cost double, "
            "category string, container_empty long, given_away long, "
            "egg_sizes string"
        ),
        "chickens": "id long, name string, breed string, hatch_date string, status string",
        "breeds": "id long, name string, annual_eggs long",
        "food_types": "id long, name string",
        "health_events": (
            "id long, chicken_id long, event_type string, event_date string, "
            "weight_grams long, notes string, created_at string"
        ),
        "nesting_boxes": "id long, name string, width_mm double, created_at string",
    },
}


def raw(spark, source, name, root=None):
    """The Delta table exactly as the merge wrote it, payload still JSON."""
    return spark.read.format("delta").load(f"{_root(root)}/{source}/{name}")


def table(spark, source, name, include_deleted=False, root=None):
    """A table with its payload flattened into columns.

    Keeps the change metadata alongside: `_seq`, `_changed_at`, `_actor` and
    `_deleted_at`, underscore-prefixed so they can't collide with a column the
    tracker adds later.
    """
    schema = SCHEMAS.get(source, {}).get(name)
    if schema is None:
        raise KeyError(
            f"no schema for {source}.{name}; known: "
            f"{', '.join(sorted(SCHEMAS.get(source, {})))}. "
            "Use raw() to read it untyped."
        )
    df = raw(spark, source, name, root)
    if not include_deleted:
        df = df.where(F.col("deleted_at").isNull())
    return (
        df.select(
            F.from_json("data", schema).alias("d"),
            F.col("seq").alias("_seq"),
            F.col("changed_at").alias("_changed_at"),
            F.col("actor").alias("_actor"),
            F.col("deleted_at").alias("_deleted_at"),
        )
        .select("d.*", "_seq", "_changed_at", "_actor", "_deleted_at")
    )


def tables(spark, source, root=None):
    """Every table with a known schema, as a dict — handy for a notebook:

        gym = tables(spark, "gym_tracker")
        gym["workout_logs"].count()
    """
    known = SCHEMAS.get(source, {})
    out, failures = {}, {}
    for name in known:
        try:
            out[name] = table(spark, source, name, root=root)
        except Exception as exc:  # noqa: BLE001 - usually a table not loaded yet
            failures[name] = exc
    # One missing table means the DAG hasn't loaded it. *Every* table missing
    # means something systemic — no Delta jars, the wrong root, a session that
    # can't reach MinIO — and silently returning {} would send you looking for
    # the wrong problem entirely.
    if known and not out:
        raise RuntimeError(
            f"could not read any {source} table under {_root(root)}. First error: "
            f"{failures[next(iter(failures))]}"
        )
    return out


def catalog_available(spark):
    """True when this session is wired to a Hive metastore.

    The Pipeline Metastore add-on is optional and the Spark add-on ships with
    it switched off, so anything catalog-shaped has to ask first rather than
    assume. Everything else in this module works either way — the paths below
    are what the DAGs write, with or without a catalog to name them.
    """
    try:
        return spark.conf.get("spark.sql.catalogImplementation") == "hive"
    except Exception:  # noqa: BLE001 - unset conf, or a session that can't be asked
        return False


def register(spark, source=None, root=None, typed_views=True):
    """Give the Delta tables names, so SQL can find them without a path.

        register(spark)                      # every source
        spark.sql("SELECT count(*) FROM gym_tracker.workout_logs_typed")

    Registration is metadata only: `CREATE TABLE ... LOCATION` over an existing
    Delta directory moves and rewrites nothing, and Delta keeps the schema in
    its own transaction log, so a tracker gaining a column needs no re-run.

    Two names per table, because the raw shape is rarely what you want to
    query. `<name>` is the table as the merge wrote it, payload still a JSON
    string; `<name>_typed` is a view applying the same schema, live-row filter
    and `_`-prefixed change metadata that `table()` gives you in Python.

    Tables that aren't in the lakehouse yet are skipped, not fatal — a DAG that
    hasn't run is the usual reason. Returns {"registered": [...], "skipped": []}.
    """
    if not catalog_available(spark):
        raise RuntimeError(
            "this session has no Hive catalog, so there is nowhere to register "
            "tables. Install the Pipeline Metastore add-on and set the Pipeline "
            "Spark add-on's metastore_uris (thrift://172.30.32.1:9083)."
        )

    sources = [source] if source else sorted(SCHEMAS)
    registered, skipped = [], []
    for src in sources:
        spark.sql(f"CREATE DATABASE IF NOT EXISTS {src}")
        for name, schema in SCHEMAS.get(src, {}).items():
            location = f"{_root(root)}/{src}/{name}"
            try:
                # Cheapest honest existence check: Delta refuses a path with no
                # transaction log, which is exactly the "not loaded yet" case.
                spark.read.format("delta").load(location).schema
            except Exception:  # noqa: BLE001 - not written yet, or unreadable
                skipped.append(f"{src}.{name}")
                continue
            spark.sql(
                f"CREATE TABLE IF NOT EXISTS {src}.{name} "
                f"USING DELTA LOCATION '{location}'"
            )
            if typed_views:
                spark.sql(
                    f"CREATE OR REPLACE VIEW {src}.{name}_typed AS "
                    f"SELECT d.*, seq AS _seq, changed_at AS _changed_at, "
                    f"actor AS _actor, deleted_at AS _deleted_at FROM ("
                    f"SELECT from_json(data, '{schema}') AS d, seq, changed_at, "
                    f"actor, deleted_at FROM {src}.{name} "
                    f"WHERE deleted_at IS NULL)"
                )
            registered.append(f"{src}.{name}")
    return {"registered": registered, "skipped": skipped}


def day(col="ts"):
    """The date part of a tracker timestamp, which is always ISO text."""
    return F.substring(F.col(col), 1, 10)


def total_reps(sets="sets", reps="reps"):
    """Reps actually performed, counting a missing `sets` as one.

    The app stores no `sets` for single-set entries, so a plain `sets * reps`
    silently nulls those rows and a day's total disappears. The app's own
    figures use COALESCE(sets, 1); matching it here keeps analysis and the UI
    telling the same story.
    """
    return F.coalesce(F.col(sets), F.lit(1)) * F.col(reps)


def held_seconds(sets="sets", duration="duration_sec"):
    """Time held, for exercises measured in duration rather than reps — a plank
    counted as zero reps would vanish from any volume total."""
    return F.coalesce(F.col(sets), F.lit(1)) * F.col(duration)
