#!/bin/sh
# Storage behaviour under a pipeline-shaped write load — one file, no Python.
#
# This exists to answer one question on any machine: when writes slow down, is
# the storage the limit or is the workload bigger? A maximum answers neither, so
# each phase reports what it asked of the device *and* what the device was doing
# — utilisation and average wait — because those are what separate "saturated"
# from "asked for more".
#
# Shaped like a full refresh rather than a generic benchmark:
#
#   bulk    large sequential writes, as Spark writing parquet/Delta files
#   commit  small writes each fsynced, as Postgres WAL and the Delta log
#   read    sequential read back, as a query scanning what was just written
#   mixed   bulk and commit at once, which is what a refresh actually does and
#           the only phase where contention shows up
#
# POSIX sh and coreutils only: Home Assistant OS is busybox, and the point is
# that this runs on a machine where you cannot install anything. Device metrics
# come from /proc/diskstats when it exists; without it the workload timings
# still stand, so this degrades rather than refuses.
#
#   ./iobench.sh --dir /var/tmp --size 512 --json results.json
set -eu

DIR="${TMPDIR:-/var/tmp}"
SIZE_MB=512          # per bulk phase
COMMITS=200          # fsynced small writes in the commit phase
BLOCK=8k             # commit write size — Postgres' page size
JSON=""
KEEP=0
DISKSTATS="${DISKSTATS_PATH:-/proc/diskstats}"

usage() {
    cat <<EOF
Usage: $0 [options]
  --dir DIR       where to write the test file (default: $DIR)
  --size MB       megabytes per bulk phase (default: $SIZE_MB)
  --commits N     fsynced small writes in the commit phase (default: $COMMITS)
  --json FILE     also write machine-readable results here
  --keep          do not delete the test file (default: delete)
  -h, --help      this
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="$2"; shift 2 ;;
        --size) SIZE_MB="$2"; shift 2 ;;
        --commits) COMMITS="$2"; shift 2 ;;
        --json) JSON="$2"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

TARGET="$DIR/.iobench.$$"
# Always remove the test file, including on Ctrl-C or an error partway through:
# a stale half-gigabyte is how a disk quietly fills weeks later.
cleanup() { [ "$KEEP" = "1" ] || rm -f "$TARGET"; }
trap cleanup EXIT INT TERM

# --- preflight ----------------------------------------------------------------

[ -d "$DIR" ] || { echo "no such directory: $DIR" >&2; exit 1; }

# Refuse rather than fill the disk. df -k is portable where df -m is not.
FREE_MB=$(df -k "$DIR" 2>/dev/null | awk 'NR==2 {print int($4/1024)}')
NEED_MB=$(( SIZE_MB + 256 ))
if [ -n "$FREE_MB" ] && [ "$FREE_MB" -lt "$NEED_MB" ]; then
    echo "only ${FREE_MB}MB free in $DIR, need ${NEED_MB}MB — refusing" >&2
    exit 1
fi

# O_DIRECT bypasses the page cache, without which this measures RAM and reports
# a number several times too high. Not every filesystem supports it (tmpfs and
# some overlays do not), so it is tested once and the fallback is recorded and
# reported rather than hidden — a cached result is not comparable to a direct one.
DIRECT=""
if dd if=/dev/zero of="$TARGET" bs=4k count=1 oflag=direct 2>/dev/null; then
    DIRECT="oflag=direct"
    CACHE_NOTE="direct I/O (page cache bypassed)"
else
    CACHE_NOTE="NO direct I/O on this filesystem — figures include page cache and flatter the disk"
fi
rm -f "$TARGET"

# Sub-second timing, by whichever route this system offers. /proc/uptime is the
# Linux answer and the one that matters here; `date +%s%N` covers GNU coreutils
# elsewhere; whole seconds is the last resort, and it is announced rather than
# silently making every short phase read as 0.0s.
if [ -r /proc/uptime ]; then
    now_ms() { awk '{printf "%d", $1 * 1000}' /proc/uptime; }
    TIMER="/proc/uptime"
elif [ "$(date +%N 2>/dev/null)" != "%N" ] && [ -n "$(date +%N 2>/dev/null)" ]; then
    now_ms() { echo $(( $(date +%s%N) / 1000000 )); }
    TIMER="date +%s%N"
else
    now_ms() { echo $(( $(date +%s) * 1000 )); }
    TIMER="whole seconds only — short phases will read as 0.0s; raise --size"
fi

# --- device counters ----------------------------------------------------------

DEVICE=""
if [ -r "$DISKSTATS" ]; then
    # df gives the filesystem source (/dev/sda1); diskstats is keyed by kernel
    # device name. Try the exact name, then strip a trailing partition number
    # (sda1 -> sda, nvme0n1p2 -> nvme0n1), which is where the stats usually live.
    SRC=$(df "$DIR" 2>/dev/null | awk 'NR==2 {print $1}' | sed 's#.*/##')
    for cand in "$SRC" "$(echo "$SRC" | sed 's/p\{0,1\}[0-9]*$//')"; do
        [ -n "$cand" ] || continue
        if awk -v d="$cand" '$3 == d {found=1} END {exit !found}' "$DISKSTATS"; then
            DEVICE="$cand"; break
        fi
    done
fi

# Fields 4,6,7,8,10,11,13 after major/minor/name: reads, read sectors, read ms,
# writes, write sectors, write ms, io_ticks.
counters() {
    [ -n "$DEVICE" ] || { echo "0 0 0 0 0 0 0"; return; }
    awk -v d="$DEVICE" '$3 == d {print $4, $6, $7, $8, $10, $11, $13; exit}' "$DISKSTATS"
}

# --- one phase ----------------------------------------------------------------
#
# Results accumulate as lines: name|seconds|mb|util|iops|read_ms|write_ms|mb_s
RESULTS=""

run_phase() {
    name="$1"; expected_mb="$2"; shift 2
    # Counters are space-separated; the results line is pipe-separated, so they
    # are translated here — otherwise all seven land in one field and awk sees
    # five columns instead of seventeen.
    before=$(counters | tr ' ' '|'); t0=$(now_ms)
    "$@" >/dev/null 2>&1 || { echo "phase $name failed" >&2; return 1; }
    t1=$(now_ms); after=$(counters | tr ' ' '|')

    RESULTS="$RESULTS$(printf '%s|%s|%s|%s|%s|%s' \
        "$name" "$expected_mb" "$t0" "$t1" "$before" "$after")
"
}

# --- the workload -------------------------------------------------------------

bulk_write() {
    dd if=/dev/zero of="$TARGET" bs=1M count="$SIZE_MB" $DIRECT conv=fsync
}

bulk_read() {
    # Drop what we can of the cache first; without this the read phase measures
    # memory. Best effort — it needs root, and is simply skipped when it fails.
    sync
    [ -w /proc/sys/vm/drop_caches ] && echo 3 > /proc/sys/vm/drop_caches 2>/dev/null || true
    dd if="$TARGET" of=/dev/null bs=1M $( [ -n "$DIRECT" ] && echo iflag=direct )
}

commit_writes() {
    # Many small writes, each one fsynced: the pattern a transaction log makes,
    # and the one that exposes per-operation latency rather than throughput.
    i=0
    while [ "$i" -lt "$COMMITS" ]; do
        dd if=/dev/zero of="$TARGET" bs="$BLOCK" count=1 seek="$i" \
           conv=fsync,notrunc 2>/dev/null
        i=$(( i + 1 ))
    done
}

mixed() {
    # Bulk and commits at once — a refresh writing data files while the log is
    # being synced. Contention only appears here, and it is the phase that
    # resembles the slow window being investigated.
    bulk_write &
    bulk_pid=$!
    commit_writes
    wait "$bulk_pid" 2>/dev/null || true
}

echo "iobench: $DIR  (${SIZE_MB}MB bulk, ${COMMITS} x ${BLOCK} commits)"
echo "device: ${DEVICE:-unknown — /proc/diskstats unavailable, workload timings only}"
echo "cache:  $CACHE_NOTE"
echo "timer:  $TIMER"
echo

COMMIT_MB=$(awk -v n="$COMMITS" 'BEGIN {printf "%.3f", n * 8 / 1024}')
MIXED_MB=$(awk -v a="$SIZE_MB" -v b="$COMMIT_MB" 'BEGIN {printf "%.3f", a + b}')

run_phase bulk   "$SIZE_MB"   bulk_write
run_phase commit "$COMMIT_MB" commit_writes
run_phase read   "$SIZE_MB"   bulk_read
run_phase mixed  "$MIXED_MB"  mixed

# --- report -------------------------------------------------------------------

echo "$RESULTS" | awk -v size_mb="$SIZE_MB" -v commits="$COMMITS" -v json="$JSON" '
BEGIN {
    FS = "|"
    printf "%-8s %8s %9s %8s %10s %10s %11s\n", \
           "phase", "seconds", "MB/s", "busy%", "IOPS", "read wait", "write wait"
    printf "%-8s %8s %9s %8s %10s %10s %11s\n", \
           "-----", "-------", "----", "-----", "----", "---------", "----------"
    if (json != "") printf "{\"phases\":[" > json
    first = 1
}
NF >= 17 {
    name = $1; expected_mb = $2 + 0; t0 = $3; t1 = $4
    # before: reads rd_sec rd_ms writes wr_sec wr_ms ticks  (fields 5..11)
    # after:  the same, fields 12..18
    secs = (t1 - t0) / 1000.0
    if (secs <= 0) secs = 0.001
    d_reads  = $12 - $5;  d_rsec = $13 - $6;  d_rms = $14 - $7
    d_writes = $15 - $8;  d_wsec = $16 - $9;  d_wms = $17 - $10
    d_ticks  = $18 - $11

    # A counter that went backwards means a reset; report nothing rather than a
    # wild negative that would read as a catastrophic event.
    if (d_reads < 0 || d_writes < 0 || d_ticks < 0) { d_reads = d_writes = d_ticks = 0 }

    # Device bytes when the counters are there; otherwise what the workload
    # itself moved, so throughput is still real on a host with no diskstats.
    mb    = (d_rsec + d_wsec) * 512 / 1048576
    if (mb <= 0) mb = expected_mb
    mb_s  = mb / secs
    util  = (d_ticks / (secs * 1000)) * 100
    if (util > 100) util = 100
    iops  = (d_reads + d_writes) / secs
    rwait = d_reads  > 0 ? d_rms / d_reads  : 0
    wwait = d_writes > 0 ? d_wms / d_writes : 0

    printf "%-8s %8.1f %9.1f %8.1f %10.0f %8.1fms %9.1fms\n", \
           name, secs, mb_s, util, iops, rwait, wwait

    if (json != "") {
        if (!first) printf "," >> json
        printf "{\"phase\":\"%s\",\"seconds\":%.3f,\"mb_s\":%.2f,\"util_percent\":%.1f,\
\"iops\":%.1f,\"read_wait_ms\":%.2f,\"write_wait_ms\":%.2f}", \
               name, secs, mb_s, util, iops, rwait, wwait >> json
        first = 0
    }
}
END {
    if (json != "") { printf "]}\n" >> json }
}'

if [ -n "$JSON" ]; then
    echo
    echo "machine-readable results: $JSON"
fi

echo
echo "Reading this: high write wait with busy% near 100 is the device saturating."
echo "High write wait with low busy% is latency without load — a slow device, or"
echo "one shared with something you cannot see from here."
