# Changelog

## 1.0.0

- First release. JupyterLab opening directly on `/share/pipeline-airflow`, with
  `lib/` on `PYTHONPATH` and `pyspark-client` + `delta-spark` baked in — so a
  notebook can drive the Spark cluster and read the Delta lakehouse without a
  JVM, a Spark installation, or a `sys.path` line.
- **Ingress only, with no published port.** A notebook server executes arbitrary
  code as root on the Home Assistant host; Home Assistant's own login is the
  gate, and nothing is exposed to the LAN.
- The stack's addresses and credentials arrive as environment variables from the
  add-on options, so a fresh notebook needs to be told nothing.
