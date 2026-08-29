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

Everything here is stdlib: the dependency budget is Flask and waitress and
nothing else, which is the right posture for the component that has to keep
reporting when the things it watches are broken.
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
    "detection-hub": ("http", 8099, "/api/stats"),
    "electricity-tracker": ("http", 8099, "/api/stats"),
    "knowledge": ("http", 8099, "/api/stats"),
}

PROBES = {
    "gym-tracker": Probe("http", 8099, "/", "web UI answers"),
    "coop-tracker": Probe("http", 8099, "/", "web UI answers"),
    "electricity-tracker": Probe("http", 8099, "/", "web UI answers"),
    "knowledge": Probe("http", 8099, "/", "web UI answers"),
    # /api/health rather than /, because this add-on's page answers perfectly
    # well with a broken model or a dead capture thread. It returns 503 for
    # exactly those, and the rest of its state arrives in its status file.
    "detection-hub": Probe("http", 8099, "/api/health", "detector ready"),
    # Same reasoning as detection-hub: the dashboard can answer fine with a
    # dead tcpdump process behind it, so /api/health rather than /.
    "network-traffic": Probe("http", 8099, "/api/health", "capture running"),
    # Ingress-only, so every request without Home Assistant's ingress user
    # header gets a 401 — including this one. That is the healthy answer here,
    # and probe_http already counts anything under 500 as alive: what is being
    # asked is whether Flask is up and speaking HTTP, not whether the watchdog
    # can get in. It deliberately cannot, and it holds no journal credential.
    "journal": Probe("http", 8099, "/", "web UI answers"),
    "pipeline-postgres": Probe("tcp", 5432, None, "accepting connections"),
    # The replica publishes 5433 on the host but still listens on 5432 inside
    # its container, and probes go to container ports.
    "pipeline-postgres-replica": Probe("tcp", 5432, None, "standby accepting connections"),
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

# Add-ons that report their own state, because a probe cannot ask the question.
# A standby that has silently stopped replaying still accepts connections, and a
# backup that stopped running leaves no trace on any port at all — both are
# invisible to a probe and both are exactly what you want to be told about.
#
# Each add-on writes one small JSON file here rather than the watchdog reaching
# into a database: this add-on holds no database credentials, and that stays
# true. Same division of labour as the trackers' /api/stats.
STATUS_DIR = os.environ.get("PIPELINE_STATUS_DIR", "/share/pipeline-status")

# A report older than this is treated as missing. The producers refresh on a
# minute (the replica) or per backup (Postgres), so a report that has not moved
# in an hour means the writer stopped, which is itself the news.
REPORT_STALE_SECONDS = 3600

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
    "report_ok", "report_detail", "report_metrics", "report_age", "report_error",
    "uptime_seconds", "uptime_known", "restarts", "last_restart_at",
    "last_restart_reason", "supervisor_watchdog",
    "version_at", "version_seconds", "version_known",
    "disk_read_bps", "disk_write_bps",
)

# Where the restart/uptime history lives. Supervisor exposes no container start
# time and no restart counter, so uptime here is measured from when *this*
# add-on first saw the other one running — which means it survives a watchdog
# restart only if it is written down.
STATE_FILE = os.environ.get("WATCHDOG_STATE_FILE", "/data/watchdog-state.json")


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
    that added /api/stats returns 404; a current one answers 401 without a token
    (its published port requires one); one with restrict_to_user_ids set answers
    403. None of those makes the add-on unhealthy, but the reason is returned
    rather than swallowed. An empty Records column with no explanation anywhere
    is the thing to avoid.
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
            401: " (needs this add-on's api_token in api_tokens)",
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


def load_state():
    try:
        with open(STATE_FILE) as handle:
            state = json.load(handle)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state):
    try:
        tmp = f"{STATE_FILE}.tmp"
        with open(tmp, "w") as handle:
            json.dump(state, handle)
        os.replace(tmp, STATE_FILE)
    except OSError:
        # Losing the history costs uptime accuracy, not correctness, and is not
        # worth failing a scan over.
        pass


def track_uptime(state, slug, running, network_rx, now, blk_read=None, blk_write=None,
                 version=None):
    """Uptime, installed version and restart count, from successive observations.

    Supervisor reports neither a container start time nor a restart counter, so
    both are derived here. Two things count as a restart:

    - a transition into `started` from anything else, which is the obvious case;
    - the container's cumulative network counter going *backwards* while it
      stayed `started`. Those counters reset with the container, so a drop is a
      restart that happened entirely between two scans — otherwise invisible,
      since a minute is long enough for an add-on to die and come back.

    Uptime is therefore "at least": measured from the first time this add-on saw
    it running, which is not the same as when it actually started. `uptime_known`
    says which it is, so the dashboard can avoid claiming more than it knows.

    Restarts are counted *for the version now running*. A count that spans
    versions answers a question nobody asked: an add-on updated seven times in
    two days accumulates restarts that belong to builds no longer installed, and
    "is this build stable" is what the number is read for. The version's own
    install time is kept alongside it, since a small count means nothing without
    knowing how long it has had to grow.
    """
    entry = state.setdefault(slug, {})
    was_running = entry.get("running", False)
    previous_rx = entry.get("network_rx")
    previous_version = entry.get("version")

    first_look = not entry.get("seen_before", False)
    # A version this add-on has not recorded before. Two cases, and they are not
    # the same thing: an update that happened while it was watching, or the first
    # scan to record any version at all — a state file written before this was
    # tracked, or an add-on seen for the first time. Only the first is evidence
    # about what the container did.
    version_changed = version is not None and version != previous_version
    version_observed = version_changed and previous_version is not None

    restarted = False
    if running and not was_running:
        restarted = not first_look
    elif running and was_running and network_rx is not None and previous_rx is not None:
        restarted = network_rx < previous_rx
    # An update replaces the container, so it restarted whether or not the
    # counters show it: rx resets to zero, but a busy minute can leave the new
    # counter above the old one and hide the drop. The version moving is proof
    # on its own, and it makes the uptime clock exact from here.
    if version_observed:
        restarted = True

    if running and first_look:
        # Already running when this add-on first looked, so its real start is
        # older than anything observable here. Uptime is a lower bound until a
        # restart is seen, at which point the clock becomes exact.
        entry["started_at"] = now
        entry["uptime_assumed"] = True
    elif restarted or (running and not entry.get("started_at")):
        entry["started_at"] = now
        entry["uptime_assumed"] = False
    if restarted and not version_changed:
        entry["restarts"] = entry.get("restarts", 0) + 1
        entry["last_restart_at"] = now
        # The reason is filled in separately, from the Supervisor log; clearing
        # it here stops a new restart inheriting the previous one's explanation.
        entry["last_restart_reason"] = None
    if version_changed:
        # The restart the update itself caused is part of the install, not a
        # fault of the new version, so the count starts at zero rather than one.
        entry["version"] = version
        entry["version_at"] = now
        entry["version_assumed"] = not version_observed
        entry["restarts"] = 0
        entry["last_restart_at"] = None
        entry["last_restart_reason"] = None
    if not running:
        entry["started_at"] = None

    # Per-add-on disk I/O, from the same cumulative-counter treatment: this is
    # what shows the pipeline's own demand was unchanged while the device was
    # saturated, which is the difference between "the storage was the limit" and
    # "the pipeline asked for more".
    read_bps = write_bps = None
    previous_at = entry.get("counters_at")
    if running and previous_at and now > previous_at and not restarted:
        window = now - previous_at
        for value, key, name in ((blk_read, "blk_read", "read"),
                                 (blk_write, "blk_write", "write")):
            before = entry.get(key)
            if value is not None and before is not None and value >= before:
                rate = round((value - before) / window, 1)
                if name == "read":
                    read_bps = rate
                else:
                    write_bps = rate

    entry["running"] = running
    entry["seen_before"] = True
    entry["counters_at"] = now
    if network_rx is not None:
        entry["network_rx"] = network_rx
    if blk_read is not None:
        entry["blk_read"] = blk_read
    if blk_write is not None:
        entry["blk_write"] = blk_write

    started_at = entry.get("started_at")
    version_at = entry.get("version_at")
    return {
        "disk_read_bps": read_bps,
        "disk_write_bps": write_bps,
        "uptime_seconds": None if not started_at else max(0, int(now - started_at)),
        "uptime_known": bool(started_at) and not entry.get("uptime_assumed", False),
        "restarts": entry.get("restarts", 0),
        "last_restart_at": entry.get("last_restart_at"),
        "last_restart_reason": entry.get("last_restart_reason"),
        "version_at": version_at,
        "version_seconds": None if not version_at else max(0, int(now - version_at)),
        # False means this version was already installed when the watchdog first
        # recorded one, so its age is a lower bound — the same distinction
        # `uptime_known` draws, and for the same reason.
        "version_known": bool(version_at) and not entry.get("version_assumed", True),
    }


# Lines Supervisor writes when an add-on stops unexpectedly or its own watchdog
# steps in. The exit-code form is confirmed against a real Supervisor log; the
# watchdog forms are matched loosely on purpose, since no watchdog-triggered
# restart has been observed here to pin the exact wording.
_RESTART_PATTERNS = (
    "exited with non-zero exit code",
    "watchdog",
    "failed to start",
    "system is not healthy",
)


def supervisor_restart_reasons(names, lines=400):
    """Best-effort: the most recent Supervisor log line explaining each add-on.

    Nothing in the Supervisor API exposes an exit code or a restart cause, so
    the log is the only source. Failure here is expected and harmless — the
    endpoint may be refused to this role — and costs a reason, never a restart
    count.
    """
    body, err = _api(SUPERVISOR_API, f"/supervisor/logs?lines={int(lines)}", raw=True)
    if err:
        return {}, err
    reasons = {}
    for line in (body or "").splitlines():
        lowered = line.lower()
        if not any(pattern in lowered for pattern in _RESTART_PATTERNS):
            continue
        for slug, name in names.items():
            if name and name.lower() in lowered:
                # Later lines win: the list is chronological, so the last match
                # is the most recent explanation.
                reasons[slug] = line.strip()[:300]
    return reasons, None


def read_report(slug, now=None):
    """An add-on's own account of itself, or None.

    Returns (report, error). A missing file is not an error — most add-ons
    write none — but a stale one is, because the interesting failure is the
    writer having stopped rather than the file having gone.
    """
    path = os.path.join(STATUS_DIR, f"{slug}.json")
    try:
        with open(path) as handle:
            body = json.load(handle)
    except FileNotFoundError:
        return None, None
    except (OSError, ValueError) as exc:
        return None, f"unreadable status report: {exc}"

    updated = body.get("updated_at")
    if not isinstance(updated, (int, float)):
        return None, "status report has no usable updated_at"
    age = max(0, int((now or time.time()) - updated))
    report = {
        "ok": bool(body.get("ok")),
        "detail": str(body.get("detail") or ""),
        "metrics": body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
        "age": age,
    }
    if age > REPORT_STALE_SECONDS:
        report["ok"] = False
        report["detail"] = f"last reported {age // 60} min ago: {report['detail']}"
    return report, None


def derive_status(state, probe_result, report=None):
    """Supervisor state, probe outcome and self-report, reduced to one word.

    `degraded` is the reason this add-on exists: the container is up and
    something inside it is not working.

    A failing self-report degrades too, and that is a deliberate widening of the
    word. Postgres with a broken backup still answers its port perfectly — the
    service is fine and the job it was given is not — but "backups stopped
    running three weeks ago" is precisely the kind of quiet failure worth being
    told about, and `degraded` is what feeds the summary sensor an automation
    can act on. The detail line says which of the two it is.
    """
    if state != "started":
        return "stopped"
    if report is not None and not report["ok"]:
        return "degraded"
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


def _api(base, path, method="GET", payload=None, timeout=10, raw=False):
    if not SUPERVISOR_TOKEN:
        return None, "SUPERVISOR_TOKEN not set (not running under Supervisor)"
    req = urllib.request.Request(f"{base}{path}", method=method)
    req.add_header("Authorization", f"Bearer {SUPERVISOR_TOKEN}")
    req.add_header("Content-Type", "application/json")
    data = json.dumps(payload).encode() if payload is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=timeout) as resp:
            body = resp.read()
            # The log endpoints return plain text, not JSON, so json.loads would
            # turn a perfectly good answer into an error.
            if raw:
                return body.decode("utf-8", "replace"), None
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

    state_history = load_state()
    # Supervisor names add-ons by their display name in the log, not their slug.
    reasons, reasons_err = supervisor_restart_reasons(
        {slug: entry.get("name") for slug, entry in seen.items()}
    )

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
        report, report_err = read_report(slug, now) if state == "started" else (None, None)
        status = derive_status(state, probe_result, report)

        # A 401 or 403 from an add-on that publishes counts means it wants a
        # credential this watchdog was not given: 401 because its published port
        # requires api_token, 403 because restrict_to_user_ids is on. It is still
        # alive — that is what the probe asked — but the empty Records column
        # would otherwise be a mystery, so the detail says what to do about it.
        if (
            probe_result is not None
            and probe_result.ok
            and not token
            and slug in STATS_PATHS
            and any(code in (probe_result.detail or "") for code in ("401", "403"))
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

        tracked = track_uptime(
            state_history, slug, state == "started", stats.get("network_rx"), now,
            blk_read=stats.get("blk_read"), blk_write=stats.get("blk_write"),
            version=entry.get("version"),
        )
        if tracked["last_restart_at"] and reasons.get(slug):
            tracked["last_restart_reason"] = reasons[slug]
            state_history[slug]["last_restart_reason"] = reasons[slug]

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
                report_ok=None if report is None else report["ok"],
                report_detail=None if report is None else report["detail"],
                report_metrics=None if report is None else report["metrics"],
                report_age=None if report is None else report["age"],
                report_error=report_err,
                supervisor_watchdog=info.get("watchdog"),
                **tracked,
                **(counts or {}),
            )
        )

    # Written once per scan rather than per add-on: uptime is measured from
    # observations, so losing this file means every add-on looks freshly started.
    save_state(state_history)

    unhealthy = sum(
        1 for r in results if r.get("installed") and is_unhealthy(r["status"], ignore_stopped)
    )
    return {
        "generated": now,
        "error": None,
        "addons": results,
        "unhealthy": unhealthy,
        "updates": sum(1 for r in results if r.get("update_available")),
        # Surfaced rather than swallowed: if the log endpoint is refused to this
        # role, restarts are still counted and only the reason is missing, and
        # the difference should be visible instead of looking like "no restarts
        # ever happened".
        "reasons_error": reasons_err,
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
                # When the running version arrived, and whether that was seen or
                # is a lower bound — an automation reporting "updated 3 days ago"
                # should not invent a date the watchdog never observed.
                "version_installed_at": row.get("version_at"),
                "version_age_seconds": row.get("version_seconds"),
                "version_installed_known": row.get("version_known"),
                "cpu_percent": row.get("cpu_percent"),
                "memory_usage_mb": _mb(row.get("memory_usage")),
                "probe": row.get("probe"),
                "probe_detail": row.get("probe_detail"),
                "uptime_seconds": row.get("uptime_seconds"),
                # Says whether uptime is exact or a lower bound, so a template
                # graphing it knows not to treat the first value as a start.
                "uptime_known": row.get("uptime_known"),
                # Restarts on the running version only — it resets on update, so
                # an alert threshold here means "this build is flapping" rather
                # than "this add-on has been updated a lot".
                "restarts": row.get("restarts"),
                "last_restart_at": row.get("last_restart_at"),
                "last_restart_reason": row.get("last_restart_reason"),
                # Whether Supervisor's own watchdog will restart this add-on —
                # which is where a restart nobody asked for usually comes from.
                "supervisor_watchdog": row.get("supervisor_watchdog"),
                # Per-add-on disk rates, so a slow window can be attributed:
                # device saturated while these stayed flat means the load is
                # not coming from here.
                "disk_read_bps": row.get("disk_read_bps"),
                "disk_write_bps": row.get("disk_write_bps"),
                # Flattened rather than nested: a Lovelace card or an automation
                # template reads state_attr(...) one level deep comfortably and
                # a dict of dicts awkwardly.
                **(
                    {
                        "report_ok": row["report_ok"],
                        "report": row.get("report_detail"),
                        "report_age_seconds": row.get("report_age"),
                        **{f"report_{k}": v for k, v in (row.get("report_metrics") or {}).items()},
                    }
                    if row.get("report_ok") is not None
                    else {}
                ),
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
