ALTER TABLE strategies
  ADD COLUMN IF NOT EXISTS strategy_source VARCHAR(20) NOT NULL DEFAULT 'tradingview';

COMMENT ON COLUMN strategies.strategy_source IS 'Signal source: tradingview | ai_engine | manual';
