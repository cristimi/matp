-- Migration 012: add dry_signal_mode to tester.backtest_runs
ALTER TABLE tester.backtest_runs
    ADD COLUMN IF NOT EXISTS dry_signal_mode BOOLEAN NOT NULL DEFAULT FALSE;
