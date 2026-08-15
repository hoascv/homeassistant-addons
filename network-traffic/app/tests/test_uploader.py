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


@mock_aws
def test_count_prefix_reports_objects_and_bytes_under_the_prefix_only():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")
    client.put_object(Bucket="raw", Key="network_traffic/2026-08-15/a.pcap", Body=b"12345")
    client.put_object(Bucket="raw", Key="network_traffic/2026-08-15/a.jsonl", Body=b"12")
    client.put_object(Bucket="raw", Key="gym_tracker/2026-08-15/run.json", Body=b"1234567890")

    count, total_bytes, err = uploader.count_prefix(client, "raw", "network_traffic")

    assert err is None
    assert count == 2
    assert total_bytes == 7


@mock_aws
def test_count_prefix_empty_is_zero_not_an_error():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")

    count, total_bytes, err = uploader.count_prefix(client, "raw", "network_traffic")

    assert (count, total_bytes, err) == (0, 0, None)


@mock_aws
def test_clear_prefix_deletes_only_its_own_objects():
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")
    client.put_object(Bucket="raw", Key="network_traffic/2026-08-15/a.pcap", Body=b"12345")
    client.put_object(Bucket="raw", Key="network_traffic/2026-08-15/a.jsonl", Body=b"12")
    client.put_object(Bucket="raw", Key="gym_tracker/2026-08-15/run.json", Body=b"1234567890")

    deleted_count, deleted_bytes, err = uploader.clear_prefix(client, "raw", "network_traffic")

    assert err is None
    assert deleted_count == 2
    assert deleted_bytes == 7

    remaining = client.list_objects_v2(Bucket="raw").get("Contents", [])
    assert [obj["Key"] for obj in remaining] == ["gym_tracker/2026-08-15/run.json"]


@mock_aws
def test_clear_prefix_handles_more_than_one_page(monkeypatch):
    """delete_objects takes at most 1000 keys per call; this forces a second
    page with a small page size instead of actually uploading 1000+ objects."""
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")
    for i in range(5):
        client.put_object(Bucket="raw", Key=f"network_traffic/2026-08-15/{i}.pcap", Body=b"x")

    real_paginate = client.get_paginator("list_objects_v2").paginate

    class SmallPagePaginator:
        def paginate(self, **kwargs):
            kwargs["PaginationConfig"] = {"PageSize": 2}
            return real_paginate(**kwargs)

    monkeypatch.setattr(client, "get_paginator", lambda name: SmallPagePaginator())

    deleted_count, deleted_bytes, err = uploader.clear_prefix(client, "raw", "network_traffic")

    assert err is None
    assert deleted_count == 5
    assert deleted_bytes == 5


@mock_aws
def test_clear_prefix_on_an_empty_prefix_deletes_nothing(monkeypatch):
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="raw")

    deleted_count, deleted_bytes, err = uploader.clear_prefix(client, "raw", "network_traffic")

    assert (deleted_count, deleted_bytes, err) == (0, 0, None)
