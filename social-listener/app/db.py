import json
import logging
from datetime import datetime, timezone

import asyncpg

from app.config import settings
from app.legs import Legs

log = logging.getLogger(__name__)
_pool: asyncpg.Pool | None = None


async def init_db():
    global _pool
    _pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    log.info("DB pool initialized")


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized")
    return _pool


async def max_channel_msg_id() -> int | None:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT max(channel_msg_id) AS m FROM public.social_signal_log WHERE source=$1",
            settings.source_tag,
        )
        return row["m"] if row else None


async def already_seen(channel_msg_id: int) -> bool:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT 1 FROM public.social_signal_log WHERE source=$1 AND channel_msg_id=$2",
            settings.source_tag, channel_msg_id,
        )
        return row is not None


async def already_shadow_evaluated(channel_msg_id: int) -> bool:
    async with pool().acquire() as c:
        row = await c.fetchrow(
            "SELECT 1 FROM public.social_shadow_orders WHERE source=$1 AND channel_msg_id=$2",
            settings.source_tag, channel_msg_id,
        )
        return row is not None


async def load_signal(channel_msg_id: int) -> dict | None:
    """Load an already-extracted record from social_signal_log for brain replay."""
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT channel_msg_id, posted_at, is_actionable, action_type,
                      asset, direction, reference_price, confidence,
                      size_fraction, trigger_price, stop_price, stop_to_breakeven,
                      take_profit_price, add_multiple
               FROM public.social_signal_log
               WHERE source=$1 AND channel_msg_id=$2""",
            settings.source_tag, channel_msg_id,
        )
        if row is None:
            return None
        return dict(row)


async def get_legs(asset: str) -> Legs:
    """Which legs of `asset` the channel is recorded as holding.

    A missing row is a flat leg, so an asset never seen before reads as flat on both
    sides — the same contract the single-stance `get_state` had.
    """
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT side FROM public.social_position_state
               WHERE source=$1 AND asset=$2 AND state='OPEN'""",
            settings.source_tag, asset,
        )
    return Legs.from_sides(r["side"] for r in rows)


async def recorded_open_legs() -> list[dict]:
    """Every leg this listener currently believes it holds, across all assets.

    `get_legs` answers the same question for one asset and returns a `Legs`; this
    returns the rows themselves, because the reconcile sweep needs `updated_at` to
    tell a leg that has gone stale from one that was opened a second ago.
    """
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT asset, side, last_msg_id, updated_at
               FROM public.social_position_state
               WHERE source=$1 AND state='OPEN'
               ORDER BY updated_at""",
            settings.source_tag,
        )
    return [dict(r) for r in rows]


async def open_leg(asset: str, side: str, msg_id: int) -> None:
    """Record a leg as open, with no levels yet.

    Levels are cleared rather than carried: an existing row for this side means a
    previous trade on it, and its stop belonged to that trade's entry price.
    """
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO public.social_position_state
                 (source, asset, side, state, last_msg_id, updated_at)
               VALUES ($1,$2,$3,'OPEN',$4, now())
               ON CONFLICT (source, asset, side) DO UPDATE
                 SET state='OPEN', last_msg_id=$4, stop_price=NULL, tp_price=NULL,
                     stop_mode=NULL, updated_at=now()""",
            settings.source_tag, asset, side, msg_id,
        )


async def close_leg(asset: str, side: str, msg_id: int) -> None:
    """Record a leg as flat.

    The row is deleted, not set to FLAT: absence IS flat everywhere else here, and
    keeping a husk around would leave its stale stop_price visible to any query that
    forgot to filter on state.
    """
    async with pool().acquire() as c:
        await c.execute(
            """DELETE FROM public.social_position_state
               WHERE source=$1 AND asset=$2 AND side=$3""",
            settings.source_tag, asset, side,
        )


async def apply_leg_changes(asset: str, changes: dict, msg_id: int) -> None:
    """Apply a decision's `advance_legs` — {side: is_open} — in one go.

    Closes run before opens so a flip frees its old leg before the new one lands;
    they are different rows, so the order only matters for how the intermediate
    state reads to a concurrent watcher pass.
    """
    for side, is_open in sorted(changes.items(), key=lambda kv: kv[1]):
        if is_open:
            await open_leg(asset, side, msg_id)
        else:
            await close_leg(asset, side, msg_id)


async def get_levels(asset: str, side: str) -> dict:
    """What this listener has set for ONE leg: stop, take-profit, and whether a
    standing 'keep me at break-even' intent is in force.

    Per leg because a long's break-even and a short's are different numbers — the
    single-row version could only ever hold one of them.
    """
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT stop_price, tp_price, stop_mode
               FROM public.social_position_state
               WHERE source=$1 AND asset=$2 AND side=$3""",
            settings.source_tag, asset, side,
        )
    if row is None:
        return {"stop_price": None, "tp_price": None, "stop_mode": None}
    return {
        "stop_price": float(row["stop_price"]) if row["stop_price"] is not None else None,
        "tp_price": float(row["tp_price"]) if row["tp_price"] is not None else None,
        "stop_mode": row["stop_mode"],
    }


async def set_levels(asset: str, side: str, stop_price: float | None = None,
                     tp_price: float | None = None,
                     stop_mode: str | None = None) -> None:
    """Record levels we have just confirmed on one leg. Only non-None arguments are
    written, so setting a take-profit never forgets the stop and vice versa."""
    sets, args = [], [settings.source_tag, asset, side]
    for col, val in (("stop_price", stop_price), ("tp_price", tp_price),
                     ("stop_mode", stop_mode)):
        if val is not None:
            args.append(val)
            sets.append(f"{col}=${len(args)}")
    if not sets:
        return
    async with pool().acquire() as c:
        await c.execute(
            f"""UPDATE public.social_position_state SET {', '.join(sets)}, updated_at=now()
                WHERE source=$1 AND asset=$2 AND side=$3""",
            *args,
        )


async def clear_stop_price(asset: str, side: str) -> None:
    """Forget the stop we had set on one leg, keeping any standing intent.

    Used after a scale-in: the blended entry price moved, so a break-even stop now
    means a different number and the watcher must be free to re-assert it.
    """
    async with pool().acquire() as c:
        await c.execute(
            """UPDATE public.social_position_state SET stop_price=NULL, updated_at=now()
               WHERE source=$1 AND asset=$2 AND side=$3""",
            settings.source_tag, asset, side,
        )


async def legs_with_standing_stop() -> list[dict]:
    """Legs whose trader asked to stay de-risked, for the watcher to re-assert.

    Each leg is returned separately: with a long and a short both de-risked, they
    need two different break-even prices and two separate stop moves.
    """
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT asset, side, stop_price, tp_price, last_msg_id
               FROM public.social_position_state
               WHERE source=$1 AND stop_mode='breakeven' AND state='OPEN'""",
            settings.source_tag,
        )
    return [dict(r) for r in rows]


async def insert_shadow_order(rec: dict) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO public.social_shadow_orders
               (source, channel_msg_id, posted_at, phase, asset, action_type, from_state, to_state,
                intended_signal, reference_price, mark_price, confidence, decision, reason, mode,
                size_fraction, close_size, stop_price, stop_reason,
                tp_price, tp_reason, add_size)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,
                       $20,$21,$22)
               ON CONFLICT (source, channel_msg_id) DO NOTHING""",
            settings.source_tag, rec["channel_msg_id"], rec["posted_at"], rec["phase"], rec["asset"],
            rec["action_type"], rec["from_state"], rec["to_state"], rec["intended_signal"],
            rec["reference_price"], rec["mark_price"], rec["confidence"], rec["decision"],
            rec["reason"], rec.get("mode", "shadow"),
            rec.get("size_fraction"), rec.get("close_size"),
            rec.get("stop_price"), rec.get("stop_reason"),
            rec.get("tp_price"), rec.get("tp_reason"), rec.get("add_size"),
        )


async def resolve_shadow_order(channel_msg_id: int, decision: str, reason: str,
                               mark: float | None, close_size: float | None,
                               mode: str) -> None:
    """Update the row a parked trim already wrote, once the watcher settles it.

    social_shadow_orders is one row per post, so a pending trim that later fires
    must overwrite its own row rather than insert a second one.
    """
    async with pool().acquire() as c:
        await c.execute(
            """UPDATE public.social_shadow_orders
               SET decision=$3, reason=$4, mark_price=$5, close_size=$6, mode=$7
               WHERE source=$1 AND channel_msg_id=$2""",
            settings.source_tag, channel_msg_id, decision, reason, mark, close_size, mode,
        )


async def open_position(strategy_id: str, asset: str, side: str | None = None) -> dict | None:
    """The listener strategy's open position in `asset` on `side`, or None.

    `side` is LONG/SHORT and is required in practice: with both legs open, the
    unscoped query returns whichever opened most recently, and every caller here
    goes on to trim it, add to it, or move its stop. Left optional only so a caller
    that genuinely means "the one position" (net-mode paths, diagnostics) can say so.

    Read from strategy_positions, the same record order-listener clamps a partial
    close against — so the size sent and the size it enforces come from one source.
    No exchange call is made here; every venue call stays in order-executor.
    """
    where, args = "", [strategy_id, f"{asset.upper()}%"]
    if side is not None:
        args.append(side.lower())
        where = f" AND lower(p.side)=${len(args)}"
    async with pool().acquire() as c:
        row = await c.fetchrow(
            f"""SELECT p.id, p.symbol, p.side, p.size, p.entry_price, p.opened_at,
                       o.tp_price, o.sl_price
                FROM public.strategy_positions p
                LEFT JOIN public.orders o ON o.id = p.opening_order_id
                WHERE p.strategy_id=$1 AND p.status='open' AND p.symbol LIKE $2{where}
                ORDER BY p.opened_at DESC LIMIT 1""",
            *args,
        )
    return dict(row) if row else None


async def insert_pending_trim(rec: dict, ttl_hours: int) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO public.social_pending_trims
                 (source, channel_msg_id, asset, side, size_fraction, trigger_price, expires_at)
               VALUES ($1,$2,$3,$4,$5,$6, now() + ($7 || ' hours')::interval)
               ON CONFLICT (source, channel_msg_id) DO NOTHING""",
            settings.source_tag, rec["channel_msg_id"], rec["asset"], rec["side"],
            rec["size_fraction"], rec["trigger_price"], str(ttl_hours),
        )


async def record_fired_trim(rec: dict, ttl_hours: int, resolution: str) -> None:
    """Enter an immediately-executed trim in the same ledger parked ones use.

    Without this the ledger only knows about trims that had to wait, and the dedupe
    below cannot see that an instruction was already carried out.
    """
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO public.social_pending_trims
                 (source, channel_msg_id, asset, side, size_fraction, trigger_price,
                  expires_at, status, resolved_at, resolution)
               VALUES ($1,$2,$3,$4,$5,$6, now() + ($7 || ' hours')::interval,
                       'fired', now(), $8)
               ON CONFLICT (source, channel_msg_id) DO NOTHING""",
            settings.source_tag, rec["channel_msg_id"], rec["asset"], rec["side"],
            rec["size_fraction"], rec.get("trigger_price"), str(ttl_hours),
            resolution[:500],
        )


async def trim_already_taken(asset: str, side: str, trigger_price: float | None,
                             since, interval_minutes: int) -> str | None:
    """Why this trim must not run, or None if it may.

    Scoped to the stance by `since` (the open position's opened_at), so trims from
    a trade that has already been and gone never block a new one.
    """
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT channel_msg_id, trigger_price, created_at
               FROM public.social_pending_trims
               WHERE source=$1 AND asset=$2 AND side=$3
                 AND status IN ('pending','fired') AND created_at >= $4
               ORDER BY id DESC""",
            settings.source_tag, asset, side, since,
        )
    if not rows:
        return None

    # Same named level, same stance: the author re-posted the card, not a new call.
    if trigger_price is not None:
        for r in rows:
            if r["trigger_price"] is None:
                continue
            prev = float(r["trigger_price"])
            if abs(prev - float(trigger_price)) / max(abs(prev), 1e-9) < 1e-4:
                return f"trim_already_taken (msg {r['channel_msg_id']} at {prev})"

    newest = rows[0]
    age_min = (datetime.now(timezone.utc) - newest["created_at"]).total_seconds() / 60
    if age_min < interval_minutes:
        return (f"trim_too_soon ({age_min:.1f}m after msg "
                f"{newest['channel_msg_id']}, floor {interval_minutes}m)")
    return None


async def pending_trims() -> list[dict]:
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT id, channel_msg_id, asset, side, size_fraction, trigger_price
               FROM public.social_pending_trims
               WHERE source=$1 AND status='pending'
               ORDER BY id""",
            settings.source_tag,
        )
    return [dict(r) for r in rows]


async def resolve_pending_trim(trim_id: int, status: str, resolution: str) -> None:
    async with pool().acquire() as c:
        await c.execute(
            """UPDATE public.social_pending_trims
               SET status=$2, resolution=$3, resolved_at=now()
               WHERE id=$1 AND status='pending'""",
            trim_id, status, resolution[:500],
        )


async def expire_pending_trims() -> int:
    """Retire levels the market never reached. Returns how many were retired."""
    async with pool().acquire() as c:
        result = await c.execute(
            """UPDATE public.social_pending_trims
               SET status='expired', resolution='ttl elapsed', resolved_at=now()
               WHERE source=$1 AND status='pending' AND expires_at < now()""",
            settings.source_tag,
        )
    return int(result.rsplit(" ", 1)[-1] or 0)


async def cancel_pending_trims(asset: str, side: str, resolution: str) -> int:
    """Drop parked trims for ONE leg that has closed.

    Scoped to the side: a long closing must not cancel a trim parked against a short
    that is still running. That was safe when an asset had one stance and is a bug
    the moment it can have two.
    """
    async with pool().acquire() as c:
        result = await c.execute(
            """UPDATE public.social_pending_trims
               SET status='cancelled', resolution=$4, resolved_at=now()
               WHERE source=$1 AND status='pending' AND asset=$2 AND side=$3""",
            settings.source_tag, asset, side, resolution[:500],
        )
    return int(result.rsplit(" ", 1)[-1] or 0)


async def load_execution_strategy(strategy_id: str) -> dict | None:
    """The strategy row that owns this listener's capital, or None if absent.

    Sizing and the webhook secret are read from it rather than duplicated into
    this service's config — strategies.margin_per_trade / default_leverage stay
    the single place capital rules are edited (Settings page included).
    """
    async with pool().acquire() as c:
        row = await c.fetchrow(
            """SELECT s.id, s.name, s.symbol, s.enabled, s.is_deleted, s.account_id,
                      s.webhook_secret, s.capital_allocation, s.margin_per_trade,
                      s.default_leverage, s.max_leverage, s.margin_mode,
                      COALESCE(a.position_mode, 'net') AS position_mode
               FROM public.strategies s
               LEFT JOIN public.exchange_accounts a ON a.id = s.account_id
               WHERE s.id = $1""",
            strategy_id,
        )
    return dict(row) if row else None


async def account_position_mode(strategy_id: str) -> str:
    """'hedge' or 'net' for the account behind `strategy_id`, defaulting to 'net'.

    Read separately from load_execution_strategy because SHADOW runs need it too:
    a shadow decision that assumed hedge while the account is net would record a
    second leg the live path could never have taken, and the shadow log is what the
    backtest and every "would we have traded this" question read.
    """
    async with pool().acquire() as c:
        mode = await c.fetchval(
            """SELECT a.position_mode FROM public.strategies s
               JOIN public.exchange_accounts a ON a.id = s.account_id
               WHERE s.id = $1""",
            strategy_id,
        )
    return mode if mode in ("net", "hedge") else "net"


async def load_extraction_cache(version: str, days: int) -> dict[int, dict]:
    """Cached backtest extractions for one extractor version, keyed by message id."""
    async with pool().acquire() as c:
        rows = await c.fetch(
            """SELECT channel_msg_id, payload FROM public.social_extraction_cache
               WHERE source=$1 AND extractor_version=$2
                 AND posted_at >= now() - ($3 || ' days')::interval""",
            settings.source_tag, version, str(days),
        )
    return {r["channel_msg_id"]: json.loads(r["payload"]) for r in rows}


async def cache_extraction(rec: dict) -> None:
    """Store one SUCCESSFUL extraction. Failures must never be cached — they have
    to be retried, which is the whole point of the cache surviving an outage."""
    async with pool().acquire() as c:
        await c.execute(
            """INSERT INTO public.social_extraction_cache
                 (source, channel_msg_id, extractor_version, model, posted_at, payload)
               VALUES ($1,$2,$3,$4,$5,$6)
               ON CONFLICT (source, channel_msg_id, extractor_version)
               DO UPDATE SET payload=$6, model=$4, cached_at=now()""",
            settings.source_tag, rec["channel_msg_id"], rec["extractor_version"],
            rec.get("model"), rec["posted_at"], json.dumps(rec, default=str),
        )


async def insert_signal(rec: dict) -> bool:
    """Insert one parsed record. Returns True if a NEW row was written, False if duplicate."""
    async with pool().acquire() as c:
        result = await c.execute(
            """
            INSERT INTO public.social_signal_log
              (source, channel_msg_id, posted_at, raw_text, preview_text, x_url,
               is_actionable, action_type, asset, direction, reference_price,
               confidence, in_whitelist, model, extractor_version, raw_llm_json,
               input_tokens, output_tokens, total_tokens, has_image, image_sha,
               merged_msg_ids, size_fraction, trigger_price, stop_price, stop_to_breakeven,
               take_profit_price, add_multiple)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,
                    $22,$23,$24,$25,$26,$27,$28)
            ON CONFLICT (source, channel_msg_id) DO NOTHING
            """,
            settings.source_tag, rec["channel_msg_id"], rec["posted_at"],
            rec["raw_text"], rec["preview_text"], rec["x_url"],
            rec["is_actionable"], rec["action_type"], rec["asset"],
            rec["direction"], rec["reference_price"], rec["confidence"],
            rec["in_whitelist"], rec["model"], rec["extractor_version"],
            json.dumps(rec["raw_llm_json"]),
            rec.get("input_tokens"), rec.get("output_tokens"), rec.get("total_tokens"),
            rec.get("has_image", False), rec.get("image_sha"),
            rec.get("merged_msg_ids"),
            rec.get("size_fraction"), rec.get("trigger_price"),
            rec.get("stop_price"), rec.get("stop_to_breakeven"),
            rec.get("take_profit_price"), rec.get("add_multiple"),
        )
        return result.endswith("1")
