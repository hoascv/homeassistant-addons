# Changelog

## 1.0.0

- First release. An ingress dashboard and one Home Assistant sensor per add-on,
  reporting the health of the other add-ons in this repository.
- Supervisor state, CPU, memory, installed version and update-available for
  each, plus a service probe over the add-on's own hostname — so an add-on
  whose container runs while the service inside it is dead reports `degraded`
  rather than healthy.
- `sensor.addon_watchdog_unhealthy` carries a count and the list of degraded
  add-ons, for a single automation that keeps working as add-ons are added.
- Stopped add-ons are not counted as unhealthy by default: most of the pipeline
  is `boot: manual` and is stopped on purpose.
