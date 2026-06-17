# db/migrations — MANIFEST

> Apply order within a duplicated number is ambiguous; see header dates. Full archive/renumber deferred until `db/init.sql` is regenerated as a complete baseline.

## Migration files

| File | Purpose |
|------|---------|
| `001_add_strategy_webhooks.sql` ⚠️ | Add webhook_secret and metadata columns to strategies |
| `001_exchange_accounts.sql` ⚠️ | Create exchange_accounts table; add account_id to strategies/orders |
| `002_strategy_centric.sql` ⚠️ | Add max_position_size, max_leverage, capital_allocation_percent to strategies |
| `002_symbol_coupling.sql` ⚠️ | Add allow_quote_variants and allow_cross_charting flags to strategies |
| `003_add_strategy_type.sql` ⚠️ | Add type column to strategies (internal / tradingview) |
| `003_default_leverage.sql` ⚠️ | Add default_leverage to strategies for signals that omit leverage |
| `004_add_closing_price.sql` ⚠️ | Add closing_price to strategy_positions |
| `004_strategy_config_jsonb.sql` ⚠️ | Add config JSONB column to strategies |
| `005_signal_log.sql` | Create signal_log and order_execution_log tables |
| `006_ai_signal_generator.sql` | Create ai_strategy_config and ai_risk_config tables |
| `007_ai_llm_provider.sql` | Add llm_provider and llm_model columns to ai_strategy_config |
| `008_strategy_source.sql` | Add strategy_source column to strategies (tradingview / ai_engine / manual) |
| `009_signal_log_ai_fields.sql` | Add ai_reasoning and ai_confidence columns to signal_log |
| `010_range_rotation_template.sql` | Insert range_rotation prompt template into ai_prompt_templates |
| `011_tester_schema.sql` | Create tester schema and all strategy-tester tables (v1.1) |
| `012_backtest_dry_signal.sql` ⚠️ | Add dry_signal_mode to tester.backtest_runs |
| `012_close_reason.sql` ⚠️ | Add close_reason to strategy_positions |
| `013_ai_config_defaulted.sql` | Add ai_config_defaulted flag to tester.strategies |
| `014_position_identity.sql` | Enforce unique index: one open position per (strategy_id, symbol, side) |
| `015_reconcile_and_pnl_attribution.sql` | Add reconcile_miss_count; link closing orders to positions |
| `016_capital_allocation.sql` | Add fixed-margin sizing (capital_allocation $) and max_drawdown_pct |
| `017_drop_daily_loss.sql` | Drop daily-loss cap columns from ai_risk_config |
| `018_drop_daily_drawdown.sql` | Drop strategies.max_daily_drawdown_percent (superseded by migration 016) |
| `019_drop_capital_allocation_percent.sql` | Drop strategies.capital_allocation_percent (superseded by migration 016) |
| `020_drop_emergency_exit_pct.sql` | Drop ai_strategy_config.emergency_exit_pct (feature removed) |
| `021_drop_max_position_size.sql` | Drop max_position_size from strategies and ai_risk_config (guard removed) |
| `fix_strategies_insert.sql` | _(unnumbered)_ Fix for incomplete INSERT in 002_strategy_centric.sql |

## Collisions (⚠️ = ambiguous apply order)

| Sequence number | Files with that number |
|-----------------|------------------------|
| 001 | `001_add_strategy_webhooks.sql`, `001_exchange_accounts.sql` |
| 002 | `002_strategy_centric.sql`, `002_symbol_coupling.sql` |
| 003 | `003_add_strategy_type.sql`, `003_default_leverage.sql` |
| 004 | `004_add_closing_price.sql`, `004_strategy_config_jsonb.sql` |
| 012 | `012_backtest_dry_signal.sql`, `012_close_reason.sql` |

`fix_strategies_insert.sql` has no number prefix; its position in the apply sequence is inferred from the header comment ("Fix for 002_strategy_centric.sql").
