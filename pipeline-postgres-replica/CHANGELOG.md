# Changelog

## 1.1.1

- Documentation fix: the notes said the Add-on Watchdog "does not report
  replication lag". It does. The watchdog holds no database credentials, which
  is exactly why this add-on queries its own standby and writes `lag_seconds`
  and its recovery state to `/share/pipeline-status/` every 60 seconds for the
  watchdog to read.

## 1.1.0

- Reports recovery state and replication lag every minute, to the add-on log and
  to `/share/pipeline-status/pipeline-postgres-replica.json` for Add-on Watchdog
  1.8.0. A standby that has silently stopped replaying still accepts connections
  and still serves stale data, so a port probe alone cannot catch it.
- Being promoted now reads as not-ok rather than healthy: promoted by hand or by
  accident, it is no longer a replica of anything.

## 1.0.0

- First release. A streaming read-only standby of Pipeline Postgres on host
  port 5433, for read queries that would otherwise compete with the pipeline
  and for fast promotion after a corrupted data directory or a bad upgrade.
- Clones the primary with `pg_basebackup` on first start, creating a
  replication slot so the primary keeps the WAL this replica has not consumed.
  A failed clone wipes the directory rather than leaving it half-copied.
- Requires Pipeline Postgres 1.2.0 or later, and the same TimescaleDB build —
  physical replication ships WAL, so the standby has to load the same
  extension.
- Not a backup and not off-host: it replays mistakes faithfully and shares the
  primary's disk. The primary's `backup_enabled` covers those.
