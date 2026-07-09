--
-- PostgreSQL database dump
--

\restrict B2fToI9eynrCCMxaq1px5Re3Au2F1E8uYVfv4PoCjYuu1Me7BRSj3VGd3L0JXPs

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: tester; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA tester;


--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: update_updated_at_column(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ai_prompt_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_prompt_templates (
    id character varying(50) NOT NULL,
    name character varying(100) NOT NULL,
    description text,
    system_prompt text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_risk_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_risk_config (
    strategy_id character varying(100) NOT NULL,
    max_concurrent_trades integer DEFAULT 1 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by character varying(100)
);


--
-- Name: ai_risk_config_audit; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_risk_config_audit (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    changed_at timestamp with time zone DEFAULT now() NOT NULL,
    changed_by character varying(100),
    field_name character varying(100) NOT NULL,
    old_value text,
    new_value text
);


--
-- Name: ai_risk_config_audit_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ai_risk_config_audit_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_risk_config_audit_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ai_risk_config_audit_id_seq OWNED BY public.ai_risk_config_audit.id;


--
-- Name: ai_signal_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_signal_log (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    triggered_at timestamp with time zone DEFAULT now() NOT NULL,
    trigger_reason character varying(50) NOT NULL,
    cycle_interval character varying(10),
    prompt_template character varying(50),
    data_sources_used text[],
    context_tokens integer,
    proposed_action character varying(20),
    confidence numeric(4,3),
    reasoning text,
    gate_passed boolean DEFAULT false NOT NULL,
    gate_rejection_reason text,
    webhook_fired boolean DEFAULT false NOT NULL,
    webhook_status integer,
    order_id uuid,
    dry_run boolean DEFAULT true NOT NULL,
    outcome_pnl numeric,
    outcome_pct numeric,
    outcome_filled_at timestamp with time zone,
    llm_provider character varying(20),
    llm_model character varying(50)
);


--
-- Name: ai_signal_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ai_signal_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_signal_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ai_signal_log_id_seq OWNED BY public.ai_signal_log.id;


--
-- Name: ai_strategy_config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ai_strategy_config (
    strategy_id character varying(100) NOT NULL,
    interval_no_position character varying(10) DEFAULT '4h'::character varying NOT NULL,
    interval_position_open character varying(10) DEFAULT '15m'::character varying NOT NULL,
    interval_at_risk character varying(10) DEFAULT '5m'::character varying NOT NULL,
    at_risk_threshold_pct numeric(5,2) DEFAULT 1.50 NOT NULL,
    use_technical boolean DEFAULT true NOT NULL,
    use_fear_greed boolean DEFAULT true NOT NULL,
    use_funding_rate boolean DEFAULT true NOT NULL,
    use_open_interest boolean DEFAULT true NOT NULL,
    use_news boolean DEFAULT true NOT NULL,
    use_economic_calendar boolean DEFAULT false NOT NULL,
    use_btc_dominance boolean DEFAULT false NOT NULL,
    use_macro boolean DEFAULT false NOT NULL,
    indicators text[] DEFAULT ARRAY['RSI'::text, 'MACD'::text, 'EMA50'::text, 'EMA200'::text, 'BB'::text, 'VWAP'::text] NOT NULL,
    lookback_days integer DEFAULT 90 NOT NULL,
    confidence_threshold numeric(4,3) DEFAULT 0.720 NOT NULL,
    cooldown_entry_minutes integer DEFAULT 240 NOT NULL,
    cooldown_increase_minutes integer DEFAULT 60 NOT NULL,
    cooldown_stop_adj_minutes integer DEFAULT 30 NOT NULL,
    template_id character varying(50) DEFAULT 'trend_following'::character varying NOT NULL,
    custom_instructions text,
    trigger_news_high boolean DEFAULT true NOT NULL,
    trigger_volume_spike boolean DEFAULT true NOT NULL,
    trigger_funding_spike boolean DEFAULT true NOT NULL,
    trigger_key_level boolean DEFAULT true NOT NULL,
    trigger_liquidation boolean DEFAULT false NOT NULL,
    volume_spike_threshold numeric(6,1) DEFAULT 300.0 NOT NULL,
    funding_spike_threshold numeric(6,4) DEFAULT 0.0500 NOT NULL,
    dry_run boolean DEFAULT true NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_by character varying(100),
    llm_provider character varying(20) DEFAULT 'google'::character varying NOT NULL,
    llm_model character varying(50) DEFAULT 'gemini-2.0-flash'::character varying NOT NULL,
    use_geometry boolean DEFAULT false NOT NULL,
    candle_close_buffer_seconds integer DEFAULT 10 NOT NULL,
    CONSTRAINT ai_strategy_config_candle_close_buffer_chk CHECK (((candle_close_buffer_seconds >= 0) AND (candle_close_buffer_seconds <= 600)))
);


--
-- Name: COLUMN ai_strategy_config.llm_provider; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_strategy_config.llm_provider IS 'LLM provider: google | openai | anthropic';


--
-- Name: COLUMN ai_strategy_config.llm_model; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.ai_strategy_config.llm_model IS 'Model name as accepted by the provider SDK';


--
-- Name: assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assets (
    id integer NOT NULL,
    symbol character varying(20) NOT NULL,
    name character varying(100)
);


--
-- Name: assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.assets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.assets_id_seq OWNED BY public.assets.id;


--
-- Name: config; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.config (
    key character varying(100) NOT NULL,
    value text NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: dead_letter_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dead_letter_orders (
    id bigint NOT NULL,
    order_id uuid NOT NULL,
    failed_at timestamp with time zone DEFAULT now() NOT NULL,
    reason text,
    retry_count integer DEFAULT 0 NOT NULL,
    last_retry timestamp with time zone
);


--
-- Name: dead_letter_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dead_letter_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dead_letter_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dead_letter_orders_id_seq OWNED BY public.dead_letter_orders.id;


--
-- Name: exchange_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.exchange_accounts (
    id character varying(100) NOT NULL,
    exchange character varying(30) NOT NULL,
    mode character varying(10) NOT NULL,
    label character varying(100) NOT NULL,
    credentials bytea NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT exchange_accounts_mode_check CHECK (((mode)::text = ANY (ARRAY[('live'::character varying)::text, ('demo'::character varying)::text])))
);


--
-- Name: order_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_events (
    id bigint NOT NULL,
    order_id uuid NOT NULL,
    event_time timestamp with time zone DEFAULT now() NOT NULL,
    from_status character varying(20),
    to_status character varying(20) NOT NULL,
    message text
);


--
-- Name: order_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.order_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: order_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.order_events_id_seq OWNED BY public.order_events.id;


--
-- Name: order_execution_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.order_execution_log (
    id bigint NOT NULL,
    signal_log_id bigint,
    account_id character varying(100),
    exchange character varying(30) NOT NULL,
    exchange_order_id character varying(100),
    client_order_id character varying(100) NOT NULL,
    symbol character varying(20) NOT NULL,
    side character varying(10) NOT NULL,
    order_type character varying(20) NOT NULL,
    requested_size numeric NOT NULL,
    requested_price numeric,
    status character varying(20) NOT NULL,
    cumulative_filled numeric DEFAULT 0,
    exchange_fee numeric DEFAULT 0,
    error_message text,
    placed_at timestamp with time zone,
    filled_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: order_execution_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.order_execution_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: order_execution_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.order_execution_log_id_seq OWNED BY public.order_execution_log.id;


--
-- Name: orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.orders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    symbol character varying(50) NOT NULL,
    side character varying(10) NOT NULL,
    signal character varying(20) NOT NULL,
    order_type character varying(20) NOT NULL,
    size numeric NOT NULL,
    price numeric,
    leverage integer,
    margin_mode character varying(10),
    tp_price numeric,
    sl_price numeric,
    platform character varying(20) NOT NULL,
    strategy_id character varying(100) NOT NULL,
    status character varying(20) DEFAULT 'received'::character varying NOT NULL,
    exchange_order_id character varying(100),
    pnl numeric,
    raw_webhook jsonb NOT NULL,
    raw_response jsonb,
    error_msg text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    signal_source character varying(100) DEFAULT 'unknown'::character varying NOT NULL,
    signal_metadata jsonb DEFAULT '{}'::jsonb,
    indicator_price numeric(18,8),
    actual_fill_price numeric,
    pair_id integer,
    account_id character varying(100),
    closes_position_id uuid
);


--
-- Name: shadow_signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.shadow_signals (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    signal_source character varying(100) NOT NULL,
    symbol character varying(50) NOT NULL,
    side character varying(10) NOT NULL,
    signal character varying(20) NOT NULL,
    signal_bar_time timestamp with time zone NOT NULL,
    bar_close_price numeric,
    bracket_spec jsonb DEFAULT '{}'::jsonb NOT NULL,
    generated_at timestamp with time zone DEFAULT now() NOT NULL,
    mode character varying(10) DEFAULT 'shadow'::character varying NOT NULL,
    matched_order_id uuid,
    match_status character varying(20),
    diff_notes text,
    exit_reason character varying(20),
    size_pct numeric
);


--
-- Name: shadow_signals_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.shadow_signals_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: shadow_signals_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.shadow_signals_id_seq OWNED BY public.shadow_signals.id;


--
-- Name: signal_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signal_log (
    id bigint NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    source_ip inet,
    strategy_id character varying(100),
    http_status integer,
    outcome character varying(30),
    error_detail text,
    raw_body jsonb,
    duration_ms integer,
    ai_reasoning text,
    ai_confidence numeric(4,3)
);


--
-- Name: COLUMN signal_log.ai_reasoning; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.signal_log.ai_reasoning IS 'LLM reasoning text from AI signal generator. NULL for non-AI signals.';


--
-- Name: COLUMN signal_log.ai_confidence; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.signal_log.ai_confidence IS 'LLM confidence score (0.0-0.95) from AI signal generator. NULL for non-AI signals.';


--
-- Name: signal_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.signal_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: signal_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.signal_log_id_seq OWNED BY public.signal_log.id;


--
-- Name: social_position_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_position_state (
    source text NOT NULL,
    asset text NOT NULL,
    state text DEFAULT 'FLAT'::text NOT NULL,
    last_msg_id bigint,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT social_state_chk CHECK ((state = ANY (ARRAY['LONG'::text, 'SHORT'::text, 'FLAT'::text])))
);


--
-- Name: social_shadow_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_shadow_orders (
    id bigint NOT NULL,
    source text NOT NULL,
    channel_msg_id bigint NOT NULL,
    posted_at timestamp with time zone,
    evaluated_at timestamp with time zone DEFAULT now() NOT NULL,
    phase text NOT NULL,
    asset text,
    action_type text,
    from_state text,
    to_state text,
    intended_signal text,
    reference_price numeric,
    mark_price numeric,
    confidence numeric,
    decision text NOT NULL,
    reason text NOT NULL,
    mode text DEFAULT 'shadow'::text NOT NULL,
    CONSTRAINT social_shadow_decision_chk CHECK ((decision = ANY (ARRAY['acted'::text, 'skipped'::text])))
);


--
-- Name: social_shadow_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.social_shadow_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: social_shadow_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.social_shadow_orders_id_seq OWNED BY public.social_shadow_orders.id;


--
-- Name: social_signal_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.social_signal_log (
    id bigint NOT NULL,
    source text NOT NULL,
    channel_msg_id bigint NOT NULL,
    posted_at timestamp with time zone NOT NULL,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_text text,
    preview_text text,
    x_url text,
    is_actionable boolean NOT NULL,
    action_type text NOT NULL,
    asset text,
    direction text,
    reference_price numeric,
    confidence numeric,
    in_whitelist boolean DEFAULT false NOT NULL,
    model text,
    extractor_version text,
    raw_llm_json jsonb,
    CONSTRAINT social_signal_action_type_chk CHECK ((action_type = ANY (ARRAY['OPEN'::text, 'FLIP'::text, 'CLOSE'::text, 'ADD'::text, 'TRIM'::text, 'NONE'::text]))),
    CONSTRAINT social_signal_direction_chk CHECK (((direction IS NULL) OR (direction = ANY (ARRAY['LONG'::text, 'SHORT'::text]))))
);


--
-- Name: social_signal_log_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.social_signal_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: social_signal_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.social_signal_log_id_seq OWNED BY public.social_signal_log.id;


--
-- Name: strategies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategies (
    id character varying(100) NOT NULL,
    name character varying(100) NOT NULL,
    class character varying(100) NOT NULL,
    symbol character varying(50) NOT NULL,
    "interval" character varying(10) NOT NULL,
    platform character varying(20) DEFAULT 'auto'::character varying NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    config_yaml text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    webhook_secret character varying(255) NOT NULL,
    description text,
    max_leverage integer DEFAULT 10,
    pnl_today numeric DEFAULT 0,
    pnl_total numeric DEFAULT 0,
    last_signal_at timestamp with time zone,
    tags text[] DEFAULT '{}'::text[],
    type character varying(20) DEFAULT 'internal'::character varying NOT NULL,
    pair_id integer,
    account_id character varying(100),
    allow_quote_variants boolean DEFAULT false NOT NULL,
    allow_cross_charting boolean DEFAULT false NOT NULL,
    default_leverage integer DEFAULT 1 NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    margin_mode character varying(10) DEFAULT 'isolated'::character varying NOT NULL,
    strategy_source character varying(20) DEFAULT 'tradingview'::character varying NOT NULL,
    capital_allocation numeric DEFAULT 100 NOT NULL,
    margin_per_trade numeric DEFAULT 5 NOT NULL,
    max_drawdown_pct numeric DEFAULT 50 NOT NULL,
    initial_allocation numeric,
    allocation_peak numeric,
    local_signal_mode character varying(10) DEFAULT 'off'::character varying NOT NULL,
    stop_reason character varying,
    entry_trigger character varying(16) DEFAULT 'bar_close'::character varying NOT NULL,
    CONSTRAINT strategies_type_check CHECK (((type)::text = ANY (ARRAY[('internal'::character varying)::text, ('tradingview'::character varying)::text]))),
    CONSTRAINT strategies_entry_trigger_chk CHECK (((entry_trigger)::text = ANY (ARRAY[('bar_close'::character varying)::text, ('intrabar'::character varying)::text])))
);


--
-- Name: COLUMN strategies.strategy_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.strategies.strategy_source IS 'Signal source: tradingview | ai_engine | social | internal | manual';


--
-- Name: strategy_performance; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_performance (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    period_type character varying(20) NOT NULL,
    period_date date,
    total_signals integer DEFAULT 0,
    filled_orders integer DEFAULT 0,
    failed_orders integer DEFAULT 0,
    rejected_orders integer DEFAULT 0,
    winning_trades integer DEFAULT 0,
    losing_trades integer DEFAULT 0,
    neutral_trades integer DEFAULT 0,
    win_rate numeric(5,2),
    total_pnl numeric(18,8),
    avg_pnl numeric(18,8),
    median_pnl numeric(18,8),
    max_win numeric(18,8),
    max_loss numeric(18,8),
    consecutive_wins integer DEFAULT 0,
    consecutive_losses integer DEFAULT 0,
    profit_factor numeric(10,4),
    largest_drawdown numeric(5,2),
    calculated_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: strategy_performance_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.strategy_performance_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: strategy_performance_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.strategy_performance_id_seq OWNED BY public.strategy_performance.id;


--
-- Name: strategy_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_positions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    strategy_id character varying(100) NOT NULL,
    exchange character varying(20) NOT NULL,
    symbol character varying(50) NOT NULL,
    side character varying(10) NOT NULL,
    entry_price numeric NOT NULL,
    size numeric NOT NULL,
    leverage integer,
    margin_mode character varying(20),
    pnl_unrealized numeric,
    pnl_realized numeric,
    status character varying(20) DEFAULT 'open'::character varying,
    opening_order_id uuid,
    closing_order_id uuid,
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    closing_price numeric,
    liquidation_price numeric,
    pair_id integer,
    close_reason character varying(30),
    reconcile_miss_count integer DEFAULT 0 NOT NULL,
    reconcile_divergent boolean DEFAULT false NOT NULL,
    reconcile_exchange_size numeric,
    reconcile_divergence_at timestamp with time zone
);


--
-- Name: strategy_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_stats (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    period_date date NOT NULL,
    trades_count integer DEFAULT 0,
    trades_won integer DEFAULT 0,
    trades_lost integer DEFAULT 0,
    win_rate numeric,
    pnl_total numeric DEFAULT 0,
    pnl_avg numeric,
    max_drawdown numeric DEFAULT 0,
    capital_deployed numeric DEFAULT 0,
    leverage_avg numeric,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: strategy_stats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.strategy_stats_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: strategy_stats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.strategy_stats_id_seq OWNED BY public.strategy_stats.id;


--
-- Name: strategy_webhook_calls; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategy_webhook_calls (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    http_status integer,
    error_message text,
    source_ip inet
);


--
-- Name: strategy_webhook_calls_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.strategy_webhook_calls_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: strategy_webhook_calls_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.strategy_webhook_calls_id_seq OWNED BY public.strategy_webhook_calls.id;


--
-- Name: trading_pairs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trading_pairs (
    id integer NOT NULL,
    base_asset_id integer NOT NULL,
    quote_asset_id integer NOT NULL,
    exchange_meta jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: trading_pairs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.trading_pairs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: trading_pairs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.trading_pairs_id_seq OWNED BY public.trading_pairs.id;


--
-- Name: ai_risk_config; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.ai_risk_config (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    max_position_size_pct numeric DEFAULT 5.0 NOT NULL,
    max_concurrent_trades integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_risk_config_id_seq; Type: SEQUENCE; Schema: tester; Owner: -
--

CREATE SEQUENCE tester.ai_risk_config_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_risk_config_id_seq; Type: SEQUENCE OWNED BY; Schema: tester; Owner: -
--

ALTER SEQUENCE tester.ai_risk_config_id_seq OWNED BY tester.ai_risk_config.id;


--
-- Name: ai_signal_log; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.ai_signal_log (
    id bigint NOT NULL,
    backtest_run_id uuid,
    strategy_id character varying(100) NOT NULL,
    triggered_at timestamp with time zone NOT NULL,
    trigger_reason character varying(50),
    cycle_interval character varying(10),
    prompt_template character varying(100),
    data_sources_used text[] DEFAULT '{}'::text[],
    context_tokens integer,
    proposed_action character varying(30),
    confidence numeric,
    reasoning text,
    gate_passed boolean DEFAULT false NOT NULL,
    gate_rejection_reason character varying(50),
    dry_run boolean DEFAULT true NOT NULL,
    llm_provider character varying(50),
    llm_model character varying(100),
    webhook_fired boolean DEFAULT false,
    webhook_status integer,
    order_id uuid
);


--
-- Name: ai_signal_log_id_seq; Type: SEQUENCE; Schema: tester; Owner: -
--

CREATE SEQUENCE tester.ai_signal_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_signal_log_id_seq; Type: SEQUENCE OWNED BY; Schema: tester; Owner: -
--

ALTER SEQUENCE tester.ai_signal_log_id_seq OWNED BY tester.ai_signal_log.id;


--
-- Name: ai_strategy_config; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.ai_strategy_config (
    id bigint NOT NULL,
    strategy_id character varying(100) NOT NULL,
    template_id character varying(100) DEFAULT 'trend_following'::character varying NOT NULL,
    llm_provider character varying(50) DEFAULT 'google'::character varying NOT NULL,
    llm_model character varying(100) DEFAULT 'gemini-2.0-flash'::character varying NOT NULL,
    use_technical boolean DEFAULT true NOT NULL,
    use_fear_greed boolean DEFAULT false NOT NULL,
    use_funding_rate boolean DEFAULT false NOT NULL,
    use_open_interest boolean DEFAULT false NOT NULL,
    use_news boolean DEFAULT false NOT NULL,
    use_btc_dominance boolean DEFAULT false NOT NULL,
    use_macro boolean DEFAULT false NOT NULL,
    indicators text[] DEFAULT '{RSI,MACD,EMA50,EMA200,BB,VWAP}'::text[] NOT NULL,
    lookback_days integer DEFAULT 90 NOT NULL,
    confidence_threshold numeric DEFAULT 0.72 NOT NULL,
    cooldown_entry_minutes integer DEFAULT 240 NOT NULL,
    cooldown_increase_minutes integer DEFAULT 60 NOT NULL,
    cooldown_stop_adj_minutes integer DEFAULT 30 NOT NULL,
    interval_no_position character varying(10) DEFAULT '4h'::character varying NOT NULL,
    interval_position_open character varying(10) DEFAULT '1h'::character varying NOT NULL,
    interval_at_risk character varying(10) DEFAULT '15m'::character varying NOT NULL,
    at_risk_threshold_pct numeric DEFAULT 3.0 NOT NULL,
    dry_run boolean DEFAULT true NOT NULL,
    custom_instructions text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_strategy_config_id_seq; Type: SEQUENCE; Schema: tester; Owner: -
--

CREATE SEQUENCE tester.ai_strategy_config_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ai_strategy_config_id_seq; Type: SEQUENCE OWNED BY; Schema: tester; Owner: -
--

ALTER SEQUENCE tester.ai_strategy_config_id_seq OWNED BY tester.ai_strategy_config.id;


--
-- Name: backtest_runs; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.backtest_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    strategy_id character varying(100) NOT NULL,
    timeframe character varying(10) NOT NULL,
    date_from date NOT NULL,
    date_to date NOT NULL,
    lookback_days integer DEFAULT 90 NOT NULL,
    initial_balance numeric DEFAULT 1000.0 NOT NULL,
    slippage_pct numeric DEFAULT 0.05 NOT NULL,
    fee_pct numeric DEFAULT 0.02 NOT NULL,
    status character varying(40) DEFAULT 'pending'::character varying NOT NULL,
    candles_processed integer DEFAULT 0,
    total_candles integer,
    total_signals integer,
    gate_passed integer,
    llm_failures integer DEFAULT 0,
    llm_failure_rate numeric(5,2),
    total_trades integer,
    winning_trades integer,
    losing_trades integer,
    win_rate numeric(5,2),
    total_pnl numeric(18,8),
    total_pnl_pct numeric(8,4),
    profit_factor numeric(10,4),
    max_drawdown_pct numeric(8,4),
    sharpe_approx numeric(8,4),
    long_count integer,
    short_count integer,
    avg_win numeric(18,8),
    avg_loss numeric(18,8),
    largest_win numeric(18,8),
    largest_loss numeric(18,8),
    total_fees_paid numeric(18,8),
    llm_provider character varying(50),
    llm_model character varying(100),
    estimated_cost_usd numeric(10,6),
    actual_tokens_used integer,
    error_message text,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    dry_signal_mode boolean DEFAULT false NOT NULL,
    CONSTRAINT backtest_runs_status_check CHECK (((status)::text = ANY (ARRAY[('pending'::character varying)::text, ('running'::character varying)::text, ('completed'::character varying)::text, ('failed'::character varying)::text, ('cancelled'::character varying)::text, ('aborted_high_failure_rate'::character varying)::text])))
);


--
-- Name: equity_curve; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.equity_curve (
    id bigint NOT NULL,
    backtest_run_id uuid NOT NULL,
    candle_ts timestamp with time zone NOT NULL,
    realized_balance numeric NOT NULL,
    mark_balance numeric NOT NULL,
    trade_pnl numeric,
    drawdown_pct numeric
);


--
-- Name: equity_curve_id_seq; Type: SEQUENCE; Schema: tester; Owner: -
--

CREATE SEQUENCE tester.equity_curve_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: equity_curve_id_seq; Type: SEQUENCE OWNED BY; Schema: tester; Owner: -
--

ALTER SEQUENCE tester.equity_curve_id_seq OWNED BY tester.equity_curve.id;


--
-- Name: ohlcv_cache; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.ohlcv_cache (
    id bigint NOT NULL,
    symbol character varying(20) NOT NULL,
    timeframe character varying(10) NOT NULL,
    exchange character varying(30) DEFAULT 'binance'::character varying NOT NULL,
    candle_ts timestamp with time zone NOT NULL,
    open numeric NOT NULL,
    high numeric NOT NULL,
    low numeric NOT NULL,
    close numeric NOT NULL,
    volume numeric NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ohlcv_cache_id_seq; Type: SEQUENCE; Schema: tester; Owner: -
--

CREATE SEQUENCE tester.ohlcv_cache_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ohlcv_cache_id_seq; Type: SEQUENCE OWNED BY; Schema: tester; Owner: -
--

ALTER SEQUENCE tester.ohlcv_cache_id_seq OWNED BY tester.ohlcv_cache.id;


--
-- Name: orders; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.orders (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    backtest_run_id uuid NOT NULL,
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    candle_timestamp timestamp with time zone NOT NULL,
    symbol character varying(50) NOT NULL,
    side character varying(10) NOT NULL,
    signal character varying(20) NOT NULL,
    order_type character varying(20) DEFAULT 'market'::character varying NOT NULL,
    size numeric NOT NULL,
    price numeric,
    leverage integer,
    margin_mode character varying(10),
    tp_price numeric,
    sl_price numeric,
    platform character varying(20) DEFAULT 'simulated'::character varying NOT NULL,
    strategy_id character varying(100) NOT NULL,
    status character varying(20) DEFAULT 'filled'::character varying NOT NULL,
    actual_fill_price numeric,
    pnl numeric,
    fee numeric,
    raw_webhook jsonb DEFAULT '{}'::jsonb NOT NULL,
    signal_source character varying(100) DEFAULT 'ai_signal_generator'::character varying NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: strategies; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.strategies (
    id character varying(100) NOT NULL,
    name character varying(100) NOT NULL,
    class character varying(100) DEFAULT 'webhook'::character varying NOT NULL,
    symbol character varying(50) NOT NULL,
    "interval" character varying(10) DEFAULT '1h'::character varying NOT NULL,
    platform character varying(20) DEFAULT 'auto'::character varying NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    type character varying(20) DEFAULT 'internal'::character varying NOT NULL,
    config_yaml text DEFAULT ''::text NOT NULL,
    config jsonb DEFAULT '{}'::jsonb NOT NULL,
    webhook_secret character varying(255) DEFAULT encode(public.gen_random_bytes(16), 'hex'::text) NOT NULL,
    webhook_enabled boolean DEFAULT false,
    description text,
    platform_override character varying(20),
    max_daily_signals integer DEFAULT 500,
    max_position_size numeric DEFAULT 1.0,
    max_leverage integer DEFAULT 10,
    signals_today integer DEFAULT 0,
    pnl_today numeric DEFAULT 0,
    pnl_total numeric DEFAULT 0,
    win_count integer DEFAULT 0,
    loss_count integer DEFAULT 0,
    last_signal_at timestamp with time zone,
    tags text[] DEFAULT '{}'::text[],
    account_id character varying(100),
    pair_id integer,
    allow_quote_variants boolean DEFAULT false NOT NULL,
    allow_cross_charting boolean DEFAULT false NOT NULL,
    default_leverage integer DEFAULT 1 NOT NULL,
    margin_mode character varying(10) DEFAULT 'isolated'::character varying NOT NULL,
    is_deleted boolean DEFAULT false NOT NULL,
    blofin_token text,
    source_matp_id character varying(100),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    ai_config_defaulted boolean DEFAULT false NOT NULL,
    initial_allocation numeric,
    allocation_peak numeric,
    local_signal_mode character varying(10) DEFAULT 'off'::character varying NOT NULL,
    entry_trigger character varying(16) DEFAULT 'bar_close'::character varying NOT NULL,
    CONSTRAINT strategies_entry_trigger_chk CHECK (((entry_trigger)::text = ANY (ARRAY[('bar_close'::character varying)::text, ('intrabar'::character varying)::text])))
);


--
-- Name: strategy_positions; Type: TABLE; Schema: tester; Owner: -
--

CREATE TABLE tester.strategy_positions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    backtest_run_id uuid NOT NULL,
    strategy_id character varying(100) NOT NULL,
    exchange character varying(20) DEFAULT 'simulated'::character varying NOT NULL,
    symbol character varying(50) NOT NULL,
    side character varying(10) NOT NULL,
    entry_price numeric NOT NULL,
    current_price numeric,
    closing_price numeric,
    size numeric NOT NULL,
    leverage integer,
    margin_mode character varying(20),
    pnl_unrealized numeric,
    pnl_realized numeric DEFAULT 0,
    fee_open numeric DEFAULT 0,
    fee_close numeric DEFAULT 0,
    status character varying(20) DEFAULT 'open'::character varying,
    opening_order_id uuid,
    closing_order_id uuid,
    close_reason character varying(50),
    opened_at timestamp with time zone DEFAULT now() NOT NULL,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ai_risk_config_audit id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_risk_config_audit ALTER COLUMN id SET DEFAULT nextval('public.ai_risk_config_audit_id_seq'::regclass);


--
-- Name: ai_signal_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_signal_log ALTER COLUMN id SET DEFAULT nextval('public.ai_signal_log_id_seq'::regclass);


--
-- Name: assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets ALTER COLUMN id SET DEFAULT nextval('public.assets_id_seq'::regclass);


--
-- Name: dead_letter_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter_orders ALTER COLUMN id SET DEFAULT nextval('public.dead_letter_orders_id_seq'::regclass);


--
-- Name: order_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_events ALTER COLUMN id SET DEFAULT nextval('public.order_events_id_seq'::regclass);


--
-- Name: order_execution_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_execution_log ALTER COLUMN id SET DEFAULT nextval('public.order_execution_log_id_seq'::regclass);


--
-- Name: shadow_signals id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shadow_signals ALTER COLUMN id SET DEFAULT nextval('public.shadow_signals_id_seq'::regclass);


--
-- Name: signal_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_log ALTER COLUMN id SET DEFAULT nextval('public.signal_log_id_seq'::regclass);


--
-- Name: social_shadow_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_shadow_orders ALTER COLUMN id SET DEFAULT nextval('public.social_shadow_orders_id_seq'::regclass);


--
-- Name: social_signal_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_signal_log ALTER COLUMN id SET DEFAULT nextval('public.social_signal_log_id_seq'::regclass);


--
-- Name: strategy_performance id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_performance ALTER COLUMN id SET DEFAULT nextval('public.strategy_performance_id_seq'::regclass);


--
-- Name: strategy_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_stats ALTER COLUMN id SET DEFAULT nextval('public.strategy_stats_id_seq'::regclass);


--
-- Name: strategy_webhook_calls id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_webhook_calls ALTER COLUMN id SET DEFAULT nextval('public.strategy_webhook_calls_id_seq'::regclass);


--
-- Name: trading_pairs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_pairs ALTER COLUMN id SET DEFAULT nextval('public.trading_pairs_id_seq'::regclass);


--
-- Name: ai_risk_config id; Type: DEFAULT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_risk_config ALTER COLUMN id SET DEFAULT nextval('tester.ai_risk_config_id_seq'::regclass);


--
-- Name: ai_signal_log id; Type: DEFAULT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_signal_log ALTER COLUMN id SET DEFAULT nextval('tester.ai_signal_log_id_seq'::regclass);


--
-- Name: ai_strategy_config id; Type: DEFAULT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_strategy_config ALTER COLUMN id SET DEFAULT nextval('tester.ai_strategy_config_id_seq'::regclass);


--
-- Name: equity_curve id; Type: DEFAULT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.equity_curve ALTER COLUMN id SET DEFAULT nextval('tester.equity_curve_id_seq'::regclass);


--
-- Name: ohlcv_cache id; Type: DEFAULT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ohlcv_cache ALTER COLUMN id SET DEFAULT nextval('tester.ohlcv_cache_id_seq'::regclass);


--
-- Name: ai_prompt_templates ai_prompt_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_prompt_templates
    ADD CONSTRAINT ai_prompt_templates_pkey PRIMARY KEY (id);


--
-- Name: ai_risk_config_audit ai_risk_config_audit_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_risk_config_audit
    ADD CONSTRAINT ai_risk_config_audit_pkey PRIMARY KEY (id);


--
-- Name: ai_risk_config ai_risk_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_risk_config
    ADD CONSTRAINT ai_risk_config_pkey PRIMARY KEY (strategy_id);


--
-- Name: ai_signal_log ai_signal_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_signal_log
    ADD CONSTRAINT ai_signal_log_pkey PRIMARY KEY (id);


--
-- Name: ai_strategy_config ai_strategy_config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_strategy_config
    ADD CONSTRAINT ai_strategy_config_pkey PRIMARY KEY (strategy_id);


--
-- Name: assets assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_pkey PRIMARY KEY (id);


--
-- Name: assets assets_symbol_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assets
    ADD CONSTRAINT assets_symbol_key UNIQUE (symbol);


--
-- Name: config config_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.config
    ADD CONSTRAINT config_pkey PRIMARY KEY (key);


--
-- Name: dead_letter_orders dead_letter_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter_orders
    ADD CONSTRAINT dead_letter_orders_pkey PRIMARY KEY (id);


--
-- Name: exchange_accounts exchange_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.exchange_accounts
    ADD CONSTRAINT exchange_accounts_pkey PRIMARY KEY (id);


--
-- Name: order_events order_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_pkey PRIMARY KEY (id);


--
-- Name: order_execution_log order_execution_log_client_order_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_execution_log
    ADD CONSTRAINT order_execution_log_client_order_id_key UNIQUE (client_order_id);


--
-- Name: order_execution_log order_execution_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_execution_log
    ADD CONSTRAINT order_execution_log_pkey PRIMARY KEY (id);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: shadow_signals shadow_signals_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.shadow_signals
    ADD CONSTRAINT shadow_signals_pkey PRIMARY KEY (id);


--
-- Name: signal_log signal_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_log
    ADD CONSTRAINT signal_log_pkey PRIMARY KEY (id);


--
-- Name: social_position_state social_position_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_position_state
    ADD CONSTRAINT social_position_state_pkey PRIMARY KEY (source, asset);


--
-- Name: social_shadow_orders social_shadow_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_shadow_orders
    ADD CONSTRAINT social_shadow_orders_pkey PRIMARY KEY (id);


--
-- Name: social_shadow_orders social_shadow_orders_source_channel_msg_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_shadow_orders
    ADD CONSTRAINT social_shadow_orders_source_channel_msg_id_key UNIQUE (source, channel_msg_id);


--
-- Name: social_signal_log social_signal_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.social_signal_log
    ADD CONSTRAINT social_signal_log_pkey PRIMARY KEY (id);


--
-- Name: strategies strategies_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategies
    ADD CONSTRAINT strategies_pkey PRIMARY KEY (id);


--
-- Name: strategies strategies_webhook_secret_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategies
    ADD CONSTRAINT strategies_webhook_secret_key UNIQUE (webhook_secret);


--
-- Name: strategy_performance strategy_performance_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_performance
    ADD CONSTRAINT strategy_performance_pkey PRIMARY KEY (id);


--
-- Name: strategy_performance strategy_performance_strategy_id_period_type_period_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_performance
    ADD CONSTRAINT strategy_performance_strategy_id_period_type_period_date_key UNIQUE (strategy_id, period_type, period_date);


--
-- Name: strategy_positions strategy_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_positions
    ADD CONSTRAINT strategy_positions_pkey PRIMARY KEY (id);


--
-- Name: strategy_stats strategy_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_stats
    ADD CONSTRAINT strategy_stats_pkey PRIMARY KEY (id);


--
-- Name: strategy_stats strategy_stats_strategy_id_period_date_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_stats
    ADD CONSTRAINT strategy_stats_strategy_id_period_date_key UNIQUE (strategy_id, period_date);


--
-- Name: strategy_webhook_calls strategy_webhook_calls_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_webhook_calls
    ADD CONSTRAINT strategy_webhook_calls_pkey PRIMARY KEY (id);


--
-- Name: trading_pairs trading_pairs_base_asset_id_quote_asset_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_pairs
    ADD CONSTRAINT trading_pairs_base_asset_id_quote_asset_id_key UNIQUE (base_asset_id, quote_asset_id);


--
-- Name: trading_pairs trading_pairs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_pairs
    ADD CONSTRAINT trading_pairs_pkey PRIMARY KEY (id);


--
-- Name: ai_risk_config ai_risk_config_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_risk_config
    ADD CONSTRAINT ai_risk_config_pkey PRIMARY KEY (id);


--
-- Name: ai_risk_config ai_risk_config_strategy_id_key; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_risk_config
    ADD CONSTRAINT ai_risk_config_strategy_id_key UNIQUE (strategy_id);


--
-- Name: ai_signal_log ai_signal_log_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_signal_log
    ADD CONSTRAINT ai_signal_log_pkey PRIMARY KEY (id);


--
-- Name: ai_strategy_config ai_strategy_config_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_strategy_config
    ADD CONSTRAINT ai_strategy_config_pkey PRIMARY KEY (id);


--
-- Name: ai_strategy_config ai_strategy_config_strategy_id_key; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_strategy_config
    ADD CONSTRAINT ai_strategy_config_strategy_id_key UNIQUE (strategy_id);


--
-- Name: backtest_runs backtest_runs_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.backtest_runs
    ADD CONSTRAINT backtest_runs_pkey PRIMARY KEY (id);


--
-- Name: equity_curve equity_curve_backtest_run_id_candle_ts_key; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.equity_curve
    ADD CONSTRAINT equity_curve_backtest_run_id_candle_ts_key UNIQUE (backtest_run_id, candle_ts);


--
-- Name: equity_curve equity_curve_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.equity_curve
    ADD CONSTRAINT equity_curve_pkey PRIMARY KEY (id);


--
-- Name: ohlcv_cache ohlcv_cache_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ohlcv_cache
    ADD CONSTRAINT ohlcv_cache_pkey PRIMARY KEY (id);


--
-- Name: ohlcv_cache ohlcv_cache_symbol_timeframe_exchange_candle_ts_key; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ohlcv_cache
    ADD CONSTRAINT ohlcv_cache_symbol_timeframe_exchange_candle_ts_key UNIQUE (symbol, timeframe, exchange, candle_ts);


--
-- Name: orders orders_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.orders
    ADD CONSTRAINT orders_pkey PRIMARY KEY (id);


--
-- Name: strategies strategies_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.strategies
    ADD CONSTRAINT strategies_pkey PRIMARY KEY (id);


--
-- Name: strategy_positions strategy_positions_pkey; Type: CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.strategy_positions
    ADD CONSTRAINT strategy_positions_pkey PRIMARY KEY (id);


--
-- Name: ai_sl_confidence_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ai_sl_confidence_idx ON public.ai_signal_log USING btree (confidence);


--
-- Name: ai_sl_proposed_action_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ai_sl_proposed_action_idx ON public.ai_signal_log USING btree (proposed_action);


--
-- Name: ai_sl_strategy_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ai_sl_strategy_id_idx ON public.ai_signal_log USING btree (strategy_id);


--
-- Name: ai_sl_triggered_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ai_sl_triggered_at_idx ON public.ai_signal_log USING btree (triggered_at DESC);


--
-- Name: idx_orders_closes_position; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_closes_position ON public.orders USING btree (closes_position_id) WHERE (closes_position_id IS NOT NULL);


--
-- Name: idx_orders_strategy_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_orders_strategy_source ON public.orders USING btree (strategy_id, signal_source);


--
-- Name: idx_shadow_signals_strat_bar; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_shadow_signals_strat_bar ON public.shadow_signals USING btree (strategy_id, signal_bar_time);


--
-- Name: idx_strat_perf_period; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strat_perf_period ON public.strategy_performance USING btree (period_type, period_date DESC);


--
-- Name: idx_strat_perf_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strat_perf_strategy ON public.strategy_performance USING btree (strategy_id);


--
-- Name: idx_strat_pos_closing_order_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strat_pos_closing_order_id ON public.strategy_positions USING btree (closing_order_id);


--
-- Name: idx_strat_pos_opened_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strat_pos_opened_at ON public.strategy_positions USING btree (opened_at DESC);


--
-- Name: idx_strat_pos_strategy_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strat_pos_strategy_status ON public.strategy_positions USING btree (strategy_id, status);


--
-- Name: idx_strat_pos_symbol_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strat_pos_symbol_status ON public.strategy_positions USING btree (symbol, status);


--
-- Name: idx_strat_stats_strategy_date; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strat_stats_strategy_date ON public.strategy_stats USING btree (strategy_id, period_date DESC);


--
-- Name: idx_strategies_webhook_secret; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strategies_webhook_secret ON public.strategies USING btree (webhook_secret);


--
-- Name: idx_webhook_calls_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_webhook_calls_status ON public.strategy_webhook_calls USING btree (http_status);


--
-- Name: idx_webhook_calls_strategy; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_webhook_calls_strategy ON public.strategy_webhook_calls USING btree (strategy_id, received_at DESC);


--
-- Name: ix_social_shadow_decision; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_shadow_decision ON public.social_shadow_orders USING btree (decision, evaluated_at DESC);


--
-- Name: ix_social_signal_actionable; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_social_signal_actionable ON public.social_signal_log USING btree (is_actionable, posted_at DESC);


--
-- Name: oel_exchange_oid_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX oel_exchange_oid_idx ON public.order_execution_log USING btree (exchange_order_id);


--
-- Name: oel_signal_log_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX oel_signal_log_idx ON public.order_execution_log USING btree (signal_log_id);


--
-- Name: order_events_order_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX order_events_order_id_idx ON public.order_events USING btree (order_id);


--
-- Name: orders_account_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orders_account_id_idx ON public.orders USING btree (account_id);


--
-- Name: orders_pair_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orders_pair_id_idx ON public.orders USING btree (pair_id);


--
-- Name: orders_platform_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orders_platform_idx ON public.orders USING btree (platform);


--
-- Name: orders_received_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orders_received_at_idx ON public.orders USING btree (received_at DESC);


--
-- Name: orders_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orders_status_idx ON public.orders USING btree (status);


--
-- Name: orders_strategy_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX orders_strategy_id_idx ON public.orders USING btree (strategy_id);


--
-- Name: shadow_signals_uniq_exit; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX shadow_signals_uniq_exit ON public.shadow_signals USING btree (strategy_id, signal, signal_bar_time, COALESCE(exit_reason, ''::character varying));


--
-- Name: signal_log_outcome_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX signal_log_outcome_idx ON public.signal_log USING btree (outcome);


--
-- Name: signal_log_strategy_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX signal_log_strategy_time_idx ON public.signal_log USING btree (strategy_id, received_at DESC);


--
-- Name: sp_pair_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sp_pair_id_idx ON public.strategy_positions USING btree (pair_id);


--
-- Name: sp_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sp_status_idx ON public.strategy_positions USING btree (status);


--
-- Name: sp_strategy_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX sp_strategy_id_idx ON public.strategy_positions USING btree (strategy_id);


--
-- Name: swc_received_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX swc_received_at_idx ON public.strategy_webhook_calls USING btree (received_at DESC);


--
-- Name: swc_strategy_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX swc_strategy_id_idx ON public.strategy_webhook_calls USING btree (strategy_id);


--
-- Name: uq_social_signal_source_msg; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_social_signal_source_msg ON public.social_signal_log USING btree (source, channel_msg_id);


--
-- Name: uq_strat_pos_one_open; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_strat_pos_one_open ON public.strategy_positions USING btree (strategy_id, symbol, side) WHERE ((status)::text = 'open'::text);


--
-- Name: tester_equity_run_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_equity_run_idx ON tester.equity_curve USING btree (backtest_run_id, candle_ts);


--
-- Name: tester_ohlcv_lookup_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_ohlcv_lookup_idx ON tester.ohlcv_cache USING btree (symbol, timeframe, exchange, candle_ts);


--
-- Name: tester_orders_run_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_orders_run_idx ON tester.orders USING btree (backtest_run_id);


--
-- Name: tester_orders_strategy_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_orders_strategy_idx ON tester.orders USING btree (strategy_id);


--
-- Name: tester_pos_run_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_pos_run_idx ON tester.strategy_positions USING btree (backtest_run_id);


--
-- Name: tester_pos_strategy_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_pos_strategy_idx ON tester.strategy_positions USING btree (strategy_id, status);


--
-- Name: tester_runs_status_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_runs_status_idx ON tester.backtest_runs USING btree (status);


--
-- Name: tester_runs_strategy_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_runs_strategy_idx ON tester.backtest_runs USING btree (strategy_id, created_at DESC);


--
-- Name: tester_signal_log_cooldown_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_signal_log_cooldown_idx ON tester.ai_signal_log USING btree (backtest_run_id, strategy_id, proposed_action, gate_passed, triggered_at DESC);


--
-- Name: tester_signal_log_run_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_signal_log_run_idx ON tester.ai_signal_log USING btree (backtest_run_id);


--
-- Name: tester_signal_log_strategy_idx; Type: INDEX; Schema: tester; Owner: -
--

CREATE INDEX tester_signal_log_strategy_idx ON tester.ai_signal_log USING btree (strategy_id, triggered_at DESC);


--
-- Name: config update_config_modtime; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_config_modtime BEFORE UPDATE ON public.config FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: exchange_accounts update_exchange_accounts_modtime; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_exchange_accounts_modtime BEFORE UPDATE ON public.exchange_accounts FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: exchange_accounts update_exchange_accounts_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_exchange_accounts_updated_at BEFORE UPDATE ON public.exchange_accounts FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: orders update_orders_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON public.orders FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: strategies update_strategies_modtime; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_strategies_modtime BEFORE UPDATE ON public.strategies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: strategies update_strategies_updated_at; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_strategies_updated_at BEFORE UPDATE ON public.strategies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: strategy_positions update_strategy_positions_modtime; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_strategy_positions_modtime BEFORE UPDATE ON public.strategy_positions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: strategy_stats update_strategy_stats_modtime; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER update_strategy_stats_modtime BEFORE UPDATE ON public.strategy_stats FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: strategy_positions update_tester_positions_updated_at; Type: TRIGGER; Schema: tester; Owner: -
--

CREATE TRIGGER update_tester_positions_updated_at BEFORE UPDATE ON tester.strategy_positions FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: ai_risk_config update_tester_risk_config_updated_at; Type: TRIGGER; Schema: tester; Owner: -
--

CREATE TRIGGER update_tester_risk_config_updated_at BEFORE UPDATE ON tester.ai_risk_config FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: backtest_runs update_tester_runs_updated_at; Type: TRIGGER; Schema: tester; Owner: -
--

CREATE TRIGGER update_tester_runs_updated_at BEFORE UPDATE ON tester.backtest_runs FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: strategies update_tester_strategies_updated_at; Type: TRIGGER; Schema: tester; Owner: -
--

CREATE TRIGGER update_tester_strategies_updated_at BEFORE UPDATE ON tester.strategies FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: ai_strategy_config update_tester_strategy_config_updated_at; Type: TRIGGER; Schema: tester; Owner: -
--

CREATE TRIGGER update_tester_strategy_config_updated_at BEFORE UPDATE ON tester.ai_strategy_config FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();


--
-- Name: ai_risk_config ai_risk_config_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_risk_config
    ADD CONSTRAINT ai_risk_config_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategies(id) ON DELETE CASCADE;


--
-- Name: ai_signal_log ai_signal_log_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_signal_log
    ADD CONSTRAINT ai_signal_log_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: ai_signal_log ai_signal_log_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_signal_log
    ADD CONSTRAINT ai_signal_log_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategies(id);


--
-- Name: ai_strategy_config ai_strategy_config_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ai_strategy_config
    ADD CONSTRAINT ai_strategy_config_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategies(id) ON DELETE CASCADE;


--
-- Name: dead_letter_orders dead_letter_orders_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dead_letter_orders
    ADD CONSTRAINT dead_letter_orders_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: order_events order_events_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_events
    ADD CONSTRAINT order_events_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.orders(id);


--
-- Name: order_execution_log order_execution_log_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_execution_log
    ADD CONSTRAINT order_execution_log_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.exchange_accounts(id);


--
-- Name: order_execution_log order_execution_log_signal_log_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.order_execution_log
    ADD CONSTRAINT order_execution_log_signal_log_id_fkey FOREIGN KEY (signal_log_id) REFERENCES public.signal_log(id);


--
-- Name: orders orders_closes_position_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_closes_position_id_fkey FOREIGN KEY (closes_position_id) REFERENCES public.strategy_positions(id);


--
-- Name: orders orders_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.orders
    ADD CONSTRAINT orders_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.trading_pairs(id);


--
-- Name: strategies strategies_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategies
    ADD CONSTRAINT strategies_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.trading_pairs(id);


--
-- Name: strategy_performance strategy_performance_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_performance
    ADD CONSTRAINT strategy_performance_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategies(id) ON DELETE CASCADE;


--
-- Name: strategy_positions strategy_positions_closing_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_positions
    ADD CONSTRAINT strategy_positions_closing_order_id_fkey FOREIGN KEY (closing_order_id) REFERENCES public.orders(id) ON DELETE RESTRICT;


--
-- Name: strategy_positions strategy_positions_opening_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_positions
    ADD CONSTRAINT strategy_positions_opening_order_id_fkey FOREIGN KEY (opening_order_id) REFERENCES public.orders(id) ON DELETE RESTRICT;


--
-- Name: strategy_positions strategy_positions_pair_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_positions
    ADD CONSTRAINT strategy_positions_pair_id_fkey FOREIGN KEY (pair_id) REFERENCES public.trading_pairs(id);


--
-- Name: strategy_positions strategy_positions_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_positions
    ADD CONSTRAINT strategy_positions_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategies(id) ON DELETE RESTRICT;


--
-- Name: strategy_stats strategy_stats_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_stats
    ADD CONSTRAINT strategy_stats_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategies(id) ON DELETE RESTRICT;


--
-- Name: strategy_webhook_calls strategy_webhook_calls_strategy_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategy_webhook_calls
    ADD CONSTRAINT strategy_webhook_calls_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES public.strategies(id);


--
-- Name: trading_pairs trading_pairs_base_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_pairs
    ADD CONSTRAINT trading_pairs_base_asset_id_fkey FOREIGN KEY (base_asset_id) REFERENCES public.assets(id);


--
-- Name: trading_pairs trading_pairs_quote_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_pairs
    ADD CONSTRAINT trading_pairs_quote_asset_id_fkey FOREIGN KEY (quote_asset_id) REFERENCES public.assets(id);


--
-- Name: ai_risk_config ai_risk_config_strategy_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_risk_config
    ADD CONSTRAINT ai_risk_config_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES tester.strategies(id) ON DELETE CASCADE;


--
-- Name: ai_signal_log ai_signal_log_backtest_run_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_signal_log
    ADD CONSTRAINT ai_signal_log_backtest_run_id_fkey FOREIGN KEY (backtest_run_id) REFERENCES tester.backtest_runs(id) ON DELETE CASCADE;


--
-- Name: ai_signal_log ai_signal_log_order_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_signal_log
    ADD CONSTRAINT ai_signal_log_order_id_fkey FOREIGN KEY (order_id) REFERENCES tester.orders(id) ON DELETE SET NULL;


--
-- Name: ai_strategy_config ai_strategy_config_strategy_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.ai_strategy_config
    ADD CONSTRAINT ai_strategy_config_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES tester.strategies(id) ON DELETE CASCADE;


--
-- Name: backtest_runs backtest_runs_strategy_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.backtest_runs
    ADD CONSTRAINT backtest_runs_strategy_id_fkey FOREIGN KEY (strategy_id) REFERENCES tester.strategies(id);


--
-- Name: equity_curve equity_curve_backtest_run_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.equity_curve
    ADD CONSTRAINT equity_curve_backtest_run_id_fkey FOREIGN KEY (backtest_run_id) REFERENCES tester.backtest_runs(id) ON DELETE CASCADE;


--
-- Name: orders orders_backtest_run_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.orders
    ADD CONSTRAINT orders_backtest_run_id_fkey FOREIGN KEY (backtest_run_id) REFERENCES tester.backtest_runs(id) ON DELETE CASCADE;


--
-- Name: strategy_positions strategy_positions_backtest_run_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.strategy_positions
    ADD CONSTRAINT strategy_positions_backtest_run_id_fkey FOREIGN KEY (backtest_run_id) REFERENCES tester.backtest_runs(id) ON DELETE CASCADE;


--
-- Name: strategy_positions strategy_positions_closing_order_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.strategy_positions
    ADD CONSTRAINT strategy_positions_closing_order_id_fkey FOREIGN KEY (closing_order_id) REFERENCES tester.orders(id) ON DELETE SET NULL;


--
-- Name: strategy_positions strategy_positions_opening_order_id_fkey; Type: FK CONSTRAINT; Schema: tester; Owner: -
--

ALTER TABLE ONLY tester.strategy_positions
    ADD CONSTRAINT strategy_positions_opening_order_id_fkey FOREIGN KEY (opening_order_id) REFERENCES tester.orders(id) ON DELETE SET NULL;



--
-- Seed data (curated subset — reference/config tables only; no secrets, no runtime data)
--

-- Data for Name: assets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.assets (id, symbol, name) FROM stdin;
1	BTC	Bitcoin
2	ETH	Ethereum
3	USDT	Tether
4	SOL	Solana
\.


--
-- Name: assets_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.assets_id_seq', 6, true);



-- Data for Name: trading_pairs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.trading_pairs (id, base_asset_id, quote_asset_id, exchange_meta) FROM stdin;
1	1	3	{"blofin": {"instId": "BTC-USDT"}}
\.


--
-- Name: trading_pairs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.trading_pairs_id_seq', 2, true);



-- Data for Name: ai_prompt_templates; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ai_prompt_templates (id, name, description, system_prompt, created_at) FROM stdin;
trend_following	Trend Following	Identifies and trades sustained directional momentum using EMA crossovers and MACD confirmation.	You are a quantitative crypto analyst specializing in trend-following strategies on perpetual futures.\nYour primary signals are EMA crossovers (50/200), MACD histogram direction, and volume confirmation.\nYou prefer high-confidence setups with clear directional bias. You avoid counter-trend trades.\nIn ranging markets, output HOLD. Only recommend a trade when multiple indicators align.	2026-06-08 20:00:12.217763+00
mean_reversion	Mean Reversion	Identifies overextended price moves and trades the return to equilibrium.	You are a quantitative crypto analyst specializing in mean-reversion strategies on perpetual futures.\nYour primary signals are RSI extremes (oversold <30, overbought >70), Bollinger Band squeezes, and VWAP deviation.\nYou trade against extended moves, expecting price to return toward the mean.\nYou require confirmation that momentum is slowing before recommending entry. You use tight stop losses.	2026-06-08 20:00:12.217763+00
breakout	Breakout Hunter	Identifies and trades volume-confirmed breakouts above key structural levels.	You are a quantitative crypto analyst specializing in breakout strategies on perpetual futures.\nYour primary signals are price breaking above/below consolidation zones with volume confirmation (>150% average).\nYou look for compression patterns (BB squeeze, low ATR) followed by expansion.\nYou require the breakout candle to close convincingly beyond the level. False breakouts without volume are HOLD.	2026-06-08 20:00:12.217763+00
scalper	Scalper	High-frequency short-duration trades on lower timeframes with tight risk management.	You are a quantitative crypto analyst specializing in scalping strategies on perpetual futures.\nYou trade on short timeframes (15m-1H). Your primary signals are VWAP positioning, order flow imbalance, and momentum bursts.\nYou use very tight stop losses (0.3-0.8%). You close positions quickly — target hold time under 2 hours.\nYou avoid entering during low-volume periods or major news events.	2026-06-08 20:00:12.217763+00
conservative	Conservative	Low-frequency, high-conviction trades only. Capital preservation priority.	You are a conservative quantitative crypto analyst specializing in low-frequency, high-conviction setups on perpetual futures.\nYou require confluence of at least 4 independent signals before recommending a trade.\nYou express confidence above 0.85 only when the setup is exceptional. You default to HOLD when uncertain.\nYou give significant weight to macro conditions and sentiment data. Capital preservation always overrides opportunity.	2026-06-08 20:00:12.217763+00
range_rotation	Range Rotation	Trades range boundaries (fade highs, buy lows) while the range holds; stands aside or flips directional when the range breaks with confirmation.	You are a quantitative crypto analyst specializing in range-trading strategies on perpetual futures.\n\nPHASE 1 — RANGE IDENTIFICATION:\nA valid range requires: at least 2 touches of support and 2 touches of resistance, flat EMA 50 (no sustained slope), RSI oscillating between roughly 35-65 without pinning at extremes, and price contained within the Bollinger Bands. If no valid range exists, output HOLD.\n\nPHASE 2 — TRADING THE RANGE:\nOpen SHORT near resistance when: price is within 1.5% of the range high, RSI > 60 and rolling over, and volume is declining on the approach (no breakout pressure).\nOpen LONG near support when: price is within 1.5% of the range low, RSI < 40 and curling up, and volume is declining on the approach.\nStop loss goes just beyond the range boundary (0.5-1.0% past it). Take profit targets the opposite side of the range or the midpoint (VWAP) for partial exits.\nNEVER enter in the middle of the range — the edge is only at the boundaries.\n\nPHASE 3 — BREAK DETECTION (overrides everything):\nThe range is considered BROKEN when: a candle closes beyond the boundary by more than 0.5x ATR(14) with volume above 150% of average, OR two consecutive closes beyond the boundary.\nIf holding a position when the range breaks AGAINST you: output close_long or close_short immediately. Do not average down. Do not wait for the stop.\nIf flat when a confirmed break occurs: you may output a trade in the DIRECTION of the break (open_long on upside break, open_short on downside break), but only with volume confirmation and a retest holding the broken level as new support/resistance. A break without retest or volume is a trap — output HOLD.\n\nRISK POSTURE:\nRange trades are mean-probability, small-edge trades: confidence should rarely exceed 0.80 inside the range. Break-and-retest trades may score higher. Funding rate extremes or major scheduled news invalidate the range thesis — output HOLD.	2026-06-09 20:31:58.132871+00
geometric_range	Geometric Range & Breakout	Trades trendline-defined boundaries (swing-based channels, wedges, triangles) by working the range with resting limit orders — places/amends a limit at the boundary, cancels it on re-fit invalidation, apex convergence, or a confirmed breakout.	You are a quantitative crypto analyst specializing in geometry-driven range and breakout strategies on perpetual futures. You work the range with RESTING LIMIT ORDERS rather than market-fading it — each cycle you review the GEOMETRIC PATTERN and OPEN ORDERS sections and choose exactly ONE action: place a resting limit, amend a resting limit, cancel a resting limit, market-trade a confirmed breakout, or hold.\n\nPHASE 1 — PATTERN VALIDITY:\nThe GEOMETRIC PATTERN section describes the detected price structure. Before acting on it:\n- Only place a new resting order for patterns with fit_quality = "strong". A "weak" fit indicates low trendline R² — the structure is unreliable; output HOLD (existing resting orders may still be managed per Phase 4/5 below).\n- Require at least 2 touches on each boundary (upper_touches ≥ 2 AND lower_touches ≥ 2) before placing a new order on that boundary.\n- Use position_in_range_pct to gauge where price currently sits: 0 = at the lower boundary, 100 = at the upper boundary.\n\nPHASE 2 — WORKING THE RANGE WITH RESTING LIMITS (channels):\nFor horizontal, ascending, and descending channels with parallel boundaries, check the OPEN ORDERS section first:\n- If no resting BUY order exists and the pattern passes Phase 1 checks: output place_limit_long with limit_price set to the lower boundary. Derive stop_loss_pct/take_profit_pct so the stop sits just below the lower boundary (0.5–1x ATR beyond it) and the target sits at the upper boundary (or the midpoint for a smaller target).\n- If no resting SELL order exists and the pattern passes Phase 1 checks: output place_limit_short with limit_price set to the upper boundary, stop just above it, target the lower boundary or midpoint.\n- If a resting order already exists on a side, do NOT place a duplicate on that side — either hold, or move to Phase 3 if the boundary has moved.\n- Never place a limit in the middle of the range (position_in_range_pct 20–80) — the edge is only at the boundaries.\n\nPHASE 3 — RE-FIT: AMEND A STALE BOUNDARY ORDER:\nIf the OPEN ORDERS section shows a resting order whose price no longer matches the current upper_boundary/lower_boundary (the trendline has re-fit as new bars close), output amend_order with target_order_id set to that order's order_id and limit_price set to the new boundary price. Do not cancel-and-replace with place_limit_* for a re-fit — amend the existing order instead.\n\nPHASE 4 — CONVERGING SHAPES (triangles and wedges):\nAscending triangle, descending triangle, rising wedge, and falling wedge have boundaries that converge. Apply Phase 2/3 rules with these modifications:\n- Reduce target distance as pattern_age_bars grows: a pattern that has been forming for 80+ bars is near resolution — tighten targets/stops on new placements.\n- Ascending triangle bias: bullish breakout favoured. Prefer place_limit_long at the lower boundary; skip or cancel_order any resting short at the upper boundary instead of amending it.\n- Descending triangle bias: mirror of above — prefer place_limit_short at the upper boundary, skip or cancel_order any resting long at the lower boundary.\n- Rising wedge: bias is DOWNSIDE on resolution — treat resting longs at the lower boundary cautiously and prioritize Phase 5 breakout handling as pattern_age_bars grows.\n- Falling wedge: bias is UPSIDE on resolution — same caution applied to resting shorts at the upper boundary.\n- If the pattern has converged to its apex (upper_boundary and lower_boundary within roughly 1x ATR of each other) or fit_quality has degraded to "weak": cancel_order any resting boundary order(s) still open — the structure is no longer tradeable, never leave a fade resting into an apex.\n\nPHASE 5 — BREAKOUT OVERRIDE (overrides Phases 2-4 entirely):\nA confirmed breakout occurs when: a candle closes beyond a boundary by more than 0.5x ATR(14) with volume above average, OR two consecutive closes beyond the boundary.\n- On any confirmed breakout: cancel_order the resting boundary order on the side being broken through — a fade resting into a confirmed break will get run over. This takes priority over placing or amending anything else this cycle.\n- Once the stale resting order is cleared (confirm via OPEN ORDERS in a later cycle), you may market-trade the breakout direction: open_long on a confirmed upside break, open_short on a confirmed downside break, stop beyond the broken boundary (now acting as support/resistance).\n- If holding a position fading the broken boundary: close it immediately (close_long/close_short) — do not average down, do not wait for the stop.\n- A single-candle wick beyond the boundary without volume confirmation is a false break — maintain the range read, do not cancel resting orders for it.\n\nCONFIDENCE CALIBRATION:\n- fit_quality = "strong", upper_touches ≥ 3, lower_touches ≥ 3: may reach 0.85.\n- fit_quality = "strong", exactly 2 touches on either side: cap confidence at 0.75.\n- pattern_age_bars > 80 in a converging shape (apex is near): reduce confidence by 0.05 — the pattern is unstable.\n- Extreme funding rates, major scheduled news, or a VWAP far outside the pattern boundaries all reduce confidence further.\n- If fit_quality = "weak" or touch counts fail Phase 1: output HOLD for new placements regardless of other signals (order management in Phases 3-5 still applies to existing resting orders).	2026-07-01 18:54:08.424001+00
\.



-- Data for Name: config; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.config (key, value, updated_at) FROM stdin;
max_order_size_btc	1.0	2026-05-18 15:41:14.011312+00
max_order_size_eth	10.0	2026-05-18 15:41:14.011312+00
active_platform	hyperliquid	2026-06-11 18:20:58.536961+00
\.



--
-- PostgreSQL database dump complete
--

\unrestrict B2fToI9eynrCCMxaq1px5Re3Au2F1E8uYVfv4PoCjYuu1Me7BRSj3VGd3L0JXPs
