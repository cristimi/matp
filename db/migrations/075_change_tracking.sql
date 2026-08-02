-- Migration 075: remember what changed, when, and what it was before.
--
-- ── THE GAP ──────────────────────────────────────────────────────────────────
-- Every settings table in this system stores only the CURRENT value plus an
-- updated_at clock. Change a strategy's confidence threshold, its LLM model, its
-- prompt template or the prompt text itself and the previous value is gone. The
-- one audit table that exists (ai_risk_config_audit) covers a single field
-- (max_concurrent_trades) and has zero rows in it.
--
-- That makes the obvious question unanswerable: "this strategy got worse in the
-- last week — what did I change?"
--
-- ── WHY TRIGGERS, NOT API-LEVEL DIFFS ────────────────────────────────────────
-- Config here is edited from at least three places: the dashboard API, hand-run
-- psql, and migrations (every prompt template edit so far arrived as a migration).
-- Diffing inside the API routes would only catch the first. A row-level trigger
-- catches all of them, including edits made years from now by code that does not
-- exist yet. The cost is that the DB does not know WHO made the change; callers
-- that care can set `matp.actor` on their transaction and the trigger will record
-- it, otherwise the row says 'system'.
--
-- ── WHAT IS DELIBERATELY NOT LOGGED ──────────────────────────────────────────
-- strategies carries runtime counters next to its settings (pnl_today, pnl_total,
-- last_signal_at, capital_allocation, allocation_peak). Those move on every fill;
-- logging them would bury the settings changes under thousands of rows. They are
-- ignored. webhook_secret is logged as a rotation event with both values masked —
-- the log must never become a place secrets leak from.
--
-- ── PROMPT TEXT ──────────────────────────────────────────────────────────────
-- ai_prompt_templates gets a version counter and a companion table holding a full
-- snapshot of every version of the text. The change log then only has to point at
-- version numbers ("v3 -> v4") instead of duplicating thousands of characters.
-- Each AI run records the version it actually ran on, so a signal from last week
-- can be read against the words that produced it rather than today's words.

BEGIN;

-- ── 1. the change log ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.strategy_change_log (
    id          bigserial PRIMARY KEY,
    changed_at  timestamptz  NOT NULL DEFAULT now(),
    -- strategy | ai_config | risk_config | prompt_template
    entity      varchar(30)  NOT NULL,
    -- NULL for prompt_template rows: a template is shared by every strategy using it
    strategy_id varchar(100),
    template_id varchar(50),
    action      varchar(10)  NOT NULL DEFAULT 'update',  -- create | update | delete
    field_name  varchar(100) NOT NULL,
    old_value   text,
    new_value   text,
    changed_by  varchar(100) NOT NULL DEFAULT 'system'
);

-- No foreign key on strategy_id on purpose: the history of a strategy has to
-- outlive the strategy row, and a hard delete must never take the log with it.
CREATE INDEX IF NOT EXISTS scl_strategy_idx ON public.strategy_change_log (strategy_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS scl_template_idx ON public.strategy_change_log (template_id, changed_at DESC);
CREATE INDEX IF NOT EXISTS scl_changed_at_idx ON public.strategy_change_log (changed_at DESC);

-- ── 2. prompt text versions ──────────────────────────────────────────────────

ALTER TABLE public.ai_prompt_templates
    ADD COLUMN IF NOT EXISTS version    integer     NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

CREATE TABLE IF NOT EXISTS public.ai_prompt_template_versions (
    id            bigserial PRIMARY KEY,
    template_id   varchar(50)  NOT NULL,
    version       integer      NOT NULL,
    name          varchar(100) NOT NULL,
    system_prompt text         NOT NULL,
    captured_at   timestamptz  NOT NULL DEFAULT now(),
    note          text,
    UNIQUE (template_id, version)
);

CREATE INDEX IF NOT EXISTS aptv_template_idx
    ON public.ai_prompt_template_versions (template_id, version DESC);

-- Which version each AI cycle actually ran on.
ALTER TABLE public.ai_signal_log
    ADD COLUMN IF NOT EXISTS prompt_version integer;

-- ── 3. the generic settings-diff trigger ─────────────────────────────────────

CREATE OR REPLACE FUNCTION public.log_config_change() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_entity   text;
    v_ignore   text[];
    v_strategy text;
    v_actor    text := COALESCE(NULLIF(current_setting('matp.actor', true), ''), 'system');
    old_j      jsonb;
    new_j      jsonb := to_jsonb(NEW);
    k          text;
    ov         text;
    nv         text;
BEGIN
    IF TG_TABLE_NAME = 'strategies' THEN
        v_entity   := 'strategy';
        v_strategy := NEW.id;
        -- runtime counters, not settings — see header
        v_ignore   := ARRAY['updated_at', 'created_at', 'pnl_today', 'pnl_total',
                            'last_signal_at', 'capital_allocation', 'allocation_peak',
                            'webhook_secret'];
    ELSIF TG_TABLE_NAME = 'ai_strategy_config' THEN
        v_entity   := 'ai_config';
        v_strategy := NEW.strategy_id;
        v_ignore   := ARRAY['updated_at', 'updated_by'];
    ELSE
        v_entity   := 'risk_config';
        v_strategy := NEW.strategy_id;
        v_ignore   := ARRAY['updated_at', 'updated_by'];
    END IF;

    IF TG_OP = 'INSERT' THEN
        -- read through the jsonb rather than NEW.<col>: only one of these tables
        -- has a name column, and a direct field reference would fail on the others
        INSERT INTO public.strategy_change_log
            (entity, strategy_id, action, field_name, old_value, new_value, changed_by)
        VALUES (v_entity, v_strategy, 'create', '(created)', NULL,
                COALESCE(new_j ->> 'name', v_entity), v_actor);
        RETURN NULL;
    END IF;

    old_j := to_jsonb(OLD);

    FOR k IN SELECT jsonb_object_keys(new_j) LOOP
        CONTINUE WHEN k = ANY (v_ignore);
        ov := old_j ->> k;
        nv := new_j ->> k;
        CONTINUE WHEN ov IS NOT DISTINCT FROM nv;
        INSERT INTO public.strategy_change_log
            (entity, strategy_id, action, field_name, old_value, new_value, changed_by)
        VALUES (v_entity, v_strategy, 'update', k, ov, nv, v_actor);
    END LOOP;

    -- A rotated webhook secret is worth knowing about; its value never is.
    IF TG_TABLE_NAME = 'strategies'
       AND (old_j ->> 'webhook_secret') IS DISTINCT FROM (new_j ->> 'webhook_secret') THEN
        INSERT INTO public.strategy_change_log
            (entity, strategy_id, action, field_name, old_value, new_value, changed_by)
        VALUES (v_entity, v_strategy, 'update', 'webhook_secret', '(hidden)', '(rotated)', v_actor);
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS log_strategy_changes ON public.strategies;
CREATE TRIGGER log_strategy_changes
    AFTER INSERT OR UPDATE ON public.strategies
    FOR EACH ROW EXECUTE FUNCTION public.log_config_change();

DROP TRIGGER IF EXISTS log_ai_config_changes ON public.ai_strategy_config;
CREATE TRIGGER log_ai_config_changes
    AFTER INSERT OR UPDATE ON public.ai_strategy_config
    FOR EACH ROW EXECUTE FUNCTION public.log_config_change();

DROP TRIGGER IF EXISTS log_risk_config_changes ON public.ai_risk_config;
CREATE TRIGGER log_risk_config_changes
    AFTER INSERT OR UPDATE ON public.ai_risk_config
    FOR EACH ROW EXECUTE FUNCTION public.log_config_change();

-- ── 4. prompt template versioning ────────────────────────────────────────────

-- BEFORE: decide the version number and stamp updated_at, so the row itself
-- always says which version it currently is.
CREATE OR REPLACE FUNCTION public.bump_prompt_template_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.version := COALESCE(NEW.version, 1);
        NEW.updated_at := now();
    ELSIF NEW.system_prompt IS DISTINCT FROM OLD.system_prompt
       OR NEW.name IS DISTINCT FROM OLD.name THEN
        NEW.version := OLD.version + 1;
        NEW.updated_at := now();
    END IF;
    RETURN NEW;
END;
$$;

-- AFTER: snapshot the text and record the change.
CREATE OR REPLACE FUNCTION public.snapshot_prompt_template() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_actor text := COALESCE(NULLIF(current_setting('matp.actor', true), ''), 'system');
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.version = OLD.version THEN
        RETURN NULL;  -- nothing about the wording changed
    END IF;

    INSERT INTO public.ai_prompt_template_versions
        (template_id, version, name, system_prompt, note)
    VALUES (NEW.id, NEW.version, NEW.name, NEW.system_prompt,
            CASE WHEN TG_OP = 'INSERT' THEN 'template created' ELSE NULL END)
    ON CONFLICT (template_id, version) DO NOTHING;

    IF TG_OP = 'INSERT' THEN
        INSERT INTO public.strategy_change_log
            (entity, template_id, action, field_name, old_value, new_value, changed_by)
        VALUES ('prompt_template', NEW.id, 'create', '(created)', NULL,
                format('v%s · %s chars', NEW.version, length(NEW.system_prompt)), v_actor);
    ELSE
        INSERT INTO public.strategy_change_log
            (entity, template_id, action, field_name, old_value, new_value, changed_by)
        VALUES ('prompt_template', NEW.id, 'update', 'system_prompt',
                format('v%s · %s chars', OLD.version, length(OLD.system_prompt)),
                format('v%s · %s chars', NEW.version, length(NEW.system_prompt)), v_actor);
    END IF;

    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS bump_prompt_version ON public.ai_prompt_templates;
CREATE TRIGGER bump_prompt_version
    BEFORE INSERT OR UPDATE ON public.ai_prompt_templates
    FOR EACH ROW EXECUTE FUNCTION public.bump_prompt_template_version();

DROP TRIGGER IF EXISTS snapshot_prompt_version ON public.ai_prompt_templates;
CREATE TRIGGER snapshot_prompt_version
    AFTER INSERT OR UPDATE ON public.ai_prompt_templates
    FOR EACH ROW EXECUTE FUNCTION public.snapshot_prompt_template();

-- ── 5. baseline ──────────────────────────────────────────────────────────────
-- Today's text becomes v1 for every existing template. This is the earliest
-- honest starting point: what the text was before today was never recorded, so
-- the snapshot is dated from the template's creation and says so.

INSERT INTO public.ai_prompt_template_versions
    (template_id, version, name, system_prompt, captured_at, note)
SELECT id, 1, name, system_prompt, created_at,
       'baseline captured by migration 075 — text before this point was never recorded'
FROM public.ai_prompt_templates
ON CONFLICT (template_id, version) DO NOTHING;

COMMIT;
