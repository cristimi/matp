-- Migration 051: geometric_range template — allow moderate-fit patterns.
-- Companion to the code change adding a 'moderate' fit_quality tier
-- (trendline R² 0.50–0.70) in app/data/geometry.py and letting it through the
-- pre-LLM skip gate (app/graph/gating.py). Rationale: over 2 days of live
-- running, 22/24 ETH cycles never reached the LLM because a *strong* fit
-- (both R² ≥ 0.70) is rare; moderate structures are tradeable with stricter
-- touch counts and a lower confidence cap.
--
-- Surgical replace() on the live prompt so the rest of the template (which has
-- accumulated wave-4 edits since 036) is untouched.

BEGIN;

UPDATE public.ai_prompt_templates
SET system_prompt = replace(
    system_prompt,
    'Only place a new resting order for patterns with fit_quality = "strong". A "weak" fit indicates low trendline R² — the structure is unreliable; output hold (existing resting orders may still be managed per Phase 4/5 below).',
    'Only place a new resting order for patterns with fit_quality = "strong" or "moderate". A "moderate" fit (trendline R² 0.50–0.70) is a lower-conviction structure: require at least 3 touches on EACH boundary before placing on it, and apply the moderate confidence cap below. A "weak" fit indicates low trendline R² — the structure is unreliable; output hold (existing resting orders may still be managed per Phase 4/5 below).'
)
WHERE id = 'geometric_range';

UPDATE public.ai_prompt_templates
SET system_prompt = replace(
    system_prompt,
    '- fit_quality = "strong", exactly 2 touches on either side: cap confidence at 0.75.',
    E'- fit_quality = "strong", exactly 2 touches on either side: cap confidence at 0.75.\n- fit_quality = "moderate": only tradeable with upper_touches ≥ 3 AND lower_touches ≥ 3; cap confidence at 0.75, and only exceed 0.72 when the boundary being worked has HVN/value-area confluence.'
)
WHERE id = 'geometric_range';

COMMIT;

-- Self-verification
DO $$
DECLARE
    prompt text;
BEGIN
    SELECT system_prompt FROM public.ai_prompt_templates
     WHERE id = 'geometric_range' INTO prompt;

    IF prompt IS NULL THEN
        RAISE EXCEPTION 'Migration 051 FAILED: geometric_range template not found';
    END IF;

    IF position('fit_quality = "strong" or "moderate"' IN prompt) = 0 THEN
        RAISE EXCEPTION 'Migration 051 FAILED: Phase 1 moderate-fit sentence not applied';
    END IF;

    IF position('- fit_quality = "moderate": only tradeable with upper_touches' IN prompt) = 0 THEN
        RAISE EXCEPTION 'Migration 051 FAILED: moderate confidence-cap line not applied';
    END IF;

    IF position('Only place a new resting order for patterns with fit_quality = "strong". A "weak"' IN prompt) > 0 THEN
        RAISE EXCEPTION 'Migration 051 FAILED: old strong-only Phase 1 sentence still present';
    END IF;

    RAISE NOTICE 'Migration 051 verified OK: geometric_range template now trades moderate fits (3+ touches, confidence cap 0.75)';
END $$;
