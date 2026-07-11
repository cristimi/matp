"""
Economic calendar fetcher (scheduled macro events in the next N hours).

External HTTP API with graceful degradation, modeled on news.py / macro.py:
failures are non-fatal — return None on error. Provider: Finnhub
`/calendar/economic` (per spec §8), keyed by settings.finnhub_api_key
(FINNHUB_API_KEY in the compose env).

Ships dormant by design: when the key is unset the fetcher returns None
immediately — the SCHEDULED EVENTS section is simply absent (honest absence),
no crash, no fabricated events. Set FINNHUB_API_KEY to activate.

Distinguishable states, per spec §8:
  - None                      → data missing (no key / provider error) → section absent
  - {'events': [], ...}       → data present, genuinely quiet window →
                                renderer prints "No high-impact events in the window."

Timezone: Finnhub returns naive timestamps — treated as UTC at parse time
(the classic bug source the spec calls out; normalized here, nowhere else).
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.data.cache import ttl_cached

logger = logging.getLogger(__name__)

MAX_EVENTS = 10  # prompt readability cap — nearest events first

# Impact filter: US events at high/medium impact, non-US only at high —
# the spec's "high/medium-impact US+global" read.
_US = ('US',)


def _keep(country: str, impact: str) -> bool:
    if impact == 'high':
        return True
    return impact == 'medium' and country in _US


@ttl_cached(success_ttl=900.0, failure_ttl=300.0)
async def fetch_economic_calendar(horizon_hours: int = 48) -> dict | None:
    """
    Fetch high/medium-impact scheduled macro events within the horizon.
    Process-wide cached (see app.data.cache) — identical across every
    strategy regardless of symbol, and was being refetched independently by
    each one every cycle. `time_until_hours` in the result is computed at
    fetch time, so it can drift up to the cache TTL — acceptable for a
    48h-horizon event list.

    Returns:
        {'events': [{'impact': 'high|medium', 'event_name': str,
                     'time_until_hours': float}, ...],   # soonest first
         'horizon_hours': int}
    or None when the API key is unset or the provider errors.
    """
    api_key = settings.finnhub_api_key
    if not api_key:
        return None  # dormant: no key configured — honest absence

    try:
        now = datetime.now(timezone.utc)
        frm = now.strftime('%Y-%m-%d')
        to  = (now + timedelta(hours=horizon_hours)).strftime('%Y-%m-%d')

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://finnhub.io/api/v1/calendar/economic",
                params={'from': frm, 'to': to, 'token': api_key},
            )
            resp.raise_for_status()
            raw = resp.json().get('economicCalendar') or []

        events = []
        for e in raw:
            impact  = (e.get('impact') or '').lower()
            country = (e.get('country') or '').upper()
            name    = (e.get('event') or '').strip()
            when    = e.get('time') or ''
            if not name or not _keep(country, impact):
                continue
            try:
                ts = datetime.fromisoformat(when).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            hours_away = (ts - now).total_seconds() / 3600
            if 0 <= hours_away <= horizon_hours:
                events.append({
                    'impact':           impact,
                    'event_name':       f"{name} ({country})" if country else name,
                    'time_until_hours': round(hours_away, 1),
                })

        events.sort(key=lambda ev: ev['time_until_hours'])
        return {'events': events[:MAX_EVENTS], 'horizon_hours': horizon_hours}

    except Exception as exc:
        logger.warning("fetch_economic_calendar error: %s", exc)
        return None


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    async def _test():
        result = await fetch_economic_calendar()
        print(json.dumps(result, indent=2))

    asyncio.run(_test())
