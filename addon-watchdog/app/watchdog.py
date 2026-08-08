"""Collecting the health of the other add-ons in this repository.

Supervisor answers one question well — is the container running — and that is
not the same question as "does the thing inside it work". The Pipeline
Metastore shipped with a background thread that died on every boot while the
port stayed open and the add-on read as perfectly healthy; that is the failure
this module exists to catch. So each add-on gets a *probe* alongside its
Supervisor state, and the two disagreeing is itself the useful signal:

    started + probe ok      -> ok
    started + probe failed  -> degraded   <- the interesting one
    not started             -> stopped

Everything here is stdlib. The host this targets is i386, so the dependency
budget is Flask and waitress and nothing else.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections import namedtuple

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
SUPERVISOR_API = "http://supervisor"
CORE_API = "http://supervisor/core/api"

# Probe per add-on, keyed by the slug as it appears in this repository.
#
# Ports are *container* ports, reached on the add-on's own hostname over the
# Supervisor network — not the published host ports. An add-on that publishes
# nothing (both trackers, ingress-only) is still reachable this way, which is
# how the Airflow DAGs talk to the trackers.
Probe = namedtuple("Probe", "kind port path what")

# Add-ons that can report how much data they hold. Both trackers expose
# /api/stats — row counts per tracked table, deliberately separate from
# /api/export, which answers the same question only by serialising every row.
#
# Nothing here reads the pipeline's Postgres: that would mean carrying database
# credentials for the sake of a number, and a watchdog is the last thing that
# should be holding them.
STATS_PATHS = {
    "gym-tracker": ("http", 8099, "/api/stats"),
    "coop-tracker": ("http", 8099, "/api/stats"),
}

PROBES = {
    "gym-tracker": Probe("http", 8099, "/", "web UI answers"),
    "coop-tracker": Probe("http", 8099, "/", "web UI answers"),
    "pipeline-postgres": Probe("tcp", 5432, None, "accepting connections"),
    "pipeline-minio": Probe("http", 9000, "/minio/health/live", "reports live"),
    "pipeline-spark": Probe("http", 8080, "/", "master UI answers"),
    "pipeline-metastore": Probe("tcp", 9083, None, "Thrift port open"),
    "pipeline-airflow": Probe("http", 8080, "/api/v2/monitor/health", "API healthy"),
    # JupyterLab binds loopback and nginx listens on the ingress port only, so
    # there is nothing a sibling add-on can reach. Supervisor state is all we
    # get, and claiming more would be a lie.
    "pipeline-notebook": None,
}

# The add-ons this watchdog reports on. Listed explicitly rather than derived
# from whatever Supervisor returns, so an add-on from another repository never
# quietly appears on the dashboard.
KNOWN_SLUGS = sorted(PROBES)

ProbeResult = namedtuple("ProbeResult", "ok detail")

# Every row carries every key, whether or not the add-on is installed. Jinja
# resolves a missing key to Undefined, which passes an `is not none` test and
# then blows up in the filter behind it — so a single add-on you happen not to
# have installed would 500 the whole dashboard. A fixed shape also means
# /api/status is worth parsing.
ROW_KEYS = (
    "slug", "installed_slug", "name", "installed", "state", "status", "version",
    "version_latest", "update_available", "cpu_percent", "memory_usage",
    "memory_percent", "probe", "probe_ok", "probe_detail", "error",
    "records", "record_counts", "other_counts", "db_bytes",
    "stats_error", "records_error",
)


def _row(**values):
    row = dict.fromkeys(ROW_KEYS)
    row.update(values)
    return row


def match_slug(installed_slug):
    """Map an installed slug back to one of ours, or None.

    Supervisor prefixes an add-on's slug with its repository id and swaps
    hyphens for underscores, so `gym-tracker` arrives as `6753e04e_gym_tracker`.
    Matching on the normalised suffix avoids hard-coding that id, which differs
    per install.
    """
    normalised = installed_slug.replace("_", "-")
    for slug in KNOWN_SLUGS:
        if normalised == slug or normalised.endswith(f"-{slug}"):
            return slug
    return None


# --- probes -------------------------------------------------------------------


def probe_tcp(host, port, timeout):
    """A connection is all a bare port can honestly tell us."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return ProbeResult(True, f"port {port} open")
    except OSError as exc:
        return ProbeResult(False, f"port {port}: {exc}")


def _request(url, token=None):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def probe_http(host, port, path, timeout, token=None):
    """Any answer below 500 counts as alive.

    A 401 or 302 means something is listening and speaking HTTP, which is the
    question being asked — several of these add-ons redirect to a login page or
    refuse an unauthenticated call, and treating that as a failure would report
    a healthy service as broken.
    """
    url = f"http://{host}:{port}{path}"
    try:
        with urllib.request.urlopen(_request(url, token), timeout=timeout) as resp:
            return ProbeResult(True, _http_detail(resp.status, resp))
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            return ProbeResult(True, f"HTTP {exc.code}")
        return ProbeResult(False, f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return ProbeResult(False, f"unreachable: {reason}")


def _http_detail(status, resp):
    """Airflow's monitor endpoint reports its own components; use them when the
    body is the JSON we expect, and fall back to the status code when it isn't
    (a different Airflow version, or something else entirely on that port)."""
    try:
        body = json.loads(resp.read(4096) or b"{}")
    except (ValueError, OSError):
        return f"HTTP {status}"
    parts = []
    for component in ("metadatabase", "scheduler", "triggerer"):
        state = (body.get(component) or {}).get("status")
        if state:
            parts.append(f"{component}={state}")
    return ", ".join(parts) if parts else f"HTTP {status}"


def run_probe(probe, host, timeout, token=None):
    if probe is None or not host:
        return None
    if probe.kind == "tcp":
        return probe_tcp(host, probe.port, timeout)
    return probe_http(host, probe.port, probe.path, timeout, token)


def fetch_stats(slug, host, timeout, token=None):
    """Row counts from an add-on that publishes them, as (data, error).

    Deliberately forgiving about the outcome — a tracker older than the release
    that added /api/stats returns 404, and one with restrict_to_user_ids set
    answers 403 without a token, and neither makes the add-on unhealthy — but
    the reason is returned rather than swallowed. An empty Records column with
    no explanation anywhere is the thing to avoid.
    """
    spec = STATS_PATHS.get(slug)
    if spec is None or not host:
        return None, None
    _, port, path = spec
    url = f"http://{host}:{port}{path}"
    try:
        with urllib.request.urlopen(_request(url, token), timeout=timeout) as resp:
            body = json.loads(resp.read(1 << 20) or b"{}")
    except urllib.error.HTTPError as exc:
        hint = {
            403: " (needs this add-on's api_token in api_tokens)",
            404: " (add-on predates /api/stats)",
        }.get(exc.code, "")
        return None, f"HTTP {exc.code}{hint}"
    except (urllib.error.URLError, OSError) as exc:
        return None, f"unreachable: {getattr(exc, 'reason', exc)}"
    except ValueError as exc:
        return None, f"bad JSON: {exc}"

    counts = body.get("counts")
    if not isinstance(counts, dict):
        return None, "response had no 'counts' object"
    others = body.get("other_counts")
    others = others if isinstance(others, dict) else {}
    return {
        # total_all covers every table in the file; total is the tracked subset
        # a tracker older than that split reports, and is the right fallback
        # rather than showing nothing.
        "records": body.get("total_all", body.get("total")),
        "record_counts": counts,
        "other_counts": others,
        "db_bytes": body.get("db_bytes"),
    }, None


def derive_status(state, probe_result):
    """Supervisor state and probe outcome, reduced to one word.

    `degraded` is the reason this add-on exists: the container is up and the
    service inside it is not answering.
    """
    if state != "started":
        return "stopped"
    if probe_result is None:
        return "running"
    return "ok" if probe_result.ok else "degraded"


def is_unhealthy(status, ignore_stopped=True):
    """What counts as worth alerting on.

    Most of the pipeline is `boot: manual` and spends its life stopped on
    purpose, so counting that as a problem would make the summary useless.
    """
    if status == "degraded":
        return True
    return status == "stopped" and not ignore_stopped


# --- Supervisor and Core APIs -------------------------------------------------


def _api(base, path, method="GET", payload=None, timeout=10):
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN not set (not running under Supervisor)"
    req = urllib.request.Request(f"{base}{path}", method=method)
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            body = resp.read()
            return (json.loads(body) if body else None), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}: {exc.read().decode('utf-8', 'ignore')[:200]}"
    except Exception as exc:  # noqa: BLE001 - a poll failure must not kill the loop
        return None, str(exc)


def supervisor_addons():
    """Every installed add-on Supervisor will tell us about."""
    body, err = _api(SUPERVISOR_API, "/addons")
    if err:
        return [], err
    return ((body or {}).get("data") or {}).get("addons", []), None


def supervisor_addon_info(slug):
    body, err = _api(SUPERVISOR_API, f"/addons/{slug}/info")
    if err:
        return {}, err
    return (body or {}).get("data") or {}, None


def supervisor_addon_stats(slug):
    body, err = _api(SUPERVISOR_API, f"/addons/{slug}/stats")
    if err:
        return {}, err
    return (body or {}).get("data") or {}, None


def push_sensor(entity_id, state, attributes):
    return _api(
        CORE_API,
        f"/states/{entity_id}",
        method="POST",
        payload={"state": state, "attributes": attributes},
    )


# --- one pass over everything -------------------------------------------------


def collect(timeout=5, ignore_stopped=True, now=None, tokens=None):
    """One full scan: Supervisor state, resource use, update status, probe.

    Returns a snapshot dict — never raises, because this runs on a timer and a
    Supervisor hiccup should show up on the dashboard rather than stop the
    watchdog.
    """
    now = now or time.time()
    addons, err = supervisor_addons()
    results = []
    if err:
        return {"generated": now, "error": err, "addons": [], "unhealthy": 0}

    seen = {}
    for entry in addons:
        slug = match_slug(entry.get("slug", ""))
        if slug:
            seen[slug] = entry

    for slug in KNOWN_SLUGS:
        entry = seen.get(slug)
        if entry is None:
            results.append(
                _row(slug=slug, name=slug, status="not installed", installed=False,
                     update_available=False)
            )
            continue

        installed_slug = entry.get("slug", "")
        info, info_err = supervisor_addon_info(installed_slug)
        state = info.get("state") or entry.get("state") or "unknown"
        host = info.get("hostname")

        token = (tokens or {}).get(slug)
        probe = PROBES.get(slug)
        probe_result = run_probe(probe, host, timeout, token) if state == "started" else None
        status = derive_status(state, probe_result)

        # A 403 from an add-on that publishes counts means restrict_to_user_ids
        # is on and no token was configured. It is still alive — that is what
        # the probe asked — but the empty Records column would otherwise be a
        # mystery, so the detail says what to do about it.
        if (
            probe_result is not None
            and probe_result.ok
            and not token
            and slug in STATS_PATHS
            and "403" in (probe_result.detail or "")
        ):
            probe_result = ProbeResult(
                True, f"{probe_result.detail} — set this add-on's api_token to read records"
            )

        stats, stats_err = {}, None
        counts, counts_err = None, None
        if state == "started":
            stats, stats_err = supervisor_addon_stats(installed_slug)
            # Only worth asking an add-on that just answered its probe; a
            # service that is not responding will not answer this either, and
            # waiting for it to time out twice slows every scan.
            if probe_result is None or probe_result.ok:
                counts, counts_err = fetch_stats(slug, host, timeout, token)

        results.append(
            _row(
                slug=slug,
                installed_slug=installed_slug,
                name=entry.get("name") or slug,
                installed=True,
                state=state,
                status=status,
                version=entry.get("version"),
                version_latest=entry.get("version_latest"),
                update_available=bool(entry.get("update_available")),
                cpu_percent=stats.get("cpu_percent"),
                memory_usage=stats.get("memory_usage"),
                memory_percent=stats.get("memory_percent"),
                probe=probe.what if probe else None,
                probe_ok=None if probe_result is None else probe_result.ok,
                probe_detail=None if probe_result is None else probe_result.detail,
                error=info_err,
                stats_error=stats_err,
                records_error=counts_err,
                **(counts or {}),
            )
        )

    unhealthy = sum(
        1 for r in results if r.get("installed") and is_unhealthy(r["status"], ignore_stopped)
    )
    return {
        "generated": now,
        "error": None,
        "addons": results,
        "unhealthy": unhealthy,
        "updates": sum(1 for r in results if r.get("update_available")),
    }


def publish(snapshot, prefix="addon_watchdog", ignore_stopped=True):
    """One sensor per add-on plus a summary, so automations can act on this.

    The summary's state is a count rather than a word: `> 0` is the whole
    condition an automation needs, and it survives adding add-ons later.
    """
    pushed, errors = 0, []
    for row in snapshot.get("addons", []):
        if not row.get("installed"):
            continue
        entity = f"sensor.{prefix}_{row['slug'].replace('-', '_')}"
        _, err = push_sensor(
            entity,
            row["status"],
            {
                "friendly_name": f"{row['name']} status",
                "icon": "mdi:heart-pulse",
                "addon_version": row.get("version"),
                "latest_version": row.get("version_latest"),
                "update_available": row.get("update_available"),
                "cpu_percent": row.get("cpu_percent"),
                "memory_usage_mb": _mb(row.get("memory_usage")),
                "probe": row.get("probe"),
                "probe_detail": row.get("probe_detail"),
                # Absent rather than null for add-ons that hold no data, so a
                # template testing the attribute gets a clean answer.
                **(
                    {
                        "records": row["records"],
                        "record_counts": row.get("record_counts"),
                        "other_counts": row.get("other_counts"),
                        "db_size_mb": _mb(row.get("db_bytes")),
                    }
                    if row.get("records") is not None
                    else {}
                ),
            },
        )
        if err:
            errors.append(f"{entity}: {err}")
        else:
            pushed += 1

    _, err = push_sensor(
        f"sensor.{prefix}_unhealthy",
        str(snapshot.get("unhealthy", 0)),
        {
            "friendly_name": "Add-ons unhealthy",
            "icon": "mdi:alert-circle-outline",
            "unit_of_measurement": "add-ons",
            "updates_available": snapshot.get("updates", 0),
            "degraded": [
                r["slug"] for r in snapshot.get("addons", []) if r.get("status") == "degraded"
            ],
            "counts_stopped": not ignore_stopped,
        },
    )
    if err:
        errors.append(f"summary: {err}")
    return pushed, errors


def _mb(value):
    return None if not value else round(value / (1024 * 1024), 1)
