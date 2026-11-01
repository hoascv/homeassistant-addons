#!/usr/bin/env python3
"""Pull progress photos out of a Goal Tracker backup, for analysis off-device.

The photos live in the database rather than as files, so a backup is also the
album. This reads one and writes the images out next to a manifest:

    python3 extract_photos.py gym-tracker-backup-20261101-083000.db -o photos/

    photos/
      2026-11-01-front.jpg
      2026-11-01-side.jpg
      manifest.json

`manifest.json` carries each photo's date, angle and notes **and the weigh-in
from that day** — weight and body fat — so a comparison needs one file rather
than a join against the database. Anything the app knows that a picture cannot
show, the manifest should carry.

Nothing is written over: a name already taken gets a numeric suffix.
"""
import argparse, json, os, sqlite3, sys

EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("backup", help="a gym-tracker-backup-*.db file")
    ap.add_argument("-o", "--out", default="photos", help="directory to write into")
    ap.add_argument("--angle", choices=("front", "side", "back"),
                    help="only this angle")
    ap.add_argument("--since", metavar="YYYY-MM-DD", help="only photos on or after this date")
    ap.add_argument("--list", action="store_true", help="show what is there and write nothing")
    args = ap.parse_args()

    if not os.path.exists(args.backup):
        sys.exit(f"no such file: {args.backup}")
    con = sqlite3.connect(f"file:{args.backup}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM progress_photos LIMIT 1")
    except sqlite3.OperationalError:
        sys.exit("this backup has no progress_photos table — it predates the feature")

    where, params = [], []
    if args.angle:
        where.append("angle = ?"); params.append(args.angle)
    if args.since:
        where.append("taken_on >= ?"); params.append(args.since)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = con.execute(
        f"SELECT id, taken_on, angle, mime, notes, LENGTH(image) AS bytes "
        f"FROM progress_photos {clause} ORDER BY taken_on ASC, angle ASC", params
    ).fetchall()
    if not rows:
        sys.exit("nothing matched")

    # The weigh-in for each day, so the manifest answers "what did the number
    # say when this was taken" without a second query.
    weights = {r["day"]: dict(r) for r in con.execute(
        "SELECT substr(ts,1,10) AS day, weight_kg, body_fat_pct FROM weight_logs GROUP BY day")}

    if args.list:
        print(f"{'date':12s} {'angle':6s} {'size':>8s}  weight")
        for r in rows:
            w = weights.get(r["taken_on"]) or {}
            kg = f"{w['weight_kg']} kg" if w.get("weight_kg") else "—"
            print(f"{r['taken_on']:12s} {r['angle']:6s} {r['bytes']//1024:6d} KB  {kg}")
        print(f"\n{len(rows)} photos, {sum(r['bytes'] for r in rows)/1e6:.1f} MB")
        return

    os.makedirs(args.out, exist_ok=True)
    manifest, taken = [], set()
    for r in rows:
        ext = EXT.get(r["mime"], "bin")
        name = f"{r['taken_on']}-{r['angle']}.{ext}"
        n = 2
        while name in taken or os.path.exists(os.path.join(args.out, name)):
            name = f"{r['taken_on']}-{r['angle']}-{n}.{ext}"; n += 1
        taken.add(name)
        blob = con.execute("SELECT image FROM progress_photos WHERE id = ?",
                           (r["id"],)).fetchone()["image"]
        with open(os.path.join(args.out, name), "wb") as f:
            f.write(blob)
        w = weights.get(r["taken_on"]) or {}
        manifest.append({
            "file": name, "taken_on": r["taken_on"], "angle": r["angle"],
            "notes": r["notes"], "bytes": r["bytes"],
            "weight_kg": w.get("weight_kg"), "body_fat_pct": w.get("body_fat_pct"),
        })

    with open(os.path.join(args.out, "manifest.json"), "w") as f:
        json.dump({"source": os.path.basename(args.backup),
                   "photos": manifest}, f, indent=1)
    total = sum(m["bytes"] for m in manifest)
    print(f"wrote {len(manifest)} photos ({total/1e6:.1f} MB) and manifest.json to {args.out}/")
    dated = sum(1 for m in manifest if m["weight_kg"] is not None)
    print(f"{dated} of {len(manifest)} have a weigh-in from the same day")


if __name__ == "__main__":
    main()
