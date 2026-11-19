"""sync/03 backfill: give every move on the NAS its original FIT from the Garmin Connect export
that already sits on the NAS. Runs ON the NAS (Python 3.8):

    python3 backfill.py            # dry run: report only
    python3 backfill.py --apply    # write blobs/<sha256(uid)>.fit where none exists yet

A FIT belongs to a move when its start is within 2 min of the move's UTC start (start_utc, or a
start_time that carries its zone). Closest wins; one FIT per move; existing FIT blobs are never
touched. Deleted moves (the store's tombstones) are skipped.
"""
import datetime, hashlib, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fit_decode  # copy tools/fit_decode.py next to this file on the NAS

DATA = "/var/services/web/sommet/sommet-data"
EXPORT = os.environ.get("SOMMET_GARMIN_EXPORT", "/volume1/docker/runalyze")   # where the Garmin export was unpacked
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fit-starts.json")
WINDOW = 120
APPLY = "--apply" in sys.argv


def blob_path(uid):
    return os.path.join(DATA, "blobs", hashlib.sha256(uid.encode()).hexdigest() + ".fit")


def utc_of(rec):
    u = rec.get("start_utc")
    try:
        if u and int(u) > 0:
            return int(u)
    except (TypeError, ValueError):
        pass
    s = str(rec.get("start_time") or "")
    if s.endswith("Z") or "+" in s[10:]:
        try:
            return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return None
    return None


# 1. Start of every export FIT (cached: decoding 6,000 files takes a while on the NAS).
starts = json.load(open(CACHE)) if os.path.exists(CACHE) else {}
paths = []
for root, _dirs, files in os.walk(EXPORT):
    if "@eaDir" in root:
        continue
    paths += [os.path.join(root, f) for f in files if f.lower().endswith(".fit")]
t0 = time.time()
for n, p in enumerate(paths):
    if p in starts:
        continue
    try:
        st = fit_decode.decode(p).get("startTime")
        starts[p] = int(datetime.datetime.fromisoformat(st.replace("Z", "+00:00")).timestamp()) if st else None
    except Exception:
        starts[p] = None
    if n % 250 == 0:
        json.dump(starts, open(CACHE, "w"))
        print("decoded", n, "of", len(paths), int(time.time() - t0), "s", flush=True)
json.dump(starts, open(CACHE, "w"))

# 2. Moves on the NAS.
store = json.load(open(os.path.join(DATA, "activities.json")))
recs = store["records"]
items = recs.items() if isinstance(recs, dict) else ((r.get("uid"), r) for r in recs)
deleted = set(store.get("deleted") or [])
moves = []
for uid, rec in items:
    if not uid or uid in deleted:
        continue
    moves.append((uid, rec, utc_of(rec)))

by_minute = {}
for p, s in starts.items():
    if s:
        by_minute.setdefault(s // 60, []).append((s, p))

have = matched = no_time = unmatched = 0
plan = []
used = set()
for uid, rec, u in moves:
    if os.path.exists(blob_path(uid)):
        have += 1
        continue
    if u is None:
        no_time += 1
        continue
    best = None
    for m in range(u // 60 - 3, u // 60 + 4):
        for s, p in by_minute.get(m, []):
            d = abs(s - u)
            if d <= WINDOW and (best is None or d < best[0]):
                best = (d, p)
    if best is None:
        unmatched += 1
        continue
    matched += 1
    plan.append((uid, best[1], best[0], rec.get("source"), str(rec.get("start_time"))[:16]))
    used.add(best[1])

print("moves", len(moves), "| already have a FIT", have, "| matched", matched,
      "| no export FIT within 2 min", unmatched, "| no UTC start", no_time)
print("export FITs", len(paths), "decoded", sum(1 for v in starts.values() if v),
      "| used", len(used), "| shared by two moves", matched - len(used))
src = {}
for _uid, _p, _d, s, _st in plan:
    src[s] = src.get(s, 0) + 1
print("matched by source", src)
json.dump(plan, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plan.json"), "w"))

if APPLY:
    wrote = 0
    for uid, p, _d, _s, _st in plan:
        dst = blob_path(uid)
        if os.path.exists(dst):
            continue
        tmp = dst + ".tmp"
        with open(p, "rb") as f, open(tmp, "wb") as g:
            g.write(f.read())
        os.chmod(tmp, 0o644)
        os.rename(tmp, dst)
        wrote += 1
    print("wrote", wrote, "FIT blobs")
