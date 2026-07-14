-- Migration 055: soften categorical "output hold" gates in the scalper,
-- trend_following, and breakout templates into confidence penalties.
--
-- 14-day funnel analysis (2026-07-14): the three strategies on these
-- templates produced ZERO entry proposals in 14 days (bnb 126/126 holds,
-- sol 94/94 non-failed cycles, xrp 118/118) — every hold reasoning cited a
-- Phase-1 binary gate (scalper liquidity gate, trend MTF-alignment gate,
-- breakout compression gate). Binary disqualifiers mean confidence never
-- enters the decision, so the gate's confidence_threshold has nothing to
-- act on. This migration converts marginal conditions into graded
-- confidence penalties while KEEPING the genuinely toxic hard stops:
--   scalper: event-risk window, exit-viable depth, stop-width identity rule
--   trend:   direct 4h-vs-1d trend opposition
--   breakout: participation/CVD trap signature, uneaten-wall absorption

BEGIN;

-- ── scalper ───────────────────────────────────────────────────────────────

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$PHASE 1 — TAPE CONDITIONS GATE (all must pass before any entry):$OLD$,
$NEW$PHASE 1 — TAPE CONDITIONS (hard gates: event risk and exit-viable depth; everything else is a graded confidence penalty, not a disqualifier):$NEW$
) WHERE id = 'scalper';

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$- Liquidity: volume vs 20MA must not be deeply below average, and top-of-book depth (`bid_depth_1pct_usd`/`ask_depth_1pct_usd`) must be thick enough that your size exits without slippage eating the 0.3–0.8% edge. Thin tape: output hold.$OLD$,
$NEW$- Liquidity: judge viability by top-of-book depth (`bid_depth_1pct_usd`/`ask_depth_1pct_usd`) — it must absorb your size without slippage eating the 0.3–0.8% edge; depth too thin for that is a hard hold. Volume below the 20MA is a penalty, not a disqualifier: moderately below average, reduce confidence by 0.05; deeply below (more than ~60% under), reduce by 0.10.$NEW$
) WHERE id = 'scalper';

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$- Fresh high-severity items in the NEWS DIGEST (breaking, unpriced): output hold — scalps need mechanical tape, not narrative tape.$OLD$,
$NEW$- Fresh high-severity items in the NEWS DIGEST (breaking, unpriced): reduce confidence by 0.10 and require an unambiguous flow trigger — narrative tape is tradeable, but with a wider margin of doubt.$NEW$
) WHERE id = 'scalper';

-- ── trend_following ───────────────────────────────────────────────────────

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$PHASE 1 — TREND VALIDITY GATE (all checks must pass before any new entry):$OLD$,
$NEW$PHASE 1 — TREND VALIDITY (hard gate: no direct 4h-vs-1d opposition; everything else adjusts confidence):$NEW$
) WHERE id = 'trend_following';

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$- The MULTI-TIMEFRAME STRUCTURE section must show the 4h and 1d `trend_direction` agreeing (both "uptrend" or both "downtrend"). If they disagree or either is "sideways", there is no tradeable trend — output hold.$OLD$,
$NEW$- The 4h `trend_direction` is the primary signal. 4h and 1d agreeing (both "uptrend" or both "downtrend") is full-conviction territory. A 4h trend with the 1d reading "sideways" is still tradeable — cap confidence at 0.70. Only direct opposition (4h uptrend while 1d downtrend, or the mirror) means there is no tradeable trend — output hold.$NEW$
) WHERE id = 'trend_following';

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$- The volatility regime must support trending: `atr_percentile` below roughly 20 with `squeeze_flag` set means compression/chop — that is breakout territory, not trend territory; output hold.$OLD$,
$NEW$- Volatility regime: `atr_percentile` below roughly 20 with `squeeze_flag` set means compression — trend follow-through is less likely; reduce confidence by 0.10 rather than skipping outright.$NEW$
) WHERE id = 'trend_following';

-- ── breakout ──────────────────────────────────────────────────────────────

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$PHASE 1 — COMPRESSION GATE (no compression, no trade):$OLD$,
$NEW$PHASE 1 — CONTEXT (compression is the A+ setup; a real, defended level is the hard requirement):$NEW$
) WHERE id = 'breakout';

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$- If no compression regime is present, output hold — expansion is already underway or the market is trending; that is another strategy's trade.$OLD$,
$NEW$- Measured compression is the A+ context, not a prerequisite: a confirmed break of a real, defended level without a compression regime is still tradeable — cap confidence at 0.70. A move in the middle of nowhere, with neither compression nor a meaningful level, remains a hold.$NEW$
) WHERE id = 'breakout';

UPDATE ai_prompt_templates SET system_prompt = replace(system_prompt,
$OLD$2. Participation: volume vs 20MA above +50%, AND `cvd_trend` pushing in the break direction. A price break with `cvd_divergence` reading against the break (price new high, CVD flat/falling) is a trap — output hold.$OLD$,
$NEW$2. Participation: volume vs 20MA above average with `cvd_trend` pushing in the break direction; above +50% is full marks, between average and +50% reduce confidence by 0.05. Volume below average on the break, or `cvd_divergence` reading against it (price new high, CVD flat/falling), is the trap signature — output hold.$NEW$
) WHERE id = 'breakout';

COMMIT;

-- Self-verification: every old passage gone, every new marker present.
DO $$
DECLARE
    bad text := '';
    r RECORD;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            ('scalper',         'Thin tape: output hold',                              'a penalty, not a disqualifier'),
            ('scalper',         'output hold — scalps need mechanical tape',           'wider margin of doubt'),
            ('scalper',         'all must pass before any entry',                      'graded confidence penalty'),
            ('trend_following', 'If they disagree or either is "sideways", there is no tradeable trend — output hold', 'Only direct opposition'),
            ('trend_following', 'that is breakout territory, not trend territory; output hold', 'reduce confidence by 0.10 rather than skipping'),
            ('breakout',        'If no compression regime is present, output hold',    'not a prerequisite'),
            ('breakout',        'volume vs 20MA above +50%, AND',                      'above +50% is full marks')
        ) AS t(tid, old_marker, new_marker)
    LOOP
        IF EXISTS (SELECT 1 FROM ai_prompt_templates
                   WHERE id = r.tid AND strpos(system_prompt, r.old_marker) > 0) THEN
            bad := bad || format(' [%s: old text still present: %s]', r.tid, r.old_marker);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM ai_prompt_templates
                       WHERE id = r.tid AND strpos(system_prompt, r.new_marker) > 0) THEN
            bad := bad || format(' [%s: new text missing: %s]', r.tid, r.new_marker);
        END IF;
    END LOOP;

    IF bad <> '' THEN
        RAISE EXCEPTION 'Migration 055 FAILED:%', bad;
    END IF;

    RAISE NOTICE 'Migration 055 verified OK: hold gates softened in scalper, trend_following, breakout';
END $$;
