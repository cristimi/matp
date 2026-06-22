--
-- PostgreSQL database dump
--

\restrict KDsbdpFynvghw4Dfy9flvxUdO3I18Q9lyc9gQERSuTIRa89GzZOdytZ7dG1rmsK

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
    llm_model character varying(50) DEFAULT 'gemini-2.0-flash'::character varying NOT NULL
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
    avg_fill_price numeric DEFAULT 0,
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
    webhook_enabled boolean DEFAULT true,
    description text,
    platform_override character varying(20),
    max_daily_signals integer DEFAULT 500,
    blofin_token text,
    max_leverage integer DEFAULT 10,
    signals_today integer DEFAULT 0,
    pnl_today numeric DEFAULT 0,
    pnl_total numeric DEFAULT 0,
    win_count integer DEFAULT 0,
    loss_count integer DEFAULT 0,
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
    drawdown_anchor_pnl numeric DEFAULT 0 NOT NULL,
    initial_allocation numeric,
    allocation_peak numeric,
    CONSTRAINT strategies_type_check CHECK (((type)::text = ANY (ARRAY[('internal'::character varying)::text, ('tradingview'::character varying)::text])))
);


--
-- Name: COLUMN strategies.strategy_source; Type: COMMENT; Schema: public; Owner: -
--

COMMENT ON COLUMN public.strategies.strategy_source IS 'Signal source: tradingview | ai_engine | manual';


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
    current_price numeric,
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
    allocation_peak numeric
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
-- Name: signal_log id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_log ALTER COLUMN id SET DEFAULT nextval('public.signal_log_id_seq'::regclass);


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
-- Data for Name: ai_prompt_templates; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ai_prompt_templates (id, name, description, system_prompt, created_at) FROM stdin;
trend_following	Trend Following	Identifies and trades sustained directional momentum using EMA crossovers and MACD confirmation.	You are a quantitative crypto analyst specializing in trend-following strategies on perpetual futures.\nYour primary signals are EMA crossovers (50/200), MACD histogram direction, and volume confirmation.\nYou prefer high-confidence setups with clear directional bias. You avoid counter-trend trades.\nIn ranging markets, output HOLD. Only recommend a trade when multiple indicators align.	2026-06-08 20:00:12.217763+00
mean_reversion	Mean Reversion	Identifies overextended price moves and trades the return to equilibrium.	You are a quantitative crypto analyst specializing in mean-reversion strategies on perpetual futures.\nYour primary signals are RSI extremes (oversold <30, overbought >70), Bollinger Band squeezes, and VWAP deviation.\nYou trade against extended moves, expecting price to return toward the mean.\nYou require confirmation that momentum is slowing before recommending entry. You use tight stop losses.	2026-06-08 20:00:12.217763+00
breakout	Breakout Hunter	Identifies and trades volume-confirmed breakouts above key structural levels.	You are a quantitative crypto analyst specializing in breakout strategies on perpetual futures.\nYour primary signals are price breaking above/below consolidation zones with volume confirmation (>150% average).\nYou look for compression patterns (BB squeeze, low ATR) followed by expansion.\nYou require the breakout candle to close convincingly beyond the level. False breakouts without volume are HOLD.	2026-06-08 20:00:12.217763+00
scalper	Scalper	High-frequency short-duration trades on lower timeframes with tight risk management.	You are a quantitative crypto analyst specializing in scalping strategies on perpetual futures.\nYou trade on short timeframes (15m-1H). Your primary signals are VWAP positioning, order flow imbalance, and momentum bursts.\nYou use very tight stop losses (0.3-0.8%). You close positions quickly — target hold time under 2 hours.\nYou avoid entering during low-volume periods or major news events.	2026-06-08 20:00:12.217763+00
conservative	Conservative	Low-frequency, high-conviction trades only. Capital preservation priority.	You are a conservative quantitative crypto analyst specializing in low-frequency, high-conviction setups on perpetual futures.\nYou require confluence of at least 4 independent signals before recommending a trade.\nYou express confidence above 0.85 only when the setup is exceptional. You default to HOLD when uncertain.\nYou give significant weight to macro conditions and sentiment data. Capital preservation always overrides opportunity.	2026-06-08 20:00:12.217763+00
range_rotation	Range Rotation	Trades range boundaries (fade highs, buy lows) while the range holds; stands aside or flips directional when the range breaks with confirmation.	You are a quantitative crypto analyst specializing in range-trading strategies on perpetual futures.\n\nPHASE 1 — RANGE IDENTIFICATION:\nA valid range requires: at least 2 touches of support and 2 touches of resistance, flat EMA 50 (no sustained slope), RSI oscillating between roughly 35-65 without pinning at extremes, and price contained within the Bollinger Bands. If no valid range exists, output HOLD.\n\nPHASE 2 — TRADING THE RANGE:\nOpen SHORT near resistance when: price is within 1.5% of the range high, RSI > 60 and rolling over, and volume is declining on the approach (no breakout pressure).\nOpen LONG near support when: price is within 1.5% of the range low, RSI < 40 and curling up, and volume is declining on the approach.\nStop loss goes just beyond the range boundary (0.5-1.0% past it). Take profit targets the opposite side of the range or the midpoint (VWAP) for partial exits.\nNEVER enter in the middle of the range — the edge is only at the boundaries.\n\nPHASE 3 — BREAK DETECTION (overrides everything):\nThe range is considered BROKEN when: a candle closes beyond the boundary by more than 0.5x ATR(14) with volume above 150% of average, OR two consecutive closes beyond the boundary.\nIf holding a position when the range breaks AGAINST you: output close_long or close_short immediately. Do not average down. Do not wait for the stop.\nIf flat when a confirmed break occurs: you may output a trade in the DIRECTION of the break (open_long on upside break, open_short on downside break), but only with volume confirmation and a retest holding the broken level as new support/resistance. A break without retest or volume is a trap — output HOLD.\n\nRISK POSTURE:\nRange trades are mean-probability, small-edge trades: confidence should rarely exceed 0.80 inside the range. Break-and-retest trades may score higher. Funding rate extremes or major scheduled news invalidate the range thesis — output HOLD.	2026-06-09 20:31:58.132871+00
\.


--
-- Data for Name: ai_risk_config; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ai_risk_config (strategy_id, max_concurrent_trades, updated_at, updated_by) FROM stdin;
\.


--
-- Data for Name: ai_risk_config_audit; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ai_risk_config_audit (id, strategy_id, changed_at, changed_by, field_name, old_value, new_value) FROM stdin;
\.


--
-- Data for Name: ai_signal_log; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ai_signal_log (id, strategy_id, triggered_at, trigger_reason, cycle_interval, prompt_template, data_sources_used, context_tokens, proposed_action, confidence, reasoning, gate_passed, gate_rejection_reason, webhook_fired, webhook_status, order_id, dry_run, outcome_pnl, outcome_pct, outcome_filled_at, llm_provider, llm_model) FROM stdin;
1	ai-btc-6f8c	2026-06-19 16:44:05.960138+00	startup	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	759	hold	0.700	Price is significantly below VWAP (-11.64%), suggesting an extended downward move that could lead to mean reversion. However, RSI (42.54) is neutral and not oversold, and Bollinger Bands are not indicating a squeeze. MACD remains bearish. The strategy requires confirmation of slowing momentum before entry, which is not present, leading to insufficient confluence for a high-conviction trade setup at this time.	f	hold_or_adjust	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
2	ai-btc-6f8c	2026-06-19 17:01:15.165703+00	startup	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	\N	\N	\N	\N	f	llm_failed	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
3	ai-btc-6f8c	2026-06-19 17:07:50.263627+00	startup	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	\N	\N	\N	\N	f	llm_failed	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
4	ai-btc-6f8c	2026-06-19 17:17:08.842125+00	startup	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	760	hold	0.550	The RSI is neutral at 40.94, not indicating an oversold extreme (<30) as a primary signal for a mean-reversion long. Bollinger Bands are at the mid-band, not showing a squeeze. While price is significantly below VWAP (-11.92%) and the Fear & Greed Index is at 14 (Extreme Fear), there is no clear confirmation that momentum is slowing, with MACD still negative and price falling for 4 consecutive days. The lack of confluence from primary signals (RSI extreme, BB squeeze) and no clear momentum slowing prevents a high-conviction entry.	f	hold_or_adjust	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
5	ai-btc-6f8c	2026-06-19 21:17:41.356903+00	scheduled	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	760	hold	0.500	While price is significantly below VWAP (-11.63%), indicating an extended move, there is insufficient confluence for a high-conviction mean-reversion trade. RSI (43.16) is neutral, not indicating oversold conditions, and Bollinger Bands are at the mid-band, not showing a squeeze. Momentum slowing is not clearly confirmed by other indicators, and the overall trend remains bearish with price below both EMA50 and EMA200.	f	hold_or_adjust	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
6	ai-btc-6f8c	2026-06-20 01:18:05.765615+00	scheduled	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	\N	\N	\N	\N	f	llm_failed	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
7	ai-btc-6f8c	2026-06-20 05:18:27.34984+00	scheduled	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	759	hold	0.700	RSI (48.21) is neutral, not indicating an oversold condition. Bollinger Bands are at the mid-band, not showing a squeeze. While price is significantly below VWAP (-10.91%) and the Fear & Greed Index is at Extreme Fear (23), suggesting a potential mean-reversion long, the lack of an oversold RSI and the prevailing bearish trend (price below EMA50/200) prevent a high-conviction entry. Momentum slowing confirmation is insufficient given the overall bearish context from news and EMAs, despite a recent bullish MACD cross.	f	hold_or_adjust	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
8	ai-btc-6f8c	2026-06-20 09:18:54.836709+00	scheduled	4h	mean_reversion	{technical,fear_greed,funding_rate,open_interest,news,btc_dominance}	759	open_long	0.700	Price is significantly below VWAP (-11.23%), indicating an extended move away from the mean, aligning with a mean-reversion strategy. The Fear & Greed Index is at 23 (Extreme Fear), suggesting potential capitulation. Volume is significantly below average (49.4% below 20MA), indicating slowing bearish momentum, which provides confirmation for entry.	f	confidence_below_threshold	f	\N	\N	f	\N	\N	\N	google	gemini-2.5-flash
\.


--
-- Data for Name: ai_strategy_config; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.ai_strategy_config (strategy_id, interval_no_position, interval_position_open, interval_at_risk, at_risk_threshold_pct, use_technical, use_fear_greed, use_funding_rate, use_open_interest, use_news, use_economic_calendar, use_btc_dominance, use_macro, indicators, lookback_days, confidence_threshold, cooldown_entry_minutes, cooldown_increase_minutes, cooldown_stop_adj_minutes, template_id, custom_instructions, trigger_news_high, trigger_volume_spike, trigger_funding_spike, trigger_key_level, trigger_liquidation, volume_spike_threshold, funding_spike_threshold, dry_run, updated_at, updated_by, llm_provider, llm_model) FROM stdin;
ai-btc-6f8c	4h	15m	5m	1.50	t	t	t	t	t	f	t	f	{RSI,MACD,EMA50,EMA200,BB,VWAP}	90	0.720	0	60	30	mean_reversion	\N	t	t	t	t	f	300.0	0.0500	f	2026-06-19 07:37:23.062254+00	\N	google	gemini-2.5-flash
\.


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
-- Data for Name: config; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.config (key, value, updated_at) FROM stdin;
max_order_size_btc	1.0	2026-05-18 15:41:14.011312+00
max_order_size_eth	10.0	2026-05-18 15:41:14.011312+00
active_platform	hyperliquid	2026-06-11 18:20:58.536961+00
\.


--
-- Data for Name: dead_letter_orders; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.dead_letter_orders (id, order_id, failed_at, reason, retry_count, last_retry) FROM stdin;
\.


--
-- Data for Name: exchange_accounts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.exchange_accounts (id, exchange, mode, label, credentials, is_active, created_at, updated_at) FROM stdin;
blofin-blofin-demo-v5vr	blofin	demo	Blofin Demo	\\xefa001667ae7edc95b7fc7dcc476f35842974a941db7417048c7370e8476c2fd5f721ba8cebae39d164b8f5fbfb739be547e58f6e79c84dd9070e88b92bce6cd1aacfc9506c462bdaf3914f98466173a8615375de97ecdb1a51448ad211e8969a9758a1281959b01289e06e337f7182cb02921dffac8111a078ddda448224b468685f64e1e8988b3f49a4f33a1bf3e3afae279f7	t	2026-06-19 07:31:54.001685+00	2026-06-19 07:31:55.761355+00
hyperliquid-hyperliquid-hqdy	hyperliquid	demo	Hyperliquid	\\xd3035553411ea7dae3646e05282df7624092efab5484489ad13d8d4066581cfec0bf0d86ab2c58f9b6dce7723d4c62b377a5b27284159bc0c70571fedf234c8e95abdd193282ae31c170e54eec7bf9e2d82ac4de2486f173dfbf5efe7b4a61e4babdc0d570a2c9c38b62369f34e7bf444ed4d10d7379712159ac421e12a4c5a663d274cfe78be143930f020e0445a72184c9c005dff1933b621e6928e5fd864da8b09b480bcfd1172e2d32	t	2026-06-19 07:35:08.333053+00	2026-06-19 07:35:09.215327+00
\.


--
-- Data for Name: order_events; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.order_events (id, order_id, event_time, from_status, to_status, message) FROM stdin;
\.


--
-- Data for Name: order_execution_log; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.order_execution_log (id, signal_log_id, account_id, exchange, exchange_order_id, client_order_id, symbol, side, order_type, requested_size, requested_price, status, cumulative_filled, avg_fill_price, exchange_fee, error_message, placed_at, filled_at, created_at, updated_at) FROM stdin;
1	2	hyperliquid-hyperliquid-hqdy	hyperliquid	\N	6093ff34-e25c-4af9-b867-afd4970894ff	HYPE-USDT	buy	market	3.920031359999999853727103982237167656421661376953125	\N	rejected	0	0	0	Order could not immediately match against any resting orders. asset=135	2026-06-19 10:06:43.617898+00	\N	2026-06-19 10:06:43.606081+00	2026-06-19 10:06:46.781172+00
2	3	hyperliquid-hyperliquid-hqdy	hyperliquid	\N	451b27f2-fa9b-4005-b939-7cc62c2c00d7	HYPE-USDT	buy	market	2.7713113800000002129308995790779590606689453125	\N	rejected	0	0	0	Order could not immediately match against any resting orders. asset=135	2026-06-19 13:26:24.15206+00	\N	2026-06-19 13:26:24.131661+00	2026-06-19 13:26:27.253917+00
3	4	hyperliquid-hyperliquid-hqdy	hyperliquid	55246830057	a13646af-f431-424d-92dd-8c78f182bf04	BTC-USDT	buy	market	0.003185519999999999844753073574565860326401889324188232421875	\N	filled	0	0	0	\N	2026-06-19 13:31:51.033994+00	2026-06-19 13:31:54.527183+00	2026-06-19 13:31:51.012293+00	2026-06-19 13:31:54.527492+00
4	5	blofin-blofin-demo-v5vr	blofin	1000130566359	00832430-ae1c-488b-9a3c-96992de75f91	HYPE-USDT	buy	market	2.93461674000000005690935722668655216693878173828125	\N	filled	0	0	0	\N	2026-06-19 13:37:16.879012+00	2026-06-19 13:37:20.177903+00	2026-06-19 13:37:16.861774+00	2026-06-19 13:37:20.178247+00
\.


--
-- Data for Name: orders; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.orders (id, received_at, symbol, side, signal, order_type, size, price, leverage, margin_mode, tp_price, sl_price, platform, strategy_id, status, exchange_order_id, pnl, raw_webhook, raw_response, error_msg, updated_at, signal_source, signal_metadata, indicator_price, actual_fill_price, pair_id, account_id, closes_position_id) FROM stdin;
4351ffc6-026b-4b2a-873b-69bfe8afb659	2026-06-19 10:06:43.314375+00	HYPE-USDT	buy	open_long	market	3.92003136	\N	10	isolated	\N	46.4282	auto	hype-test-7db4	rejected	\N	\N	{"side": "buy", "size": "3.92003136", "price": null, "signal": "open_long", "leverage": 10, "sl_price": "46.4282", "tp_price": null, "timestamp": "2026-06-19T10:06:41Z", "base_asset": "HYPE", "order_type": "market", "margin_mode": null, "quote_asset": "USDT", "signal_source": "tradingview", "indicator_price": null, "signal_metadata": {"entry_ref": 51.02, "sl_source": "liquidation_safe", "used_size": 3.92003136, "original_size": 5.0, "sl_distance_pct": 9.0, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}, "target_position": null}	{"status": "ok", "response": {"data": {"statuses": [{"error": "Order could not immediately match against any resting orders. asset=135"}]}, "type": "order"}}	Order could not immediately match against any resting orders. asset=135	2026-06-19 10:06:46.826504+00	tradingview	{"entry_ref": 51.02, "sl_source": "liquidation_safe", "used_size": 3.92003136, "original_size": 5.0, "sl_distance_pct": 9.0, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}	\N	\N	\N	\N	\N
84e79694-a141-4cd1-9952-b4ff84cef2b4	2026-06-19 13:26:23.719437+00	HYPE-USDT	buy	open_long	market	2.77131138	\N	10	isolated	\N	65.6729	auto	hype-test-7db4	rejected	\N	\N	{"side": "buy", "size": "2.77131138", "price": null, "signal": "open_long", "leverage": 10, "sl_price": "65.6729", "tp_price": null, "timestamp": "2026-06-19T13:26:20Z", "base_asset": "HYPE", "order_type": "market", "margin_mode": null, "quote_asset": "USDT", "signal_source": "tradingview", "indicator_price": null, "signal_metadata": {"entry_ref": 72.168, "sl_source": "liquidation_safe", "used_size": 2.77131138, "original_size": 5.0, "sl_distance_pct": 9.0, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}, "target_position": null}	{"status": "ok", "response": {"data": {"statuses": [{"error": "Order could not immediately match against any resting orders. asset=135"}]}, "type": "order"}}	Order could not immediately match against any resting orders. asset=135	2026-06-19 13:26:27.276302+00	tradingview	{"entry_ref": 72.168, "sl_source": "liquidation_safe", "used_size": 2.77131138, "original_size": 5.0, "sl_distance_pct": 9.0, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}	\N	\N	\N	\N	\N
1ac4c406-3347-474f-85d8-89c1ec1b1b9f	2026-06-19 13:31:50.552333+00	BTC-USDT	buy	open_long	market	0.00318552	\N	40	isolated	\N	61842.2	auto	tv-btc-test-hl-94e1	filled	55246830057	\N	{"side": "buy", "size": "0.00318552", "price": null, "signal": "open_long", "leverage": 40, "sl_price": "61842.2", "tp_price": null, "timestamp": "2026-06-19T13:31:48Z", "base_asset": "BTC", "order_type": "market", "margin_mode": null, "quote_asset": "USDT", "signal_source": "tradingview", "indicator_price": null, "signal_metadata": {"entry_ref": 62784.0, "sl_source": "liquidation_safe", "used_size": 0.00318552, "original_size": 0.05, "sl_distance_pct": 1.5001, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}, "target_position": null}	{"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 55246830057, "avgPx": "62790.0", "totalSz": "0.00319"}}, "waitingForTrigger"]}, "type": "order"}}	\N	2026-06-19 13:31:54.579475+00	tradingview	{"entry_ref": 62784.0, "sl_source": "liquidation_safe", "used_size": 0.00318552, "original_size": 0.05, "sl_distance_pct": 1.5001, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}	\N	62790.0	\N	\N	\N
0728af9b-8c17-4d55-992e-e02a71077ffa	2026-06-19 13:37:16.519607+00	HYPE-USDT	buy	open_long	market	2.93461674	\N	10	isolated	\N	62.0183	auto	hype-test-7db4	filled	1000130566359	0	{"side": "buy", "size": "2.93461674", "price": null, "signal": "open_long", "leverage": 10, "sl_price": "62.0183", "tp_price": null, "timestamp": "2026-06-19T13:37:10Z", "base_asset": "HYPE", "order_type": "market", "margin_mode": null, "quote_asset": "USDT", "signal_source": "tradingview", "indicator_price": null, "signal_metadata": {"entry_ref": 68.152, "sl_source": "liquidation_safe", "used_size": 2.93461674, "original_size": 5.0, "sl_distance_pct": 9.0, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}, "target_position": null}	{"msg": "", "code": "0", "data": [{"msg": "Order placed", "code": "0", "orderId": "1000130566359", "clientOrderId": ""}]}	\N	2026-06-19 13:37:20.221178+00	tradingview	{"entry_ref": 68.152, "sl_source": "liquidation_safe", "used_size": 2.93461674, "original_size": 5.0, "sl_distance_pct": 9.0, "ref_price_source": "exchange_mark", "size_scaled_to_margin": true}	\N	68.151	\N	\N	\N
\.


--
-- Data for Name: signal_log; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.signal_log (id, received_at, source_ip, strategy_id, http_status, outcome, error_detail, raw_body, duration_ms, ai_reasoning, ai_confidence) FROM stdin;
1	2026-06-19 10:05:59.820866+00	172.18.0.12	hype-test-7db4	422	guard_rejected	Leverage 20x exceeds strategy maximum of 10x for strategy hype-test-7db4.	{"side": "buy", "size": "5", "token": "a358dde02769b0482d121843e1a2cd94", "signal": "open_long", "leverage": "20", "timestamp": "2026-06-19T10:05:58Z", "base_asset": "HYPE", "order_type": "market", "quote_asset": "USDT"}	61	\N	\N
2	2026-06-19 10:06:42.100561+00	172.18.0.12	hype-test-7db4	200	route_failed	Order could not immediately match against any resting orders. asset=135	{"side": "buy", "size": "5", "token": "a358dde02769b0482d121843e1a2cd94", "signal": "open_long", "leverage": "10", "timestamp": "2026-06-19T10:06:41Z", "base_asset": "HYPE", "order_type": "market", "quote_asset": "USDT"}	4736	\N	\N
3	2026-06-19 13:26:22.225232+00	172.18.0.12	hype-test-7db4	200	route_failed	Order could not immediately match against any resting orders. asset=135	{"side": "buy", "size": "5", "token": "a358dde02769b0482d121843e1a2cd94", "signal": "open_long", "leverage": "10", "timestamp": "2026-06-19T13:26:20Z", "base_asset": "HYPE", "order_type": "market", "quote_asset": "USDT"}	5062	\N	\N
4	2026-06-19 13:31:49.240853+00	172.18.0.12	tv-btc-test-hl-94e1	200	filled	\N	{"side": "buy", "size": "0.05", "token": "a18117c6aab67a60347d295ec47baafa", "signal": "open_long", "leverage": "40", "timestamp": "2026-06-19T13:31:48Z", "base_asset": "BTC", "order_type": "market", "quote_asset": "USDT"}	5355	\N	\N
5	2026-06-19 13:37:15.806405+00	172.18.0.12	hype-test-7db4	200	filled	\N	{"side": "buy", "size": "5", "token": "a358dde02769b0482d121843e1a2cd94", "signal": "open_long", "leverage": "10", "timestamp": "2026-06-19T13:37:10Z", "base_asset": "HYPE", "order_type": "market", "quote_asset": "USDT"}	4433	\N	\N
\.


--
-- Data for Name: strategies; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.strategies (id, name, class, symbol, "interval", platform, enabled, config_yaml, created_at, updated_at, webhook_secret, webhook_enabled, description, platform_override, max_daily_signals, blofin_token, max_leverage, signals_today, pnl_today, pnl_total, win_count, loss_count, last_signal_at, tags, type, pair_id, account_id, allow_quote_variants, allow_cross_charting, default_leverage, is_deleted, config, margin_mode, strategy_source, capital_allocation, margin_per_trade, max_drawdown_pct, drawdown_anchor_pnl, initial_allocation, allocation_peak) FROM stdin;
tv-btc-test-hl-94e1	TV BTC Test HL	webhook	BTC-USDT	1h	auto	t		2026-06-19 13:30:15.758677+00	2026-06-20 10:15:18.74637+00	a18117c6aab67a60347d295ec47baafa	t		\N	500	\N	40	1	0	0	0	0	2026-06-19 13:31:50.580053+00	{}	internal	\N	hyperliquid-hyperliquid-hqdy	t	f	20	f	{}	isolated	tradingview	100	5	50	0	100	100
hype-test-7db4	HYPE Test	webhook	HYPE-USDT	4h	auto	t		2026-06-19 07:39:00.475144+00	2026-06-20 10:15:18.74637+00	a358dde02769b0482d121843e1a2cd94	t		\N	500	\N	10	3	0	0	0	0	2026-06-19 13:37:16.547791+00	{}	internal	\N	blofin-blofin-demo-v5vr	t	f	10	f	{}	isolated	tradingview	200	20	75	0	200	200
ai-btc-6f8c	AI BTC	webhook	BTC-USDT	1h	auto	t		2026-06-19 07:37:22.895888+00	2026-06-20 10:15:18.74637+00	50be8bd56dd338cec7292aec27919e92	t		\N	500	\N	40	0	0	0	0	0	\N	{}	internal	\N	hyperliquid-hyperliquid-hqdy	t	f	10	f	{}	isolated	ai_engine	100	10	50	0	100	100
\.


--
-- Data for Name: strategy_performance; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.strategy_performance (id, strategy_id, period_type, period_date, total_signals, filled_orders, failed_orders, rejected_orders, winning_trades, losing_trades, neutral_trades, win_rate, total_pnl, avg_pnl, median_pnl, max_win, max_loss, consecutive_wins, consecutive_losses, profit_factor, largest_drawdown, calculated_at, updated_at) FROM stdin;
\.


--
-- Data for Name: strategy_positions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.strategy_positions (id, strategy_id, exchange, symbol, side, entry_price, current_price, size, leverage, margin_mode, pnl_unrealized, pnl_realized, status, opening_order_id, closing_order_id, opened_at, closed_at, created_at, updated_at, closing_price, liquidation_price, pair_id, close_reason, reconcile_miss_count, reconcile_divergent, reconcile_exchange_size, reconcile_divergence_at) FROM stdin;
c8927ca5-917a-45b7-83e0-c79750770aaf	tv-btc-test-hl-94e1	auto	BTC-USDT	long	62790.0	62790.0	0.00318552	40	isolated	\N	0	open	1ac4c406-3347-474f-85d8-89c1ec1b1b9f	\N	2026-06-19 13:31:54.861506+00	\N	2026-06-19 13:31:54.861506+00	2026-06-19 13:31:54.861506+00	\N	\N	1	\N	0	f	\N	\N
c495d9e8-e208-4a1a-8f88-11647abbe7e6	hype-test-7db4	auto	HYPE-USDT	long	68.151	68.151	2.9000000000000000000	10	isolated	\N	0	open	0728af9b-8c17-4d55-992e-e02a71077ffa	\N	2026-06-19 13:37:20.433483+00	\N	2026-06-19 13:37:20.433483+00	2026-06-19 13:37:20.433483+00	\N	\N	\N	\N	0	f	\N	\N
\.


--
-- Data for Name: strategy_stats; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.strategy_stats (id, strategy_id, period_date, trades_count, trades_won, trades_lost, win_rate, pnl_total, pnl_avg, max_drawdown, capital_deployed, leverage_avg, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: strategy_webhook_calls; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.strategy_webhook_calls (id, strategy_id, received_at, http_status, error_message, source_ip) FROM stdin;
1	hype-test-7db4	2026-06-19 10:06:43.295173+00	200	\N	\N
2	hype-test-7db4	2026-06-19 13:26:23.711157+00	200	\N	\N
3	tv-btc-test-hl-94e1	2026-06-19 13:31:50.546764+00	200	\N	\N
4	hype-test-7db4	2026-06-19 13:37:16.496606+00	200	\N	\N
\.


--
-- Data for Name: trading_pairs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.trading_pairs (id, base_asset_id, quote_asset_id, exchange_meta) FROM stdin;
1	1	3	{"blofin": {"instId": "BTC-USDT"}}
\.


--
-- Data for Name: ai_risk_config; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.ai_risk_config (id, strategy_id, max_position_size_pct, max_concurrent_trades, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: ai_signal_log; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.ai_signal_log (id, backtest_run_id, strategy_id, triggered_at, trigger_reason, cycle_interval, prompt_template, data_sources_used, context_tokens, proposed_action, confidence, reasoning, gate_passed, gate_rejection_reason, dry_run, llm_provider, llm_model, webhook_fired, webhook_status, order_id) FROM stdin;
\.


--
-- Data for Name: ai_strategy_config; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.ai_strategy_config (id, strategy_id, template_id, llm_provider, llm_model, use_technical, use_fear_greed, use_funding_rate, use_open_interest, use_news, use_btc_dominance, use_macro, indicators, lookback_days, confidence_threshold, cooldown_entry_minutes, cooldown_increase_minutes, cooldown_stop_adj_minutes, interval_no_position, interval_position_open, interval_at_risk, at_risk_threshold_pct, dry_run, custom_instructions, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: backtest_runs; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.backtest_runs (id, strategy_id, timeframe, date_from, date_to, lookback_days, initial_balance, slippage_pct, fee_pct, status, candles_processed, total_candles, total_signals, gate_passed, llm_failures, llm_failure_rate, total_trades, winning_trades, losing_trades, win_rate, total_pnl, total_pnl_pct, profit_factor, max_drawdown_pct, sharpe_approx, long_count, short_count, avg_win, avg_loss, largest_win, largest_loss, total_fees_paid, llm_provider, llm_model, estimated_cost_usd, actual_tokens_used, error_message, started_at, completed_at, created_at, updated_at, dry_signal_mode) FROM stdin;
\.


--
-- Data for Name: equity_curve; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.equity_curve (id, backtest_run_id, candle_ts, realized_balance, mark_balance, trade_pnl, drawdown_pct) FROM stdin;
\.


--
-- Data for Name: ohlcv_cache; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.ohlcv_cache (id, symbol, timeframe, exchange, candle_ts, open, high, low, close, volume, fetched_at) FROM stdin;
\.


--
-- Data for Name: orders; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.orders (id, backtest_run_id, received_at, candle_timestamp, symbol, side, signal, order_type, size, price, leverage, margin_mode, tp_price, sl_price, platform, strategy_id, status, actual_fill_price, pnl, fee, raw_webhook, signal_source, updated_at) FROM stdin;
\.


--
-- Data for Name: strategies; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.strategies (id, name, class, symbol, "interval", platform, enabled, type, config_yaml, config, webhook_secret, webhook_enabled, description, platform_override, max_daily_signals, max_position_size, max_leverage, signals_today, pnl_today, pnl_total, win_count, loss_count, last_signal_at, tags, account_id, pair_id, allow_quote_variants, allow_cross_charting, default_leverage, margin_mode, is_deleted, blofin_token, source_matp_id, created_at, updated_at, ai_config_defaulted, initial_allocation, allocation_peak) FROM stdin;
\.


--
-- Data for Name: strategy_positions; Type: TABLE DATA; Schema: tester; Owner: -
--

COPY tester.strategy_positions (id, backtest_run_id, strategy_id, exchange, symbol, side, entry_price, current_price, closing_price, size, leverage, margin_mode, pnl_unrealized, pnl_realized, fee_open, fee_close, status, opening_order_id, closing_order_id, close_reason, opened_at, closed_at, created_at, updated_at) FROM stdin;
\.


--
-- Name: ai_risk_config_audit_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.ai_risk_config_audit_id_seq', 1, false);


--
-- Name: ai_signal_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.ai_signal_log_id_seq', 8, true);


--
-- Name: assets_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.assets_id_seq', 6, true);


--
-- Name: dead_letter_orders_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.dead_letter_orders_id_seq', 1, false);


--
-- Name: order_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.order_events_id_seq', 1, false);


--
-- Name: order_execution_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.order_execution_log_id_seq', 4, true);


--
-- Name: signal_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.signal_log_id_seq', 5, true);


--
-- Name: strategy_performance_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.strategy_performance_id_seq', 1, false);


--
-- Name: strategy_stats_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.strategy_stats_id_seq', 1, false);


--
-- Name: strategy_webhook_calls_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.strategy_webhook_calls_id_seq', 4, true);


--
-- Name: trading_pairs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.trading_pairs_id_seq', 2, true);


--
-- Name: ai_risk_config_id_seq; Type: SEQUENCE SET; Schema: tester; Owner: -
--

SELECT pg_catalog.setval('tester.ai_risk_config_id_seq', 1, false);


--
-- Name: ai_signal_log_id_seq; Type: SEQUENCE SET; Schema: tester; Owner: -
--

SELECT pg_catalog.setval('tester.ai_signal_log_id_seq', 1, false);


--
-- Name: ai_strategy_config_id_seq; Type: SEQUENCE SET; Schema: tester; Owner: -
--

SELECT pg_catalog.setval('tester.ai_strategy_config_id_seq', 1, false);


--
-- Name: equity_curve_id_seq; Type: SEQUENCE SET; Schema: tester; Owner: -
--

SELECT pg_catalog.setval('tester.equity_curve_id_seq', 1, false);


--
-- Name: ohlcv_cache_id_seq; Type: SEQUENCE SET; Schema: tester; Owner: -
--

SELECT pg_catalog.setval('tester.ohlcv_cache_id_seq', 1, false);


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
-- Name: signal_log signal_log_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signal_log
    ADD CONSTRAINT signal_log_pkey PRIMARY KEY (id);


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
-- Name: idx_strategies_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_strategies_enabled ON public.strategies USING btree (webhook_enabled);


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
-- PostgreSQL database dump complete
--

\unrestrict KDsbdpFynvghw4Dfy9flvxUdO3I18Q9lyc9gQERSuTIRa89GzZOdytZ7dG1rmsK

