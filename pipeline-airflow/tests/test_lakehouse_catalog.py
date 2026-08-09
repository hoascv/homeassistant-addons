"""The catalog helpers, and specifically what they do with no catalog.

The Pipeline Metastore add-on is optional and Spark ships with it switched off,
so the *absence* of a catalog is the default configuration rather than an edge
case. These run without a JVM: `catalog_available` only asks the session for a
conf value, which a stub can answer.
"""
import pytest

# At module scope for the same reason test_merge_spark.py does it: lakehouse.py
# imports pyspark at import time, so a machine without the package must skip
# this file at collection rather than error and take the whole run down. No JVM
# is needed beyond that — the stub below answers everything these tests ask.
pytest.importorskip("pyspark")

from lakehouse import catalog_available, register  # noqa: E402


class _Session:
    """Just enough SparkSession to answer a conf lookup.

    Keyed, not a single value for every question. It used to answer any key with
    the same string, which was fine while `catalog_available` consulted one
    conf — once it began consulting the metastore URI as well, an "in-memory"
    session cheerfully reported an "in-memory" metastore URI and read as a
    working catalog. The stub has to distinguish the two confs because the code
    under test does.
    """

    def __init__(self, impl, metastore_uris=None):
        self._confs = {}
        if impl is not None:
            self._confs["spark.sql.catalogImplementation"] = impl
        if metastore_uris is not None:
            self._confs["spark.hadoop.hive.metastore.uris"] = metastore_uris
        self.statements = []

    class _Conf:
        def __init__(self, confs):
            self._confs = confs

        def get(self, key):
            if key not in self._confs:
                # Spark raises rather than returning a default for an unset conf.
                raise Exception(f"no such conf: {key}")
            return self._confs[key]

    @property
    def conf(self):
        return self._Conf(self._confs)

    def sql(self, statement):
        self.statements.append(statement)


def test_hive_catalog_is_detected():
    assert catalog_available(_Session("hive")) is True


def test_in_memory_catalog_is_not_a_catalog():
    assert catalog_available(_Session("in-memory")) is False


def test_a_metastore_uri_alone_is_enough():
    """catalogImplementation is a static SQL conf that a Connect session does
    not reliably surface, so the URI is the second, independent way to tell."""
    assert catalog_available(
        _Session("in-memory", metastore_uris="thrift://abc123-pipeline-metastore:9083")
    ) is True


def test_an_empty_metastore_uri_is_not_a_catalog():
    """Spark's own default when the option is left blank."""
    assert catalog_available(_Session("in-memory", metastore_uris="")) is False


def test_unset_conf_is_not_fatal():
    """An older or stripped-down session may not answer at all; that is a no,
    not a crash — this runs inside notebooks people are editing."""
    assert catalog_available(_Session(None)) is False


def test_register_refuses_without_a_catalog():
    session = _Session("in-memory")
    with pytest.raises(RuntimeError) as exc:
        register(session)
    # The message has to name the fix: whoever hits this has the metastore
    # add-on switched off, and the option to change is not in this repo.
    assert "metastore_uris" in str(exc.value)
    assert session.statements == [], "nothing should be created on the way out"


@pytest.mark.spark
def test_real_session_without_metastore_reports_no_catalog(spark):
    """The fixture is a plain local Spark, which is what the pipeline looks like
    before the metastore add-on is installed."""
    assert catalog_available(spark) is False
    with pytest.raises(RuntimeError):
        register(spark)


def test_a_connect_session_that_hides_the_static_conf_is_still_detected():
    """spark.sql.catalogImplementation is a static SQL conf fixed at server
    start, and a Spark Connect session does not reliably surface those — so a
    correctly configured cluster answered "no catalog" and register() refused.
    The metastore URI is written at the same moment and is an ordinary conf."""
    class _Session:
        class _Conf:
            def get(self, key):
                if key == "spark.hadoop.hive.metastore.uris":
                    return "thrift://abc123-pipeline-metastore:9083"
                raise Exception(f"no such conf: {key}")
        conf = _Conf()

    assert catalog_available(_Session()) is True


def test_neither_conf_still_means_no_catalog():
    class _Session:
        class _Conf:
            def get(self, key):
                raise Exception(f"no such conf: {key}")
        conf = _Conf()

    assert catalog_available(_Session()) is False
