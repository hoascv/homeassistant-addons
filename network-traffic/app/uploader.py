"""Shipping a completed rotation straight to MinIO.

The first add-on in this repository to speak to MinIO directly with boto3,
rather than through Airflow's S3Hook — packet capture is a continuous stream,
not the small transactional records the trackers push through Airflow's own
HTTP-pull DAGs, so this add-on lands its own data rather than waiting to be
asked for it.
"""
from __future__ import annotations

import os
import re
import socket

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

# The Supervisor bridge gateway, same address pipeline-airflow's own
# minio_endpoint option defaults to for reaching Pipeline MinIO.
DEFAULT_ENDPOINT = "http://172.30.32.1:9000"

_CAPTURE_NAME_RE = re.compile(r"^capture-(\d{8})T(\d{6})Z\.pcap$")


def make_client(endpoint, access_key, secret_key):
    """A boto3 S3 client aimed at MinIO.

    Path-style addressing is mandatory here: boto3 defaults to virtual-hosted
    style (`bucket.endpoint/key`), which cannot resolve against a bare IP
    endpoint the way it can against a real DNS name. region_name is a
    placeholder MinIO ignores but boto3 requires one to sign requests at all —
    "us-east-1" matches what pipeline-airflow's own MinIO connection uses.
    """
    return boto3.client(
        "s3",
        endpoint_url=endpoint or DEFAULT_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def ensure_bucket(client, bucket):
    """Create the bucket if it is not there yet, mirroring the check-then-
    create pattern pipeline-airflow's trackers_ingest.py uses for the same
    `raw` bucket — except through a raw client here, since there is no
    S3Hook outside Airflow to lean on. Returns an error string, or None.
    """
    try:
        client.head_bucket(Bucket=bucket)
        return None
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code not in ("404", "NoSuchBucket"):
            return str(exc)
    except BotoCoreError as exc:
        return str(exc)

    try:
        client.create_bucket(Bucket=bucket)
        return None
    except (ClientError, BotoCoreError) as exc:
        return str(exc)


def resolve_label(capture_label):
    """An empty capture_label option means the container hostname — enough
    to tell captures apart if this add-on is ever installed on more than one
    host writing into the same bucket."""
    label = (capture_label or "").strip()
    return label or socket.gethostname()


def object_keys(pcap_filename, prefix, label):
    """The MinIO key pair for one rotated capture, derived from the pcap's
    own filename so the pcap and its JSONL sidecar always agree on where they
    landed, however long after rotation the upload actually runs.

    Layout: `<prefix>/<YYYY-MM-DD>/<label>-<timestamp>.{pcap,jsonl}`. Falls
    back to the bare filename as the timestamp if it does not match tcpdump's
    own naming template — worth keeping the upload working over refusing an
    oddly-named file outright.
    """
    name = os.path.basename(pcap_filename)
    match = _CAPTURE_NAME_RE.match(name)
    if match:
        date, time_part = match.groups()
        date_key = f"{date[0:4]}-{date[4:6]}-{date[6:8]}"
        stamp = f"{date}T{time_part}Z"
    else:
        date_key = "unknown-date"
        stamp = os.path.splitext(name)[0]

    base = f"{prefix}/{date_key}/{label}-{stamp}"
    return f"{base}.pcap", f"{base}.jsonl"


def upload_pair(client, bucket, pcap_path, jsonl_path, prefix, label):
    """Upload one rotated pcap and its JSONL sidecar. The pcap goes first: a
    JSONL object should never exist in MinIO without the source pcap backing
    it, so a failure between the two calls always leaves that invariant true.

    Returns ((pcap_key, jsonl_key), None) on success, or (None, error).
    """
    pcap_key, jsonl_key = object_keys(pcap_path, prefix, label)
    try:
        client.upload_file(pcap_path, bucket, pcap_key)
        client.upload_file(jsonl_path, bucket, jsonl_key)
    except (ClientError, BotoCoreError, OSError) as exc:
        return None, str(exc)
    return (pcap_key, jsonl_key), None
