"""Compare Gemini's extractions against Claude's on the messages both read."""
import asyncio
import json
import sys

import asyncpg

FIELDS = ("is_actionable", "action_type", "asset", "direction", "reference_price")


async def main(path: str, dsn: str):
    with open(path) as f:
        gem = {r["channel_msg_id"]: r for r in json.load(f)}

    conn = await asyncpg.connect(dsn)
    rows = await conn.fetch(
        "SELECT channel_msg_id, payload FROM social_extraction_cache "
        "WHERE extractor_version='v2' AND channel_msg_id = ANY($1)",
        list(gem),
    )
    await conn.close()
    cla = {r["channel_msg_id"]: json.loads(r["payload"]) for r in rows}

    both = sorted(set(gem) & set(cla))
    print(f"gemini records={len(gem)}  claude records={len(cla)}  comparable={len(both)}")

    agree = 0
    for mid in both:
        g, c = gem[mid], cla[mid]
        diffs = [f"{f}: claude={c.get(f)!r} gemini={g.get(f)!r}"
                 for f in FIELDS if g.get(f) != c.get(f)]
        if not diffs:
            agree += 1
            continue
        print(f"\nmsg {mid}  (image={g.get('has_image')})")
        for d in diffs:
            print(f"    {d}")
        print(f"    confidence: claude={c.get('confidence')} gemini={g.get('confidence')}")

    print(f"\nidentical on all {len(FIELDS)} verdict fields: {agree}/{len(both)}"
          f" ({agree/len(both)*100:.0f}%)" if both else "no overlap")

    ga = [m for m in both if gem[m].get("is_actionable")]
    ca = [m for m in both if cla[m].get("is_actionable")]
    print(f"actionable — claude: {len(ca)} {ca}")
    print(f"actionable — gemini: {len(ga)} {ga}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
