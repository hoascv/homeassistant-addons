# Is the storage the limit?

When writes get slower at certain times of day, there are two explanations and
they need opposite responses:

- **The storage is saturated.** Something is using the device harder than it can
  serve, and your pipeline is queuing behind it. Tuning the pipeline will not
  help.
- **The workload grew.** The pipeline is asking for more than it used to, and the
  device is keeping up. Then the pipeline is where to look.

A maximum settles neither. Knowing the disk can do 900 IOPS does not tell you
that you were at 900 when it hurt. What separates the two is **utilisation and
latency at the moment it was slow**, next to **how much your own pipeline was
asking for**.

The argument that holds up:

> Device utilisation pinned at 100% and average write wait rose from 1 ms to
> 48 ms, while the pipeline add-ons' own I/O rates were unchanged. The extra
> load came from somewhere else.

Two tools produce that, and they answer different questions.

| | **Add-on Watchdog** | **`iobench.sh`** |
|---|---|---|
| When | continuously, unattended | when you run it |
| Load added | none — passive observation | writes real data |
| Runs on | this Home Assistant host | any Linux machine |
| Answers | *was* the disk the limit at 10:00 | what this storage does under a refresh-shaped load |

The add-on catches the event. The script characterises the hardware and lets you
compare machines. Neither replaces the other.

---

## Continuous capture — Add-on Watchdog

Two threads run independently. The scanner does its Supervisor calls and probes
once a minute. A separate sampler reads `/proc/diskstats` every **10 seconds** —
decoupled deliberately, because a 60-second average smooths away exactly the
short stalls worth finding.

Each minute the window closes and publishes both the **mean and the peak**:

| Sensor | Meaning |
|---|---|
| `sensor.addon_watchdog_disk_util` | % of time the device had a request in flight |
| `sensor.addon_watchdog_disk_write_latency_ms` | average ms per write |
| `sensor.addon_watchdog_disk_read_latency_ms` | average ms per read |
| `sensor.addon_watchdog_disk_iops` | operations per second |

They carry `state_class: measurement`, which is what makes Home Assistant's
recorder keep long-term statistics rather than only recent states. That is the
whole retention strategy: **the window records itself with nobody watching.**

Each add-on's own sensor also gains `disk_read_bps` and `disk_write_bps`. That is
the half of the argument that shows the pipeline's demand did *not* rise.

The scan line carries the headline too, so the add-on log alone shows a bad
window:

```
scanned in 13.2s: 7 ok, 1 running | 9 sensors | disk 100.0% peak busy, 48.0ms write wait
```

### Reading it

| busy % | write wait | Reading |
|---|---|---|
| high | high | **saturated** — the device is the limit |
| low | high | slow device, or one shared with something this host cannot see |
| high | low | busy but keeping up |
| low | low | the storage is not the problem; look at the workload |

A measured idle baseline on this host, for reference:

```
Device busy 1.0% peak / 0.7% mean · Write wait 1.2 ms / 0.9 ms · IOPS 41.7 / 21.6
```

That is what "not the storage" looks like. Compare the slow window against it.

### Alerting

Latency **and** utilisation together — high latency alone can just mean a slow
device; high latency while pinned at 100% busy means saturation:

```yaml
automation:
  - alias: "Storage is the bottleneck"
    trigger:
      - platform: numeric_state
        entity_id: sensor.addon_watchdog_disk_write_latency_ms
        above: 20
        for: "00:05:00"
    condition:
      - condition: numeric_state
        entity_id: sensor.addon_watchdog_disk_util
        above: 90
    action:
      - service: notify.persistent_notification
        data:
          message: >
            Disk saturated: {{ states('sensor.addon_watchdog_disk_util') }}% busy,
            {{ states('sensor.addon_watchdog_disk_write_latency_ms') }}ms write wait
```

### The ceiling

The **Measure ceiling** button on the dashboard runs `fio` once: 8 KiB random
read and write, `direct=1`. 8 KiB because that is Postgres' page size, so the
result is comparable to database I/O rather than a sequential MB/s figure nothing
here is limited by; `direct=1` because without it the benchmark measures the page
cache and reports several times too high.

It is **never scheduled** — it writes real data and competes with the live
database. It refuses below 2 GB free and removes its test file even on failure.

The result is a **floor** on the true maximum, not the maximum: it was measured
while everything else was running. Quote it that way.

---

## Point measurement, anywhere — `iobench.sh`

`pipeline-airflow/jobs/iobench.sh`, published to
`/share/pipeline-airflow/lib/iobench.sh` on every start.

```sh
sh /share/pipeline-airflow/lib/iobench.sh --dir /data --size 256
```

Four phases, shaped like a refresh rather than a generic benchmark:

| Phase | Stands in for |
|---|---|
| `bulk` | large sequential writes — Spark writing parquet / Delta files |
| `commit` | small writes, each fsynced — Postgres WAL, the Delta log |
| `read` | sequential read back — a query scanning what was written |
| `mixed` | both at once — what a refresh actually does, and the only phase where contention shows |

Expect `commit` to report far lower MB/s than `bulk`. Measured on one laptop:
**1422 MB/s against 2.9 MB/s on the same disk.** That is not a fault — fsynced
8 KiB writes are latency-bound where bulk writes are throughput-bound, and a
pipeline that commits often lives in the second world.

### On other machines

**POSIX `sh` and coreutils only** — no Python, no packages, nothing to install.
Copy one file:

```sh
scp iobench.sh user@other:/tmp/
ssh user@other 'sh /tmp/iobench.sh --dir /var/tmp --size 256 --json /tmp/r.json'
```

Verified to give identical results under **dash** (Debian/Ubuntu `/bin/sh`),
**ksh** and **bash**. Where systems differ it detects and reports rather than
assuming: no `O_DIRECT` says the figures include the page cache; no
`/proc/diskstats` leaves the device columns at zero and says `device: unknown`;
busybox whole-second timing announces itself.

Use the **same `--size` and `--commits`** on every machine, or the comparison is
meaningless. And check the `cache:` line in each run — a host without `O_DIRECT`
will look far faster than it is.

`notebooks/simulate_io.ipynb` drives it, saves runs by label, and compares a
quiet baseline against one taken during the slow window.

---

## A runbook for the slow window

1. **Before it happens**, install Add-on Watchdog and let it record a normal day.
   Without a baseline there is nothing to compare against, and "48 ms" means
   nothing on its own.
2. **Measure the ceiling** once, on a quiet evening, so saturation can be quoted
   as a fraction rather than an adjective.
3. **When it is slow**, look at the recorder history for
   `disk_util` and `disk_write_latency_ms`. Both elevated is saturation.
4. **Check the pipeline's own demand** — `disk_write_bps` on the pipeline
   add-ons' sensors. Flat or lower while the device is saturated is the finding:
   the load is not yours.
5. **Optionally run `iobench.sh` during the window.** The same workload that took
   30 s at 03:00 taking 4 minutes at 10:00 is a direct, repeatable demonstration.

## What this cannot tell you

**What else is using the disk.** Home Assistant OS exposes little per-process
I/O, so if utilisation is high while every add-on here is idle, the conclusion is
that something outside this view is responsible — which is the finding, not a
gap, but it does not name the culprit. On a VM or network-backed storage, a noisy
neighbour is the usual answer and nothing on the guest can see it.

**Whether the ceiling is stable.** On shared or virtualised storage the maximum
varies between runs. Measure it more than once before trusting a percentage.

**Anything about the pipeline's logic.** These measure the device under load.
A DAG that got slower because it is processing ten times the rows will show
higher demand, not saturation — and that is the case where the pipeline *is* the
answer.
