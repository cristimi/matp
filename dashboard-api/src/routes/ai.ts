import { Router, Request, Response } from 'express';
import { getPool } from '../db';

const router = Router();
const AI_URL = process.env.AI_SIGNAL_GENERATOR_URL || 'http://ai-signal-generator:8005';

// ── Constants ─────────────────────────────────────────────────────────────────

const INTERVAL_PATTERN = /^[0-9]+(m|h|d)$/;
const VALID_PROVIDERS   = ['google', 'openai', 'anthropic', 'groq'];

const ALLOWED_CONFIG_FIELDS = [
  'interval_no_position', 'interval_position_open', 'interval_at_risk',
  'at_risk_threshold_pct', 'use_technical', 'use_fear_greed', 'use_funding_rate',
  'use_open_interest', 'use_news', 'use_economic_calendar', 'use_btc_dominance',
  'use_macro', 'use_geometry', 'indicators', 'lookback_days', 'confidence_threshold',
  'cooldown_entry_minutes', 'cooldown_increase_minutes', 'cooldown_stop_adj_minutes',
  'template_id', 'custom_instructions', 'trigger_news_high', 'trigger_volume_spike',
  'trigger_funding_spike', 'trigger_key_level', 'trigger_liquidation',
  'volume_spike_threshold', 'funding_spike_threshold', 'dry_run',
  'llm_provider', 'llm_model',
  'use_mtf_structure', 'use_orderbook', 'use_volume_profile', 'use_cvd',
  'use_momentum_divergence', 'use_volatility_regime', 'use_funding_history',
  'use_liquidations', 'use_limit_orders',
];

const RISK_FIELDS = [
  'max_concurrent_trades',
];

const RISK_DEFAULTS: Record<string, number> = {
  max_concurrent_trades: 1,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatConfig(row: any): any {
  if (!row) return null;
  return {
    ...row,
    at_risk_threshold_pct:   Number(row.at_risk_threshold_pct),
    confidence_threshold:    Number(row.confidence_threshold),
    volume_spike_threshold:  Number(row.volume_spike_threshold),
    funding_spike_threshold: Number(row.funding_spike_threshold),
  };
}

function formatRiskConfig(row: any): any {
  if (!row) return null;
  return { ...row };
}

function formatSignal(row: any): any {
  return {
    ...row,
    confidence:  row.confidence  != null ? Number(row.confidence)  : null,
    outcome_pnl: row.outcome_pnl != null ? Number(row.outcome_pnl) : null,
    outcome_pct: row.outcome_pct != null ? Number(row.outcome_pct) : null,
  };
}

function notifyConfigReload(strategyId: string): void {
  fetch(`${AI_URL}/internal/schedulers/${strategyId}/reconcile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  }).catch((e) => console.warn(`Could not notify scheduler reconcile for ${strategyId}:`, e));
}

// ── GET /templates ────────────────────────────────────────────────────────────

router.get('/templates', async (_req: Request, res: Response) => {
  try {
    const { rows } = await getPool().query(
      'SELECT id, name, description, system_prompt FROM ai_prompt_templates ORDER BY name'
    );
    res.json(rows);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── GET /models ───────────────────────────────────────────────────────────────

router.get('/models', async (req: Request, res: Response) => {
  const provider = (req.query.provider as string) || '';
  try {
    const response = await fetch(`${AI_URL}/internal/models?provider=${provider}`);
    const data = await response.json();
    res.json(data);
  } catch (err: any) {
    console.error(`Error fetching models for provider ${provider}:`, err);
    res.status(500).json({ provider, models: [], key_configured: false, error: err.message });
  }
});

// ── GET /usage — actual LLM token spend (total / per strategy / per model) ────
// Actuals come from ai_signal_log.input/output/total_tokens (provider-reported,
// migration 047; NULL on pre-047 rows and calls that failed before a response).

router.get('/usage', async (req: Request, res: Response) => {
  const from = (req.query.from as string) || '1970-01-01';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(from)) {
    return res.status(400).json({ error: 'from must be YYYY-MM-DD' });
  }
  try {
    const totalQ = getPool().query(
      `SELECT COUNT(*) FILTER (WHERE total_tokens IS NOT NULL) AS tracked_calls,
              COUNT(*)                                         AS llm_calls,
              COALESCE(SUM(input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(total_tokens), 0)  AS total_tokens
       FROM ai_signal_log
       WHERE triggered_at >= $1 AND context_tokens > 0`,
      [from]
    );
    const perStrategyQ = getPool().query(
      `SELECT strategy_id,
              COUNT(*) FILTER (WHERE total_tokens IS NOT NULL) AS tracked_calls,
              COALESCE(SUM(input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(total_tokens), 0)  AS total_tokens
       FROM ai_signal_log
       WHERE triggered_at >= $1 AND context_tokens > 0
       GROUP BY strategy_id
       ORDER BY SUM(total_tokens) DESC NULLS LAST`,
      [from]
    );
    const perModelQ = getPool().query(
      `SELECT llm_provider, llm_model,
              COUNT(*) FILTER (WHERE total_tokens IS NOT NULL) AS tracked_calls,
              COALESCE(SUM(input_tokens), 0)  AS input_tokens,
              COALESCE(SUM(output_tokens), 0) AS output_tokens,
              COALESCE(SUM(total_tokens), 0)  AS total_tokens
       FROM ai_signal_log
       WHERE triggered_at >= $1 AND context_tokens > 0
       GROUP BY llm_provider, llm_model
       ORDER BY SUM(total_tokens) DESC NULLS LAST`,
      [from]
    );
    const [total, perStrategy, perModel] = await Promise.all([totalQ, perStrategyQ, perModelQ]);
    const num = (r: any) => Object.fromEntries(
      Object.entries(r).map(([k, v]) => [k, typeof v === 'string' && /^\d+$/.test(v) ? Number(v) : v])
    );
    res.json({
      from,
      note: 'actuals from provider usage_metadata; rows before 2026-07-07 (migration 047) have no actuals',
      total:        num(total.rows[0]),
      per_strategy: perStrategy.rows.map(num),
      per_model:    perModel.rows.map(num),
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── Register specific sub-paths BEFORE parent paths ──────────────────────────
// (Express ordering: more-specific routes first)

// GET /strategies/:id/config/preview-prompt
router.get('/strategies/:id/config/preview-prompt', async (req: Request, res: Response) => {
  try {
    const [strategyResult, aiConfigResult, riskConfigResult] = await Promise.all([
      getPool().query(`SELECT id, symbol FROM strategies WHERE id = $1`, [req.params.id]),
      getPool().query(`SELECT * FROM ai_strategy_config WHERE strategy_id = $1`, [req.params.id]),
      getPool().query(`SELECT * FROM ai_risk_config WHERE strategy_id = $1`, [req.params.id]),
    ]);

    if ((strategyResult.rowCount ?? 0) === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    if ((aiConfigResult.rowCount ?? 0) === 0) {
      return res.status(404).json({ error: 'No AI config for this strategy' });
    }

    const strategy   = strategyResult.rows[0];
    const aiConfig   = aiConfigResult.rows[0];
    const riskConfig = (riskConfigResult.rowCount ?? 0) > 0 ? riskConfigResult.rows[0] : null;
    const [baseAsset = 'BTC', quoteAsset = 'USDT'] = strategy.symbol.replace('/', '-').split('-');

    const mockState = {
      strategy_config: {
        base_asset:          baseAsset,
        quote_asset:         quoteAsset,
        template_id:         aiConfig.template_id,
        custom_instructions: aiConfig.custom_instructions ?? null,
        use_technical:       aiConfig.use_technical,
        use_fear_greed:      aiConfig.use_fear_greed,
        use_funding_rate:    aiConfig.use_funding_rate,
        use_open_interest:   aiConfig.use_open_interest,
        use_news:            aiConfig.use_news,
        use_btc_dominance:   aiConfig.use_btc_dominance,
        use_macro:           aiConfig.use_macro,
        use_geometry:        aiConfig.use_geometry,
        use_economic_calendar:   aiConfig.use_economic_calendar,
        use_mtf_structure:       aiConfig.use_mtf_structure,
        use_orderbook:           aiConfig.use_orderbook,
        use_volume_profile:      aiConfig.use_volume_profile,
        use_cvd:                 aiConfig.use_cvd,
        use_momentum_divergence: aiConfig.use_momentum_divergence,
        use_volatility_regime:   aiConfig.use_volatility_regime,
        use_funding_history:     aiConfig.use_funding_history,
        use_liquidations:        aiConfig.use_liquidations,
        use_limit_orders:        aiConfig.use_limit_orders,
        indicators:          aiConfig.indicators,
        lookback_days:       aiConfig.lookback_days,
      },
      risk_config: {},
      position_open:               false,
      position_side:               null,
      position_entry_price:        null,
      position_unrealized_pnl_pct: null,
      position_opened_at:          null,
      original_reasoning:          null,
      trigger_reason:              'preview',
      cycle_interval:              aiConfig.interval_no_position,
      ohlcv_data:                  null,
      technical_indicators:        null,
      sentiment_data:              null,
      news_data:                   null,
      market_context:              null,
      data_fetch_errors:           [],
    };

    const aiResponse = await fetch(`${AI_URL}/internal/preview-prompt`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ strategy_id: req.params.id, mock_state: mockState }),
    });

    if (!aiResponse.ok) {
      const errText = await aiResponse.text();
      throw new Error(`AI service error ${aiResponse.status}: ${errText}`);
    }

    res.json(await aiResponse.json());
  } catch (e: any) {
    console.error(`Error building preview prompt for ${req.params.id}:`, e);
    res.status(500).json({ error: e.message });
  }
});

// POST /strategies/:id/config/enable-live
router.post('/strategies/:id/config/enable-live', async (req: Request, res: Response) => {
  const { confirm } = req.body;
  if (confirm !== 'ENABLE_LIVE_TRADING') {
    return res.status(400).json({
      error: 'Confirmation required. Send { "confirm": "ENABLE_LIVE_TRADING" }',
    });
  }
  try {
    const { rowCount } = await getPool().query(
      `UPDATE ai_strategy_config SET dry_run = false, updated_at = NOW()
       WHERE strategy_id = $1`,
      [req.params.id]
    );
    if (!rowCount) {
      return res.status(404).json({ error: `No AI config for strategy: ${req.params.id}` });
    }
    notifyConfigReload(req.params.id);
    const { rows } = await getPool().query(
      `SELECT a.*, t.name AS template_name, t.description AS template_description
       FROM ai_strategy_config a
       LEFT JOIN ai_prompt_templates t ON t.id = a.template_id
       WHERE a.strategy_id = $1`,
      [req.params.id]
    );
    res.json(formatConfig(rows[0]));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /strategies/:id/config/enable-dry
router.post('/strategies/:id/config/enable-dry', async (req: Request, res: Response) => {
  try {
    const { rowCount } = await getPool().query(
      `UPDATE ai_strategy_config SET dry_run = true, updated_at = NOW()
       WHERE strategy_id = $1`,
      [req.params.id]
    );
    if (!rowCount) {
      return res.status(404).json({ error: `No AI config for strategy: ${req.params.id}` });
    }
    notifyConfigReload(req.params.id);
    const { rows } = await getPool().query(
      `SELECT a.*, t.name AS template_name, t.description AS template_description
       FROM ai_strategy_config a
       LEFT JOIN ai_prompt_templates t ON t.id = a.template_id
       WHERE a.strategy_id = $1`,
      [req.params.id]
    );
    res.json(formatConfig(rows[0]));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /strategies/:id/signals/stats (before /strategies/:id/signals)
router.get('/strategies/:id/signals/stats', async (req: Request, res: Response) => {
  try {
    const { rows } = await getPool().query(
      `SELECT
         COUNT(*) AS total_signals,
         COUNT(*) FILTER (WHERE gate_passed = true)   AS signals_passed,
         COUNT(*) FILTER (WHERE webhook_fired = true) AS webhooks_fired,
         COUNT(*) FILTER (WHERE dry_run = true)       AS dry_run_signals,
         AVG(confidence) FILTER (WHERE confidence IS NOT NULL) AS avg_confidence,
         COUNT(*) FILTER (WHERE confidence >= 0.85)  AS high_confidence,
         COUNT(*) FILTER (WHERE confidence >= 0.75 AND confidence < 0.85) AS med_confidence,
         COUNT(*) FILTER (WHERE confidence >= 0.65 AND confidence < 0.75) AS low_confidence,
         COUNT(*) FILTER (WHERE confidence < 0.65 OR confidence IS NULL)  AS below_threshold,
         COUNT(*) FILTER (WHERE proposed_action = 'open_long')    AS open_long_count,
         COUNT(*) FILTER (WHERE proposed_action = 'open_short')   AS open_short_count,
         COUNT(*) FILTER (WHERE proposed_action = 'hold')         AS hold_count,
         COUNT(*) FILTER (WHERE proposed_action = 'close_long')   AS close_long_count,
         COUNT(*) FILTER (WHERE proposed_action = 'close_short')  AS close_short_count,
         COUNT(*) FILTER (WHERE gate_rejection_reason = 'llm_failed') AS llm_failures,
         COUNT(*) FILTER (WHERE gate_rejection_reason = 'confidence_below_threshold') AS low_confidence_rejections,
         COUNT(*) FILTER (WHERE gate_rejection_reason = 'cooldown_active') AS cooldown_rejections,
         COALESCE(SUM(input_tokens), 0)  AS input_tokens,
         COALESCE(SUM(output_tokens), 0) AS output_tokens,
         COALESCE(SUM(total_tokens), 0)  AS total_tokens
       FROM ai_signal_log
       WHERE strategy_id = $1`,
      [req.params.id]
    );
    const r = rows[0];
    res.json({
      total_signals:             Number(r.total_signals),
      signals_passed:            Number(r.signals_passed),
      webhooks_fired:            Number(r.webhooks_fired),
      dry_run_signals:           Number(r.dry_run_signals),
      avg_confidence:            r.avg_confidence != null ? Number(r.avg_confidence) : null,
      high_confidence:           Number(r.high_confidence),
      med_confidence:            Number(r.med_confidence),
      low_confidence:            Number(r.low_confidence),
      below_threshold:           Number(r.below_threshold),
      open_long_count:           Number(r.open_long_count),
      open_short_count:          Number(r.open_short_count),
      hold_count:                Number(r.hold_count),
      close_long_count:          Number(r.close_long_count),
      close_short_count:         Number(r.close_short_count),
      llm_failures:              Number(r.llm_failures),
      low_confidence_rejections: Number(r.low_confidence_rejections),
      cooldown_rejections:       Number(r.cooldown_rejections),
      input_tokens:              Number(r.input_tokens),
      output_tokens:             Number(r.output_tokens),
      total_tokens:              Number(r.total_tokens),
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /strategies/:id/trigger — manual cycle, always dry_run forced
router.post('/strategies/:id/trigger', async (req: Request, res: Response) => {
  try {
    const aiResponse = await fetch(`${AI_URL}/internal/trigger`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ strategy_id: req.params.id, trigger_reason: 'manual_dashboard' }),
    });
    const data = await aiResponse.json() as any;
    if (!aiResponse.ok) {
      return res.status(aiResponse.status).json(data);
    }
    res.json({ ...data, dry_run: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── GET /strategies/:id/config ────────────────────────────────────────────────

router.get('/strategies/:id/config', async (req: Request, res: Response) => {
  try {
    const { rows, rowCount } = await getPool().query(
      `SELECT a.*, t.name AS template_name, t.description AS template_description
       FROM ai_strategy_config a
       LEFT JOIN ai_prompt_templates t ON t.id = a.template_id
       WHERE a.strategy_id = $1`,
      [req.params.id]
    );
    if (!rowCount) {
      return res.status(404).json({ error: `No AI config for strategy: ${req.params.id}` });
    }
    res.json(formatConfig(rows[0]));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── PUT /strategies/:id/config ────────────────────────────────────────────────

router.put('/strategies/:id/config', async (req: Request, res: Response) => {
  const strategyId = req.params.id;
  const body = req.body;

  // Validation
  for (const field of ['interval_no_position', 'interval_position_open', 'interval_at_risk']) {
    if (body[field] !== undefined && !INTERVAL_PATTERN.test(String(body[field]))) {
      return res.status(400).json({ error: `${field} must match pattern /^[0-9]+(m|h|d)$/ (e.g. '4h', '15m', '1d')` });
    }
  }
  if (body.confidence_threshold !== undefined) {
    const v = Number(body.confidence_threshold);
    if (isNaN(v) || v < 0.5 || v > 0.95) {
      return res.status(400).json({ error: 'confidence_threshold must be between 0.5 and 0.95' });
    }
  }
  if (body.cooldown_entry_minutes !== undefined) {
    const v = Number(body.cooldown_entry_minutes);
    if (isNaN(v) || v < 0) {
      return res.status(400).json({ error: 'cooldown_entry_minutes must be >= 0' });
    }
  }
  if (body.lookback_days !== undefined) {
    const v = Number(body.lookback_days);
    if (isNaN(v) || v < 7 || v > 365) {
      return res.status(400).json({ error: 'lookback_days must be between 7 and 365' });
    }
  }
  if (body.llm_provider !== undefined && !VALID_PROVIDERS.includes(body.llm_provider)) {
    return res.status(400).json({ error: `llm_provider must be one of: ${VALID_PROVIDERS.join(', ')}` });
  }

  const updates: Array<[string, any]> = [];
  for (const field of ALLOWED_CONFIG_FIELDS) {
    if (body[field] !== undefined) updates.push([field, body[field]]);
  }

  try {
    const { rowCount } = await getPool().query(
      'SELECT 1 FROM ai_strategy_config WHERE strategy_id = $1',
      [strategyId]
    );

    if ((rowCount ?? 0) === 0) {
      const cols = ['strategy_id', ...updates.map(([f]) => f)];
      const vals: any[] = [strategyId, ...updates.map(([, v]) => v)];
      const placeholders = vals.map((_, i) => `$${i + 1}`).join(', ');
      await getPool().query(
        `INSERT INTO ai_strategy_config (${cols.join(', ')}, updated_at)
         VALUES (${placeholders}, NOW())`,
        vals
      );
    } else if (updates.length > 0) {
      const setClauses = updates.map(([f], i) => `${f} = $${i + 2}`).join(', ');
      await getPool().query(
        `UPDATE ai_strategy_config SET ${setClauses}, updated_at = NOW()
         WHERE strategy_id = $1`,
        [strategyId, ...updates.map(([, v]) => v)]
      );
    }

    const { rows } = await getPool().query(
      `SELECT a.*, t.name AS template_name, t.description AS template_description
       FROM ai_strategy_config a
       LEFT JOIN ai_prompt_templates t ON t.id = a.template_id
       WHERE a.strategy_id = $1`,
      [strategyId]
    );
    notifyConfigReload(strategyId);
    res.json(formatConfig(rows[0]));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── GET /strategies/:id/risk-config ──────────────────────────────────────────

router.get('/strategies/:id/risk-config', async (req: Request, res: Response) => {
  try {
    const { rows, rowCount } = await getPool().query(
      'SELECT * FROM ai_risk_config WHERE strategy_id = $1',
      [req.params.id]
    );
    if (!rowCount) {
      return res.json({
        strategy_id:           req.params.id,
        max_concurrent_trades: 1,
        updated_at:            null,
        updated_by:            null,
      });
    }
    res.json(formatRiskConfig(rows[0]));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── PUT /strategies/:id/risk-config ──────────────────────────────────────────

router.put('/strategies/:id/risk-config', async (req: Request, res: Response) => {
  const strategyId = req.params.id;
  const body = req.body;
  const changedBy = (req.ip ?? 'dashboard').replace('::ffff:', '');

  // Floor enforcement
  if (body.max_concurrent_trades !== undefined) {
    const v = Number(body.max_concurrent_trades);
    if (isNaN(v) || v < 1 || v > 5) {
      return res.status(400).json({ error: 'max_concurrent_trades must be >= 1 and <= 5' });
    }
  }

  const updates: Array<[string, number]> = [];
  for (const field of RISK_FIELDS) {
    if (body[field] !== undefined) updates.push([field, Number(body[field])]);
  }
  if (updates.length === 0) {
    return res.status(400).json({ error: 'No valid fields provided' });
  }

  try {
    const pool = getPool();
    const existing = await pool.query(
      'SELECT * FROM ai_risk_config WHERE strategy_id = $1',
      [strategyId]
    );
    const currentRow = (existing.rowCount ?? 0) > 0 ? existing.rows[0] : null;

    // Baseline values for diff comparison
    const baseline: Record<string, number> = currentRow ? {
      max_concurrent_trades: Number(currentRow.max_concurrent_trades),
    } : { ...RISK_DEFAULTS };

    if (!currentRow) {
      const cols = ['strategy_id', ...updates.map(([f]) => f)];
      const vals: any[] = [strategyId, ...updates.map(([, v]) => v)];
      const placeholders = vals.map((_, i) => `$${i + 1}`).join(', ');
      await pool.query(
        `INSERT INTO ai_risk_config (${cols.join(', ')}, updated_at, updated_by)
         VALUES (${placeholders}, NOW(), $${vals.length + 1})`,
        [...vals, changedBy]
      );
    } else {
      const setClauses = updates.map(([f], i) => `${f} = $${i + 2}`).join(', ');
      await pool.query(
        `UPDATE ai_risk_config SET ${setClauses}, updated_at = NOW(), updated_by = $${updates.length + 2}
         WHERE strategy_id = $1`,
        [strategyId, ...updates.map(([, v]) => v), changedBy]
      );
    }

    // Audit: one row per changed field
    for (const [field, newVal] of updates) {
      const oldNum = baseline[field];
      if (oldNum !== newVal) {
        await pool.query(
          `INSERT INTO ai_risk_config_audit (strategy_id, changed_by, field_name, old_value, new_value)
           VALUES ($1, $2, $3, $4, $5)`,
          [strategyId, changedBy, field, String(oldNum), String(newVal)]
        );
      }
    }

    const { rows } = await pool.query(
      'SELECT * FROM ai_risk_config WHERE strategy_id = $1',
      [strategyId]
    );
    res.json(formatRiskConfig(rows[0]));
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── GET /signals — global AI signal log across all strategies ─────────────────

router.get('/signals', async (req: Request, res: Response) => {
  const limit  = Math.min(Number(req.query.limit)  || 50, 200);
  const offset = Math.max(Number(req.query.offset) || 0,  0);
  const strategyId = req.query.strategy_id as string | undefined;
  const action     = req.query.action      as string | undefined;
  const gatePassed = req.query.gate_passed as string | undefined;
  const webhookFired = req.query.webhook_fired as string | undefined;

  const conditions: string[] = [];
  const params: any[] = [];

  if (strategyId) {
    params.push(strategyId);
    conditions.push(`strategy_id = $${params.length}`);
  }
  if (action) {
    params.push(action);
    conditions.push(`proposed_action = $${params.length}`);
  }
  if (gatePassed !== undefined) {
    params.push(gatePassed === 'true');
    conditions.push(`gate_passed = $${params.length}`);
  }
  if (webhookFired !== undefined) {
    params.push(webhookFired === 'true');
    conditions.push(`webhook_fired = $${params.length}`);
  }

  const where = conditions.length > 0 ? `WHERE ${conditions.join(' AND ')}` : '';

  try {
    const [dataResult, countResult] = await Promise.all([
      getPool().query(
        `SELECT * FROM ai_signal_log ${where}
         ORDER BY triggered_at DESC
         LIMIT $${params.length + 1} OFFSET $${params.length + 2}`,
        [...params, limit, offset]
      ),
      getPool().query(
        `SELECT COUNT(*) FROM ai_signal_log ${where}`,
        params
      ),
    ]);

    res.json({
      signals: dataResult.rows.map(formatSignal),
      total:   Number(countResult.rows[0].count),
      limit,
      offset,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// ── GET /strategies/:id/signals ───────────────────────────────────────────────

router.get('/strategies/:id/signals', async (req: Request, res: Response) => {
  const strategyId = req.params.id;
  const limit  = Math.min(Number(req.query.limit)  || 20, 100);
  const offset = Math.max(Number(req.query.offset) || 0,  0);
  const action     = req.query.action     as string | undefined;
  const gatePassed = req.query.gate_passed as string | undefined;

  const conditions: string[] = ['strategy_id = $1'];
  const params: any[] = [strategyId];

  if (action) {
    params.push(action);
    conditions.push(`proposed_action = $${params.length}`);
  }
  if (gatePassed !== undefined) {
    params.push(gatePassed === 'true');
    conditions.push(`gate_passed = $${params.length}`);
  }

  const where = conditions.join(' AND ');

  try {
    const [dataResult, countResult] = await Promise.all([
      getPool().query(
        `SELECT * FROM ai_signal_log WHERE ${where}
         ORDER BY triggered_at DESC
         LIMIT $${params.length + 1} OFFSET $${params.length + 2}`,
        [...params, limit, offset]
      ),
      getPool().query(
        `SELECT COUNT(*) FROM ai_signal_log WHERE ${where}`,
        params
      ),
    ]);

    res.json({
      signals: dataResult.rows.map(formatSignal),
      total:   Number(countResult.rows[0].count),
      limit,
      offset,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

export default router;
