/* === Fix for 002_strategy_centric.sql === */

INSERT INTO strategies (
    id, name, class, symbol, interval, platform, enabled, 
    config_yaml, webhook_secret, description, tags
)
VALUES (
    'test', 'Test Strategy', 'TestStrategy', '*', '1m', 'auto', false, 
    '', 'temp_secret_for_test', 'Default strategy for smoke test and development orders', '{test,development}'
)
ON CONFLICT (id) DO NOTHING;
