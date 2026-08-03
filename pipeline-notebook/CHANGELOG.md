# Changelog

## 1.0.1

- Fix the 404. 1.0.0 set JupyterLab's `base_url` to the ingress path, on the
  assumption that Home Assistant forwards requests with that prefix intact. It
  does not — it *strips* the prefix, so requests arrived at `/` while Jupyter was
  listening under `/api/hassio_ingress/<token>/` and answered its own 404 page.

  The prefix cannot simply be dropped either: `base_url` is also what JupyterLab
  builds every asset and websocket URL from, so with `/` the browser would
  request assets outside ingress entirely. nginx now sits on the ingress port and
  adds the prefix back on the way to Jupyter, which makes the round trip
  consistent in both directions. This is why the community add-on ships nginx
  too; I should have taken the hint.
- Jupyter now binds loopback only, with nginx accepting from Home Assistant's
  ingress address alone.
- A Supervisor lookup that fails is now fatal rather than falling back to `/`,
  which could only have produced the same broken UI.

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
