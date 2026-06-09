ALTER TABLE ai_strategy_config
  ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(20) NOT NULL DEFAULT 'google',
  ADD COLUMN IF NOT EXISTS llm_model    VARCHAR(50) NOT NULL DEFAULT 'gemini-2.0-flash';

COMMENT ON COLUMN ai_strategy_config.llm_provider IS 'LLM provider: google | openai | anthropic';
COMMENT ON COLUMN ai_strategy_config.llm_model    IS 'Model name as accepted by the provider SDK';

ALTER TABLE ai_signal_log
  ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(20),
  ADD COLUMN IF NOT EXISTS llm_model    VARCHAR(50);
