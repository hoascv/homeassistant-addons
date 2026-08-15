# Changelog

## 1.1.0

- The console is now also reachable through Home Assistant's own sidebar
  (`ingress: true`), not just `http://<home-assistant-host-ip>:9001` directly.
  Fronted by nginx on an internal-only port, since the console's own HTML
  needs one thing rewritten to work under Home Assistant's generated ingress
  path: MinIO derives every API call, websocket URL and asset path from
  `document.baseURI`, which comes from a single `<base href="/">` tag in the
  page — nginx rewrites just that, and everything downstream falls into
  place on its own. Also strips `X-Frame-Options: DENY`, which MinIO sends by
  default and which would otherwise make the browser refuse to render the
  console inside Home Assistant's ingress iframe at all.
- nginx runs as a best-effort layer: if it fails to start (most likely
  because the ingress path could not be read from the Supervisor), the S3
  API and the console both keep working exactly as before at `:9000`/`:9001`
  — only the sidebar entry is affected.
- The console's own login (`root_user`/`root_password`) is unchanged and
  still required either way; ingress only adds a second door in, it does not
  replace MinIO's own authentication.

## 1.0.2

- Documentation fix: corrected the opening line. This is a seven-add-on
  pipeline, not four — Metastore, Notebook and Postgres Replica were missing.

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. MinIO S3-compatible object storage: S3 API on host port 9000,
  console on 9001.
- Creates default buckets (`raw`, `staging`, `curated`) on start; objects persisted
  in `/data/minio`. amd64 only.
