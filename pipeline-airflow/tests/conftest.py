"""Fixtures for the pipeline tests.

`pytest.ini` puts `jobs/` on the path, so `trackers_feed` and `trackers_merge`
import as the DAG imports them at runtime. The DAG module itself is deliberately
never imported here: it pulls in `airflow.sdk` and two providers and builds its
DAGs on import, which is exactly why the logic worth testing was moved out.
"""
import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest


def have_jvm():
    """Whether a JVM can actually run — not merely whether `java` is on PATH.

    macOS ships a `/usr/bin/java` stub that exists and then reports "Unable to
    locate a Java Runtime", so `shutil.which` says yes on a machine with no JDK
    at all. Run it and see.
    """
    java = shutil.which("java")
    if not java:
        return False
    try:
        return subprocess.run(
            [java, "-version"], capture_output=True, timeout=30
        ).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def pytest_collection_modifyitems(config, items):
    """Deselect the Spark tests when there is no JVM to run them.

    They are the only tests that exercise the MERGE, so they must not be
    quietly optional in CI — but on a laptop without Java, erroring on every
    run would make the whole suite useless. Skipping is loud enough there.
    """
    if have_jvm():
        return
    skip = pytest.mark.skip(reason="no working JVM; run scripts/dev-setup.sh to fetch one")
    for item in items:
        if "spark" in item.keywords:
            item.add_marker(skip)


class _Handler(BaseHTTPRequestHandler):
    """Serves whatever the test queued up, by path prefix."""

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        status, body = self.server.responses.get(
            self.path.split("?")[0], (404, {"error": "not found"})
        )
        self.server.requests.append((self.path, self.headers.get("Authorization")))
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def tracker_server():
    """A stand-in tracker. `serve(path, status, body)` queues a response;
    `.requests` records what was asked for, so auth headers can be asserted."""
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    server.responses = {}
    server.requests = []
    threading.Thread(target=server.serve_forever, daemon=True).start()

    class Fixture:
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        requests = server.requests

        @staticmethod
        def serve(path, body, status=200):
            server.responses[path] = (status, body)

    try:
        yield Fixture()
    finally:
        server.shutdown()


@pytest.fixture(scope="session")
def spark():
    """A local Spark with Delta, shared across the Spark tests — a session takes
    long enough to start that one per test would dominate the run."""
    pytest.importorskip("pyspark")
    pytest.importorskip("delta")
    from pyspark.sql import SparkSession

    session = (
        SparkSession.builder.appName("pipeline-tests")
        .master("local[2]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config(
            "spark.sql.catalog.spark_catalog",
            "org.apache.spark.sql.delta.catalog.DeltaCatalog",
        )
        .config("spark.jars.packages", "io.delta:delta-spark_4.1_2.13:4.3.1")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "2")
        .getOrCreate()
    )
    session.sparkContext.setLogLevel("ERROR")
    yield session
    session.stop()
