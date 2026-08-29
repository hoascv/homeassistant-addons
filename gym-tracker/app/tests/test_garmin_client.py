"""garmin_client.py on its own: the login handshake and the normalizers.

test_garmin.py drives the sync through a monkeypatched `garmin_client`, which
is the right level for the sync but leaves this module's own contents untested.
This covers them directly, still without importing the real `garminconnect`
stack — `_new_client` is the seam, and every test replaces it.

The normalizers get most of the attention because the unofficial API's shapes
drift: every one of them is written so that a missing key, a null, a string
where a number belongs, or an outright exception costs one metric rather than
the whole day. That property is only true if it is tested, since none of it
shows up until Garmin changes something.
"""
import os

import pytest

import app as gymapp

garmin_client = gymapp.garmin_client


@pytest.fixture(autouse=True)
def _tokenstore(monkeypatch, tmp_path):
    """Never touch the real /data/garmin, and start every test disconnected."""
    monkeypatch.setattr(garmin_client, "TOKENSTORE", str(tmp_path / "garmin"))
    monkeypatch.setattr(garmin_client, "_pending", None)
    yield
    monkeypatch.setattr(garmin_client, "_pending", None)


class _Tokens:
    """Stands in for the library's client.dump()."""

    def dump(self, path):
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "garmin_tokens.json"), "w") as handle:
            handle.write("{}")


class _Garmin:
    """A stand-in for garminconnect.Garmin."""

    def __init__(self, login_result=("ok", None), **kwargs):
        self.client = _Tokens()
        self.kwargs = kwargs
        self._login_result = login_result
        self.resumed_with = None
        self.logged_in_from = None

    def login(self, tokenstore=None):
        self.logged_in_from = tokenstore
        return self._login_result

    def resume_login(self, client_state, code):
        self.resumed_with = (client_state, code)
        return ("ok", None)


# --- the login handshake ------------------------------------------------------


def test_a_login_without_2fa_saves_tokens_immediately(monkeypatch):
    made = _Garmin(login_result=("ok", None))
    monkeypatch.setattr(garmin_client, "_new_client", lambda **kwargs: made)

    assert garmin_client.begin_login("a@b.c", "pw") == {"status": "connected"}
    assert garmin_client.is_connected() is True


def test_the_password_is_never_persisted(monkeypatch, tmp_path):
    """The auth model's whole claim: the password buys OAuth tokens once and is
    then gone. A password reaching the token directory would survive restarts,
    updates and every backup."""
    made = _Garmin(login_result=("ok", None))
    monkeypatch.setattr(garmin_client, "_new_client", lambda **kwargs: made)
    garmin_client.begin_login("a@b.c", "hunter2")

    written = (tmp_path / "garmin" / "garmin_tokens.json").read_text()
    assert "hunter2" not in written


def test_a_login_needing_2fa_stops_and_waits(monkeypatch):
    """Tokens must not be written here — there is nothing to write yet, and
    is_connected() keying off the file would otherwise report a half-login."""
    made = _Garmin(login_result=("needs_mfa", {"state": 1}))
    monkeypatch.setattr(garmin_client, "_new_client", lambda **kwargs: made)

    assert garmin_client.begin_login("a@b.c", "pw") == {"status": "mfa_required"}
    assert garmin_client.is_connected() is False


def test_the_code_is_handed_to_the_client_that_started_the_login(monkeypatch):
    """The live client from step one has to survive until step two; a fresh
    client would have no idea which challenge the code answers."""
    made = _Garmin(login_result=("needs_mfa", {"state": 1}))
    monkeypatch.setattr(garmin_client, "_new_client", lambda **kwargs: made)
    garmin_client.begin_login("a@b.c", "pw")

    assert garmin_client.complete_mfa(" 123456 ") == {"status": "connected"}
    assert made.resumed_with == ({"state": 1}, "123456")
    assert garmin_client.is_connected() is True


def test_a_code_with_no_login_in_flight_is_refused():
    """After a restart the pending client is gone; asking for the code again is
    the only honest answer."""
    with pytest.raises(RuntimeError, match="no login is awaiting"):
        garmin_client.complete_mfa("123456")


def test_the_pending_login_is_cleared_once_it_completes(monkeypatch):
    """Otherwise a stale client sits in module state until the next restart, and
    a second code would be posted against a spent challenge."""
    made = _Garmin(login_result=("needs_mfa", {"state": 1}))
    monkeypatch.setattr(garmin_client, "_new_client", lambda **kwargs: made)
    garmin_client.begin_login("a@b.c", "pw")
    garmin_client.complete_mfa("123456")

    with pytest.raises(RuntimeError):
        garmin_client.complete_mfa("123456")


def test_disconnecting_drops_a_pending_login_as_well_as_the_tokens(monkeypatch):
    """Disconnect mid-2FA is exactly when someone has decided not to go on."""
    made = _Garmin(login_result=("needs_mfa", {"state": 1}))
    monkeypatch.setattr(garmin_client, "_new_client", lambda **kwargs: made)
    garmin_client.begin_login("a@b.c", "pw")

    garmin_client.disconnect()
    with pytest.raises(RuntimeError):
        garmin_client.complete_mfa("123456")


def test_disconnecting_when_never_connected_is_not_an_error():
    garmin_client.disconnect()
    assert garmin_client.is_connected() is False


def test_a_client_is_built_from_the_saved_tokens(monkeypatch):
    """The background sync reloads tokens rather than logging in again, which is
    what keeps a password out of the picture after the first connect."""
    made = _Garmin()
    monkeypatch.setattr(garmin_client, "_new_client", lambda **kwargs: made)
    garmin_client.begin_login("a@b.c", "pw")

    client = garmin_client.get_client()
    assert client is made
    assert client.logged_in_from == garmin_client.TOKENSTORE


def test_asking_for_a_client_while_disconnected_raises():
    with pytest.raises(RuntimeError, match="not connected"):
        garmin_client.get_client()


# --- numbers off an API that drifts -------------------------------------------


@pytest.mark.parametrize("value,expected", [
    (7.6, 8), ("42", 42), (0, 0), (None, None), ("", None), ("n/a", None), ([], None),
])
def test_numbers_are_coerced_or_dropped(value, expected):
    assert garmin_client._num(value) == expected


# --- sleep --------------------------------------------------------------------


class _Client:
    """Returns whatever it was given, or raises if given an exception."""

    def __init__(self, **responses):
        self._responses = responses

    def _answer(self, name, *args):
        value = self._responses.get(name)
        if isinstance(value, Exception):
            raise value
        return value

    def get_sleep_data(self, day):
        return self._answer("sleep")

    def get_all_day_stress(self, day):
        return self._answer("stress")

    def get_user_summary(self, day):
        return self._answer("summary")

    def get_body_battery(self, start, end):
        return self._answer("body_battery")

    def get_heart_rates(self, day):
        return self._answer("heart_rates")

    def get_activities_by_date(self, start, end):
        return self._answer("activities")

    def get_device_last_used(self):
        return self._answer("device")


def test_sleep_stages_and_resting_hr_are_flattened():
    client = _Client(sleep={
        "dailySleepDTO": {
            "sleepTimeSeconds": 27000, "deepSleepSeconds": 5400,
            "lightSleepSeconds": 16200, "remSleepSeconds": 3600,
            "awakeSleepSeconds": 1800,
            "sleepScores": {"overall": {"value": 81}},
        },
        "restingHeartRate": 48,
    })
    fields = garmin_client._sleep_fields(client, "2026-08-23")
    assert fields["sleep_seconds"] == 27000
    assert fields["sleep_deep_seconds"] == 5400
    assert fields["sleep_score"] == 81
    assert fields["resting_hr"] == 48


def test_a_device_that_reports_no_sleep_score_still_reports_the_durations():
    """The documented case: 22 fields of durations and no `sleepScores` key
    anywhere. Losing the durations too would be the wrong trade."""
    client = _Client(sleep={"dailySleepDTO": {"sleepTimeSeconds": 27000}})
    fields = garmin_client._sleep_fields(client, "2026-08-23")
    assert fields["sleep_seconds"] == 27000
    assert fields["sleep_score"] is None


def test_a_sleep_score_outside_nought_to_a_hundred_is_rejected():
    """A sentinel like -1 graphed as a score would be worse than a gap."""
    client = _Client(sleep={"dailySleepDTO": {"sleepScores": {"overall": {"value": 255}}}})
    assert garmin_client._sleep_fields(client, "2026-08-23")["sleep_score"] is None


def test_a_sleep_score_at_the_top_level_is_read_too():
    """Some accounts carry it beside the DTO rather than inside it."""
    assert garmin_client._sleep_score({"sleepScores": {"overall": {"value": 70}}}) == 70


@pytest.mark.parametrize("payload", [
    {"dailySleepDTO": {"sleepScores": "not a dict"}},
    {"dailySleepDTO": {"sleepScores": {"overall": "not a dict"}}},
    {},
])
def test_an_unexpected_sleep_score_shape_is_no_score(payload):
    assert garmin_client._sleep_score(payload) is None


def test_sleep_that_raises_costs_only_sleep():
    client = _Client(sleep=RuntimeError("garmin 500"))
    assert garmin_client._sleep_fields(client, "2026-08-23") == {}


def test_sleep_that_is_not_a_dict_is_ignored():
    assert garmin_client._sleep_fields(_Client(sleep=["unexpected"]), "2026-08-23") == {}


# --- stress -------------------------------------------------------------------


def test_stress_is_flattened_to_average_and_max():
    client = _Client(stress={"avgStressLevel": 31, "maxStressLevel": 94})
    assert garmin_client._stress_fields(client, "2026-08-23") == {
        "stress_avg": 31, "stress_max": 94,
    }


def test_stress_that_raises_costs_only_stress():
    assert garmin_client._stress_fields(_Client(stress=RuntimeError("boom")), "d") == {}


# --- one whole day ------------------------------------------------------------


def test_a_day_merges_every_source(monkeypatch):
    client = _Client(
        sleep={"dailySleepDTO": {"sleepTimeSeconds": 27000}, "restingHeartRate": 48},
        stress={"avgStressLevel": 31, "maxStressLevel": 94},
        summary={"bodyBatteryHighestValue": 88, "bodyBatteryLowestValue": 21},
    )
    fields = garmin_client.fetch_day(client, "2026-08-23")
    assert fields["sleep_seconds"] == 27000
    assert fields["stress_avg"] == 31
    assert fields["body_battery_high"] == 88


def test_one_broken_source_does_not_sink_the_others():
    """The property the whole module is written around."""
    client = _Client(
        sleep=RuntimeError("garmin 500"),
        stress={"avgStressLevel": 31, "maxStressLevel": 94},
        summary={"bodyBatteryHighestValue": 88},
    )
    fields = garmin_client.fetch_day(client, "2026-08-23")
    assert "sleep_seconds" not in fields
    assert fields["stress_avg"] == 31
    assert fields["body_battery_high"] == 88


# --- the heart-rate series ----------------------------------------------------


def test_heart_rate_samples_are_seconds_and_bpm_oldest_first():
    """Garmin sends epoch milliseconds; storing those as seconds would date
    every sample about fifty thousand years into the future."""
    client = _Client(heart_rates={"heartRateValues": [
        [1756000200000, 71], [1756000000000, 64],
    ]})
    assert garmin_client.fetch_heart_rate_series(client, "2026-08-23") == [
        (1756000000.0, 64), (1756000200.0, 71),
    ]


def test_gaps_in_the_series_are_dropped_not_zeroed():
    """Garmin sends null for a gap. A zero would read as a stopped heart."""
    client = _Client(heart_rates={"heartRateValues": [
        [1756000000000, None], [1756000200000, 71], None, [1756000400000],
    ]})
    assert garmin_client.fetch_heart_rate_series(client, "2026-08-23") == [(1756000200.0, 71)]


def test_a_boolean_is_not_a_heart_rate():
    """`True` is an int in Python and would sail through a bare isinstance
    check, storing a heart rate of 1."""
    client = _Client(heart_rates={"heartRateValues": [[1756000000000, True], [True, 60]]})
    assert garmin_client.fetch_heart_rate_series(client, "2026-08-23") == []


def test_a_heart_rate_call_that_fails_is_an_empty_series():
    assert garmin_client.fetch_heart_rate_series(_Client(heart_rates=RuntimeError()), "d") == []
    assert garmin_client.fetch_heart_rate_series(_Client(heart_rates=["x"]), "d") == []


# --- activities ---------------------------------------------------------------


def test_activities_are_normalised_to_flat_rows():
    client = _Client(activities=[{
        "activityId": 12345,
        "startTimeLocal": "2026-08-23 07:15:00",
        "activityType": {"typeKey": "running"},
        "activityName": "Morning Run",
        "duration": 1800.7,
        "distance": 5000.0,
        "calories": 410,
        "averageHR": 148,
        "maxHR": 172,
    }])
    row = garmin_client.fetch_activities(client, "2026-08-23", "2026-08-23")[0]
    assert row["activity_id"] == 12345
    assert row["activity_type"] == "running"
    assert row["duration_sec"] == 1801
    assert row["distance_m"] == 5000.0
    assert row["avg_hr"] == 148


def test_an_activity_with_no_id_is_skipped():
    """The id is the dedupe key for the sync; a row without one would import
    afresh on every pass."""
    client = _Client(activities=[{"activityName": "Mystery"}, {"activityId": 1}])
    rows = garmin_client.fetch_activities(client, "a", "b")
    assert [r["activity_id"] for r in rows] == [1]


def test_an_activity_missing_its_optional_fields_still_imports():
    client = _Client(activities=[{"activityId": 7}])
    row = garmin_client.fetch_activities(client, "a", "b")[0]
    assert row["activity_type"] is None
    assert row["distance_m"] is None
    assert row["calories"] is None


def test_a_non_numeric_distance_is_dropped_rather_than_stored():
    client = _Client(activities=[{"activityId": 7, "distance": "5 km"}])
    assert garmin_client.fetch_activities(client, "a", "b")[0]["distance_m"] is None


def test_the_gmt_start_time_is_used_when_there_is_no_local_one():
    client = _Client(activities=[{"activityId": 7, "startTimeGMT": "2026-08-23 05:15:00"}])
    assert garmin_client.fetch_activities(client, "a", "b")[0]["start_time"] == "2026-08-23 05:15:00"


def test_an_activities_call_that_fails_is_an_empty_list():
    assert garmin_client.fetch_activities(_Client(activities=RuntimeError()), "a", "b") == []


# --- diagnostics --------------------------------------------------------------


def test_the_sleep_diagnostic_reports_shape_not_content():
    """It exists for an empty score on somebody's real account, so it must be
    safe to paste — keys and the parsed result, never the payload."""
    client = _Client(
        sleep={"dailySleepDTO": {"sleepTimeSeconds": 27000}, "restingHeartRate": 48},
        summary={"sleepingSeconds": 27000},
    )
    out = garmin_client.diagnose_sleep(client, "2026-08-23")
    assert out["day"] == "2026-08-23"
    assert "dailySleepDTO" in out["top_level_keys"]
    assert out["dto_keys"] == ["sleepTimeSeconds"]
    assert out["sleep_scores_keys"] is None
    assert out["parsed_score"] is None
    assert out["sleep_scores_overall"] is None


def test_the_sleep_diagnostic_reports_a_failing_call_rather_than_raising():
    client = _Client(sleep=RuntimeError("garmin 500"), summary=RuntimeError("also 500"))
    out = garmin_client.diagnose_sleep(client, "2026-08-23")
    assert "garmin 500" in out["error"]
    assert "also 500" in out["summary_error"]
