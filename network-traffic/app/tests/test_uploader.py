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


# --- what MinIO refusing looks like -------------------------------------------
#
# moto answers correctly, which is what makes it useful above and useless here:
# every branch below is a MinIO that is down, full, or refusing the credential,
# and the point of each is that the add-on returns the reason rather than
# raising it into the lifecycle thread.


def _client_error(code, operation="HeadBucket"):
    return ClientError({"Error": {"Code": code, "Message": "denied"}}, operation)


class _Failing:
    """A client whose named methods raise; anything else returns an empty dict."""

    def __init__(self, **failures):
        self._failures = failures
        self.calls = []

    def __getattr__(self, name):
        def _call(*args, **kwargs):
            self.calls.append(name)
            if name in self._failures:
                raise self._failures[name]
            return {}

        return _call


def test_a_bucket_check_refused_for_a_reason_other_than_absence_is_reported():
    """403 means the credential is wrong; creating the bucket would fail too,
    and reporting "created" would be a lie."""
    client = _Failing(head_bucket=_client_error("403"))
    err = uploader.ensure_bucket(client, "raw")
    assert err and "denied" in err
    assert "create_bucket" not in client.calls


def test_a_transport_failure_checking_the_bucket_is_reported():
    from botocore.exceptions import EndpointConnectionError

    client = _Failing(head_bucket=EndpointConnectionError(endpoint_url="http://x"))
    err = uploader.ensure_bucket(client, "raw")
    assert err and "http://x" in err


def test_a_failed_bucket_creation_is_reported_not_raised():
    client = _Failing(head_bucket=_client_error("404"),
                      create_bucket=_client_error("AccessDenied", "CreateBucket"))
    err = uploader.ensure_bucket(client, "raw")
    assert err and "denied" in err


def test_an_unreadable_lifecycle_configuration_is_reported():
    """Anything other than "there is no rule yet" means the call itself failed,
    and overwriting rules we could not read would clobber somebody else's."""
    client = _Failing(get_bucket_lifecycle_configuration=_client_error(
        "AccessDenied", "GetBucketLifecycleConfiguration"))
    err = uploader.ensure_lifecycle(client, "raw", "network_traffic", 7)
    assert err and "denied" in err
    assert "put_bucket_lifecycle_configuration" not in client.calls


def test_a_transport_failure_reading_the_lifecycle_rules_is_reported():
    from botocore.exceptions import EndpointConnectionError

    client = _Failing(get_bucket_lifecycle_configuration=EndpointConnectionError(
        endpoint_url="http://x"))
    assert uploader.ensure_lifecycle(client, "raw", "network_traffic", 7)


def test_a_failed_rule_write_is_reported():
    client = _Failing(
        get_bucket_lifecycle_configuration=_client_error(
            "NoSuchLifecycleConfiguration", "GetBucketLifecycleConfiguration"),
        put_bucket_lifecycle_configuration=_client_error(
            "AccessDenied", "PutBucketLifecycleConfiguration"),
    )
    err = uploader.ensure_lifecycle(client, "raw", "network_traffic", 7)
    assert err and "denied" in err


def test_counting_reports_what_it_managed_before_the_failure():
    """The usage tile would rather show a floor than nothing at all."""
    class _Paginator:
        def paginate(self, **kwargs):
            yield {"Contents": [{"Key": "network_traffic/a.pcap", "Size": 100}]}
            raise _client_error("InternalError", "ListObjectsV2")

    class _Client:
        def get_paginator(self, _name):
            return _Paginator()

    count, total, err = uploader.count_prefix(_Client(), "raw", "network_traffic")
    assert (count, total) == (1, 100)
    assert err


def test_a_delete_that_partly_fails_reports_both_halves():
    """MinIO answers 200 with a per-key Errors list, which a bare try/except
    around the call would miss — the count would claim keys that still exist."""
    class _Paginator:
        def paginate(self, **kwargs):
            yield {"Contents": [
                {"Key": "network_traffic/a.pcap", "Size": 100},
                {"Key": "network_traffic/b.pcap", "Size": 200},
            ]}

    class _Client:
        def get_paginator(self, _name):
            return _Paginator()

        def delete_objects(self, **kwargs):
            return {
                "Deleted": [{"Key": "network_traffic/a.pcap"}],
                "Errors": [{"Key": "network_traffic/b.pcap", "Message": "locked"}],
            }

    count, freed, err = uploader.clear_prefix(_Client(), "raw", "network_traffic")
    assert (count, freed) == (1, 100)
    assert "network_traffic/b.pcap: locked" in err


def test_a_long_list_of_delete_failures_is_truncated():
    """One line per failed key would fill the log with a thousand of them."""
    keys = [{"Key": f"network_traffic/{i}.pcap", "Size": 1} for i in range(12)]

    class _Paginator:
        def paginate(self, **kwargs):
            yield {"Contents": keys}

    class _Client:
        def get_paginator(self, _name):
            return _Paginator()

        def delete_objects(self, **kwargs):
            return {"Deleted": [], "Errors": [
                {"Key": k["Key"], "Message": "locked"} for k in keys]}

    _, _, err = uploader.clear_prefix(_Client(), "raw", "network_traffic")
    assert err.count("locked") == 5
    assert "(+7 more)" in err


def test_a_delete_that_cannot_start_is_reported():
    class _Client:
        def get_paginator(self, _name):
            raise _client_error("AccessDenied", "ListObjectsV2")

    count, freed, err = uploader.clear_prefix(_Client(), "raw", "network_traffic")
    assert (count, freed) == (0, 0)
    assert err
