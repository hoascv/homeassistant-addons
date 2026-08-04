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
