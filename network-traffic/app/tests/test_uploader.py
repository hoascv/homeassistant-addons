"""uploader.py: key layout is pure and tested directly; bucket/upload
behaviour is tested against moto's mocked S3 rather than a real MinIO — this
add-on is the first in the repository to talk to MinIO outside Airflow, so
there is no existing double for it to reuse.
"""
import boto3
from botocore.exceptions import ClientError
from moto import mock_aws

import uploader


def test_make_client_uses_path_style_addressing():
    client = uploader.make_client("http://172.30.32.1:9000", "AK", "SK")
    assert client.meta.config.s3["addressing_style"] == "path"


def test_resolve_label_falls_back_to_hostname(monkeypatch):
    monkeypatch.setattr(uploader.socket, "gethostname", lambda: "hass")
    assert uploader.resolve_label("") == "hass"
    assert uploader.resolve_label("   ") == "hass"
    assert uploader.resolve_label("  custom-host  ") == "custom-host"


def test_object_keys_derived_from_capture_filename():
    pcap_key, jsonl_key = uploader.object_keys(
        "/data/pcap/capture-20260815T120000Z.pcap", "network_traffic", "myhost",
    )
    assert pcap_key == "network_traffic/2026-08-15/myhost-20260815T120000Z.pcap"
    assert jsonl_key == "network_traffic/2026-08-15/myhost-20260815T120000Z.jsonl"


def test_object_keys_falls_back_for_an_unexpected_filename():
    pcap_key, jsonl_key = uploader.object_keys("/data/pcap/weird-name.pcap", "network_traffic", "host")
    assert pcap_key == "network_traffic/unknown-date/host-weird-name.pcap"
    assert jsonl_key == "network_traffic/unknown-date/host-weird-name.jsonl"


@mock_aws
def test_ensure_bucket_creates_when_missing_and_is_idempotent():
    client = boto3.client("s3", region_name="us-east-1")
    assert uploader.ensure_bucket(client, "raw") is None
    # Already exists now — head_bucket should succeed and nothing re-creates it.
    assert uploader.ensure_bucket(client, "raw") is None


@mock_aws
def test_upload_pair_lands_both_objects(tmp_path):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")

    pcap = tmp_path / "capture-20260815T120000Z.pcap"
    pcap.write_bytes(b"fake-pcap-bytes")
    jsonl = tmp_path / "capture-20260815T120000Z.jsonl"
    jsonl.write_text('{"a": 1}\n')

    keys, err = uploader.upload_pair(client, "raw", str(pcap), str(jsonl), "network_traffic", "myhost")

    assert err is None
    pcap_key, jsonl_key = keys
    assert client.get_object(Bucket="raw", Key=pcap_key)["Body"].read() == b"fake-pcap-bytes"
    assert client.get_object(Bucket="raw", Key=jsonl_key)["Body"].read() == b'{"a": 1}\n'


@mock_aws
def test_upload_pair_reports_error_without_raising(tmp_path):
    client = boto3.client("s3", region_name="us-east-1")
    # Bucket deliberately not created, so upload_file fails.
    keys, err = uploader.upload_pair(
        client, "missing-bucket", str(tmp_path / "no.pcap"), str(tmp_path / "no.jsonl"),
        "network_traffic", "host",
    )
    assert keys is None
    assert err


@mock_aws
def test_ensure_lifecycle_creates_an_expiration_rule():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")

    err = uploader.ensure_lifecycle(client, "raw", "network_traffic", 7)

    assert err is None
    rules = client.get_bucket_lifecycle_configuration(Bucket="raw")["Rules"]
    assert len(rules) == 1
    assert rules[0]["Filter"]["Prefix"] == "network_traffic/"
    assert rules[0]["Expiration"]["Days"] == 7
    assert rules[0]["Status"] == "Enabled"


@mock_aws
def test_ensure_lifecycle_updates_its_own_rule_without_touching_others():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")
    client.put_bucket_lifecycle_configuration(
        Bucket="raw",
        LifecycleConfiguration={"Rules": [{
            "ID": "gym-tracker-expiry",
            "Filter": {"Prefix": "gym_tracker/"},
            "Status": "Enabled",
            "Expiration": {"Days": 30},
        }]},
    )

    uploader.ensure_lifecycle(client, "raw", "network_traffic", 7)
    uploader.ensure_lifecycle(client, "raw", "network_traffic", 14)  # a later change of mind

    rules = client.get_bucket_lifecycle_configuration(Bucket="raw")["Rules"]
    assert len(rules) == 2
    ours = next(r for r in rules if r["Filter"]["Prefix"] == "network_traffic/")
    theirs = next(r for r in rules if r["Filter"]["Prefix"] == "gym_tracker/")
    assert ours["Expiration"]["Days"] == 14
    assert theirs["Expiration"]["Days"] == 30  # untouched


@mock_aws
def test_ensure_lifecycle_zero_days_removes_only_our_rule():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")
    client.put_bucket_lifecycle_configuration(
        Bucket="raw",
        LifecycleConfiguration={"Rules": [{
            "ID": "gym-tracker-expiry",
            "Filter": {"Prefix": "gym_tracker/"},
            "Status": "Enabled",
            "Expiration": {"Days": 30},
        }]},
    )
    uploader.ensure_lifecycle(client, "raw", "network_traffic", 7)

    err = uploader.ensure_lifecycle(client, "raw", "network_traffic", 0)

    assert err is None
    rules = client.get_bucket_lifecycle_configuration(Bucket="raw")["Rules"]
    assert len(rules) == 1
    assert rules[0]["Filter"]["Prefix"] == "gym_tracker/"


@mock_aws
def test_ensure_lifecycle_zero_days_deletes_configuration_if_nothing_left():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")
    uploader.ensure_lifecycle(client, "raw", "network_traffic", 7)

    err = uploader.ensure_lifecycle(client, "raw", "network_traffic", 0)

    assert err is None
    try:
        client.get_bucket_lifecycle_configuration(Bucket="raw")
        assert False, "expected NoSuchLifecycleConfiguration"
    except ClientError as exc:
        assert exc.response["Error"]["Code"] == "NoSuchLifecycleConfiguration"
