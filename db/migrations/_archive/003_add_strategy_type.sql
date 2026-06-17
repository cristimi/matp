ALTER TABLE strategies ADD COLUMN type VARCHAR(20) NOT NULL DEFAULT 'internal' CHECK (type IN ('internal', 'tradingview'));
