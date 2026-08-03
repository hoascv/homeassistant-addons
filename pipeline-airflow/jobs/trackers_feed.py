"""Reading a tracker's change feed — the part that needs no Airflow.

Kept separate from `trackers_ingest.py` so it can be imported and tested
directly: the DAG module pulls in `airflow.sdk` and two providers at import
time and builds its DAGs as a side effect, so a test can't touch it without a
full Airflow installation. Everything here is standard library.

That also makes it importable from a notebook. The add-on publishes this
directory to `/share/pipeline-airflow/lib/` on every start, so a JupyterLab
running elsewhere on the machine can exercise the same code the scheduler runs:

    import sys; sys.path.insert(0, "/share/pipeline-airflow/lib")
    from trackers_feed import discover_base_url, get

Failures raise `FeedError`, which the DAG turns into `AirflowFailException`.
Keeping the exception plain is the point — the module is usable outside Airflow.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request

# Both trackers listen on this inside their own containers, always — it is only
# the *host* port that has to differ, and reaching them by hostname sidesteps
# host ports entirely.
TRACKER_PORT = 8099
OUR_SLUG = "pipeline-airflow"

# One request per page; the feed caps this itself. Bounded so a very stale
# watermark can't turn a single run into an unbounded loop.
PAGE_SIZE = 1000
MAX_PAGES = 50
HTTP_TIMEOUT = 30


class FeedError(Exception):
    """A tracker could not be read. The message is written to be acted on."""


def discover_base_url(source: str, hostname: str | None = None) -> str | None:
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
    `hostname` is injectable so this is testable without touching the machine.
    """
    host = (hostname if hostname is not None else socket.gethostname()).split(".")[0]
    suffix = f"-{OUR_SLUG}"
    if not host.endswith(suffix) or host == suffix:
        return None
    prefix = host[: -len(suffix)]
    return f"http://{prefix}-{source.replace('_', '-')}:{TRACKER_PORT}"


def get(source: str, base_url: str, token: str, path: str) -> dict:
    """One authenticated GET against a tracker, with errors worth reading."""
    url = f"{base_url.rstrip('/')}{path}"
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        # 401/403 is worth naming: it means we reached the add-on and it
        # rejected the token, which is a different fix from not reaching it.
        if exc.code in (401, 403):
            raise FeedError(
                f"{url} rejected the token ({exc.code}). The {source}_api_token option "
                f"must match the api_token in the {source} add-on's own configuration. "
                "Both report the token's length at startup, which identifies a mismatch "
                "without writing either value down."
            ) from exc
        raise FeedError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        # "Connection refused" on its own doesn't say what was dialled, which is
        # exactly the thing you need to know.
        raise FeedError(
            f"Could not reach {url} ({exc.reason}). Check the {source} add-on is running. "
            f"If {source}_base_url is set, it must name a host port that add-on actually "
            "publishes under its Network settings; leaving the option blank is usually "
            "better, as the address is then derived from this add-on's own hostname and "
            "needs no published port at all."
        ) from exc


def read_batch(source, base_url, token, watermark, archive, log=None):
    """Everything since `watermark`, or a full snapshot when that won't do.

    `archive(name, payload)` is called with each raw response before anything
    interprets it — injected rather than reaching for S3 here, so the decision
    logic can be tested without MinIO.

    Returns the batch description the merge and watermark tasks consume.
    """
    def _log(msg, *args):
        if log is not None:
            log.info(msg, *args)

    # A first run, or a watermark the feed can no longer bridge.
    probe = get(source, base_url, token, f"/api/changes?since={watermark}&limit=1")
    if watermark == 0 or probe.get("full_reload_required"):
        snapshot = get(source, base_url, token, "/api/export")
        archive("export.json", snapshot)
        rows = sum(len(v) for v in snapshot["tables"].values())
        _log(
            "bootstrap: %s rows across %s tables, up to seq %s (watermark was %s)",
            rows, len(snapshot["tables"]), snapshot["max_seq"], watermark,
        )
        return {
            "mode": "bootstrap",
            "max_seq": snapshot["max_seq"],
            "from_seq": watermark,
            "pages": 1,
        }

    since, pages, changes_seen = watermark, 0, 0
    while pages < MAX_PAGES:
        page = get(source, base_url, token, f"/api/changes?since={since}&limit={PAGE_SIZE}")
        changes = page.get("changes") or []
        if not changes:
            break
        archive(f"changes-{since}.json", page)
        since = changes[-1]["seq"]
        changes_seen += len(changes)
        pages += 1
        if len(changes) < PAGE_SIZE:
            break
    _log(
        "incremental: %s change(s) over %s page(s), seq %s -> %s",
        changes_seen, pages, watermark, since,
    )
    return {
        "mode": "incremental",
        "max_seq": since,
        "from_seq": watermark,
        "pages": pages,
    }
