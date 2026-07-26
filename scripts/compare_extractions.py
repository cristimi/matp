"""Compare the per-message verdicts of N independent v2 extraction runs."""
import json
import sys

FIELDS = ("is_actionable", "action_type", "asset", "direction", "reference_price")

runs = []
for p in sys.argv[1:]:
    with open(p) as f:
        runs.append({r["channel_msg_id"]: r for r in json.load(f)})

ids = sorted(set().union(*(set(r) for r in runs)))
print(f"runs={len(runs)}  messages={len(ids)}")

disagree_any = []
disagree_actionable = []
conf_swings = []

for mid in ids:
    rows = [r.get(mid) for r in runs]
    if any(x is None for x in rows):
        disagree_any.append((mid, "missing in a run"))
        continue
    diffs = []
    for f in FIELDS:
        vals = {json.dumps(x.get(f)) for x in rows}
        if len(vals) > 1:
            diffs.append(f"{f}={'|'.join(sorted(vals))}")
    if diffs:
        disagree_any.append((mid, "; ".join(diffs)))
    if len({bool(x.get("is_actionable")) for x in rows}) > 1:
        disagree_actionable.append(mid)
    confs = [float(x.get("confidence") or 0) for x in rows]
    spread = max(confs) - min(confs)
    if spread > 0:
        conf_swings.append((mid, min(confs), max(confs), spread,
                            bool(rows[0].get("is_actionable"))))

stable = len(ids) - len(disagree_any)
print(f"identical across all runs: {stable}/{len(ids)} ({stable/len(ids)*100:.1f}%)")
print(f"disagree on any field:     {len(disagree_any)}")
print(f"disagree on is_actionable: {len(disagree_actionable)} -> {disagree_actionable}")
print(f"confidence moved at all:   {len(conf_swings)}")

print("\n-- messages whose verdict fields differ --")
for mid, d in disagree_any:
    print(f"  {mid}: {d}")

print("\n-- largest confidence swings --")
for mid, lo, hi, sp, act in sorted(conf_swings, key=lambda x: -x[3])[:12]:
    print(f"  {mid}: {lo:.2f} -> {hi:.2f}  (spread {sp:.2f}) actionable={act}")
