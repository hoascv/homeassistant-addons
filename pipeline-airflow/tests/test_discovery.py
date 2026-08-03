"""Where the trackers are, worked out from our own hostname.

A pinned address is the thing that breaks silently when the service moves, so
these pin every hostname shape a real installation can present.
"""
import pytest

from trackers_feed import discover_base_url


@pytest.mark.parametrize(
    "hostname, source, expected",
    [
        # An add-on installed from a git repository: the prefix is the repo hash.
        ("6753e04e-pipeline-airflow", "gym_tracker", "http://6753e04e-gym-tracker:8099"),
        ("6753e04e-pipeline-airflow", "coop_tracker", "http://6753e04e-coop-tracker:8099"),
        # Airflow reports itself fully qualified in some contexts.
        ("6753e04e-pipeline-airflow.local.hass.io", "gym_tracker",
         "http://6753e04e-gym-tracker:8099"),
        # A locally installed copy is prefixed `local`, and the trackers with it.
        ("local-pipeline-airflow", "gym_tracker", "http://local-gym-tracker:8099"),
        # Re-adding the repository changes the hash; we move with it because we
        # are named the same way.
        ("a0d7b954-pipeline-airflow", "coop_tracker", "http://a0d7b954-coop-tracker:8099"),
    ],
)
def test_derives_the_tracker_address_from_our_own_hostname(hostname, source, expected):
    assert discover_base_url(source, hostname=hostname) == expected


@pytest.mark.parametrize(
    "hostname",
    [
        "some-random-box",     # not an add-on at all, e.g. a developer's laptop
        "pipeline-airflow",    # no prefix to borrow
        "-pipeline-airflow",   # degenerate: the prefix would be empty
    ],
)
def test_returns_nothing_when_the_hostname_is_not_an_addons(hostname):
    # None rather than a guess: the caller then falls back to the configured
    # default instead of dialling something that cannot exist.
    assert discover_base_url("gym_tracker", hostname=hostname) is None


def test_underscores_in_the_source_become_hyphens():
    # Sources are named gym_tracker; add-on slugs are gym-tracker.
    url = discover_base_url("gym_tracker", hostname="x-pipeline-airflow")
    assert "gym-tracker" in url and "gym_tracker" not in url
