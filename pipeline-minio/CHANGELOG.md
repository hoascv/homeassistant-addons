# Changelog

## 1.0.1

- Set the base image directly in the Dockerfile and removed `build.yaml`
  (deprecated by Supervisor 2026.04.0, which no longer passes `BUILD_FROM`).

## 1.0.0

- First release. MinIO S3-compatible object storage: S3 API on host port 9000,
  console on 9001.
- Creates default buckets (`raw`, `staging`, `curated`) on start; objects persisted
  in `/data/minio`. amd64 only.
