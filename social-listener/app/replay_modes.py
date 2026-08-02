"""Replay the channel's recorded posts through the state machine in BOTH position
modes and report where the decisions differ.

This is how to answer "what would multi-position actually have changed?" before
switching the account to hedge mode, and how to check afterwards that it changed
what was expected. Read-only: it reads social_signal_log, sends no orders, and
writes nothing.

    docker compose exec -e DAYS=60 social-listener python -m app.replay_modes

Both runs use phase="backfill", which skips the price/staleness gates — those need
a live mark price that cannot be reconstructed from the log — so the comparison is
over the leg logic itself: entries, flips and exits.
"""
import asyncio
import os

from app import db
from app.legs import Legs
from app.statemachine import evaluate

DAYS = int(os.environ.get("DAYS", "60"))


async def main():
    await db.init_db()
    async with db.pool().acquire() as c:
        rows = await c.fetch(
            """SELECT channel_msg_id, posted_at, is_actionable, action_type, asset,
                      direction, reference_price, confidence, size_fraction,
                      trigger_price, stop_price, stop_to_breakeven, take_profit_price,
                      add_multiple
               FROM public.social_signal_log
               WHERE source=$1 AND posted_at >= now() - ($2 || ' days')::interval
               ORDER BY channel_msg_id""",
            db.settings.source_tag, str(DAYS),
        )

    recs = [dict(r) for r in rows if r["is_actionable"]]
    print(f"{len(rows)} posts in {DAYS}d, {len(recs)} actionable\n")

    # Trims and adds are refused under backfill by design (they would re-fire a
    # real reduce on every restart), so they show as "none" on both sides.
    state = {False: {}, True: {}}
    diverged = []
    for r in recs:
        asset = (r["asset"] or "").upper() or None
        if not asset:
            continue
        out = {}
        for multi in (False, True):
            legs = state[multi].get(asset, Legs())
            d = evaluate(r, "backfill", legs, None, None, now=r["posted_at"],
                         multi=multi)
            if d["advance"]:
                nxt = legs
                for side, is_open in d["advance_legs"].items():
                    nxt = nxt.with_side(side, is_open)
                state[multi][asset] = nxt
            out[multi] = d
        if out[False]["intended_signal"] != out[True]["intended_signal"]:
            diverged.append((r, out))

    print(f"decisions that differ between net and hedge: {len(diverged)}\n")
    for r, out in diverged:
        print(f"  msg {r['channel_msg_id']} {r['posted_at']:%Y-%m-%d %H:%M} "
              f"{r['asset']} {r['action_type']} {r['direction']}")
        print(f"      net   : {out[False]['intended_signal']:<14} "
              f"{out[False]['from_state']} -> {out[False]['to_state']}")
        print(f"      hedge : {out[True]['intended_signal']:<14} "
              f"{out[True]['from_state']} -> {out[True]['to_state']}")

    print()
    for multi in (False, True):
        label = "hedge" if multi else "net  "
        final = {a: l.label() for a, l in state[multi].items()}
        print(f"final recorded legs ({label}): {final}")


if __name__ == "__main__":
    asyncio.run(main())
