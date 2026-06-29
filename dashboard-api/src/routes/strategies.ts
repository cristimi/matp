import { Router, Request, Response } from 'express';
import crypto from 'crypto';
import { getPool } from '../db';

const router = Router();

const EXECUTOR_URL  = process.env.EXECUTOR_URL || 'http://order-executor:8004';
const AI_URL        = process.env.AI_SIGNAL_GENERATOR_URL || 'http://ai-signal-generator:8005';
const LISTENER_URL  = process.env.ORDER_LISTENER_URL || 'http://order-listener:8001';

// Fire-and-forget: tell ai-signal-generator to reconcile this strategy's scheduler
// against its current DB state (start / reload / teardown). No-op for non-AI strategies.
function notifyReconcile(strategyId: string): void {
  fetch(`${AI_URL}/internal/schedulers/${strategyId}/reconcile`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  }).catch((e) => console.warn(`Could not notify scheduler reconcile for ${strategyId}:`, e));
}

// ── Exchange symbol validation (routed through order-executor) ─────────────
// Cache: { account_id -> { symbols: Set<string>, fetchedAt: number } }
const _symbolCache = new Map<string, { symbols: Set<string>; fetchedAt: number }>();
const SYMBOL_CACHE_TTL_MS = 60 * 60 * 1000; // 1 hour

async function validateSymbolForAccount(
  accountId: string,
  symbol: string,
): Promise<{ valid: boolean; error?: string }> {
  try {
    const now = Date.now();
    const cached = _symbolCache.get(accountId);

    let symbols: Set<string>;
    if (cached && now - cached.fetchedAt < SYMBOL_CACHE_TTL_MS) {
      symbols = cached.symbols;
    } else {
      const resp = await fetch(`${EXECUTOR_URL}/accounts/${accountId}/instruments`);
      if (!resp.ok) return { valid: true }; // executor unreachable — fail open
      const data = await resp.json() as { instruments: string[] };
      symbols = new Set((data.instruments ?? []).map((s: string) => s.toUpperCase()));
      _symbolCache.set(accountId, { symbols, fetchedAt: now });
    }

    if (symbols.size === 0) return { valid: true }; // empty list means unknown exchange — skip

    const upper = symbol.toUpperCase();
    if (!symbols.has(upper)) {
      const base = upper.split('-')[0];
      const suggestions = [...symbols].filter(s => s.startsWith(base + '-')).slice(0, 5);
      const hint = suggestions.length > 0 ? ` Available variants: ${suggestions.join(', ')}.` : '';
      // Fetch exchange/mode for the error message
      const acctRow = await getPool().query(
        `SELECT exchange, mode FROM exchange_accounts WHERE id = $1`, [accountId]
      );
      const label = acctRow.rowCount
        ? `${acctRow.rows[0].exchange} (${acctRow.rows[0].mode})`
        : accountId;
      return { valid: false, error: `Symbol '${symbol}' does not exist on ${label}.${hint}` };
    }
    return { valid: true };
  } catch (err: any) {
    console.warn(`Symbol validation skipped (executor unreachable): ${err.message}`);
    return { valid: true };
  }
}

const PERIOD_FILTER: Record<string, string> = {
  today: "INTERVAL '1 day'",
  '7d':  "INTERVAL '7 days'",
  '30d': "INTERVAL '30 days'",
  all:   "INTERVAL '100 years'",
};

router.get('/', async (_req: Request, res: Response) => {
  try {
    const query = `
      SELECT
        s.*,
        ea.exchange        AS account_exchange,
        ea.mode            AS account_mode,
        ea.label           AS account_label,
        COALESCE((
          SELECT COUNT(*)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'open'
        ), 0)::int         AS open_positions_count,
        COALESCE((
          SELECT COUNT(*)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'closed'
        ), 0)::int         AS closed_positions_count,
        COALESCE((
          SELECT SUM(sp.pnl_realized)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'closed'
        ), 0)::numeric     AS realized_pnl,
        COALESCE((
          SELECT COUNT(*)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'closed'
          AND sp.side = 'long'
        ), 0)::int         AS closed_long_count,
        COALESCE((
          SELECT COUNT(*)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id
          AND sp.status = 'closed'
          AND sp.side = 'short'
        ), 0)::int         AS closed_short_count,
        CASE
          WHEN COALESCE((
            SELECT COUNT(*) FROM strategy_positions sp
            WHERE sp.strategy_id = s.id AND sp.status = 'closed'
          ), 0) = 0 THEN 0::float
          ELSE ROUND(
            COALESCE((
              SELECT COUNT(*) FROM strategy_positions sp
              WHERE sp.strategy_id = s.id
              AND sp.status = 'closed'
              AND sp.pnl_realized > 0
            ), 0)::numeric * 100.0 /
            COALESCE((
              SELECT COUNT(*) FROM strategy_positions sp
              WHERE sp.strategy_id = s.id AND sp.status = 'closed'
            ), 1)::numeric,
          1)::float
        END                AS win_rate,
        CASE
          WHEN COALESCE(s.initial_allocation, s.capital_allocation, 0) = 0 THEN 0::float
          ELSE ROUND(
            COALESCE((
              SELECT SUM(sp.pnl_realized)
              FROM strategy_positions sp
              WHERE sp.strategy_id = s.id AND sp.status = 'closed'
            ), 0)::numeric /
            NULLIF(COALESCE(s.initial_allocation, s.capital_allocation), 0)::numeric * 100,
          2)::float
        END                AS total_return,
        aic.dry_run                AS ai_dry_run,
        aic.llm_model              AS ai_llm_model,
        aic.llm_provider           AS ai_llm_provider,
        aic.interval_no_position   AS ai_interval_no_position,
        aic.interval_position_open AS ai_interval_position_open,
        aic.interval_at_risk       AS ai_interval_at_risk,
        aic.at_risk_threshold_pct::float AS ai_at_risk_threshold_pct,
        (
          SELECT triggered_at FROM ai_signal_log
          WHERE strategy_id = s.id
          ORDER BY triggered_at DESC LIMIT 1
        ) AS ai_last_cycle_at
      FROM strategies s
      LEFT JOIN exchange_accounts ea ON ea.id = s.account_id
      LEFT JOIN ai_strategy_config aic ON aic.strategy_id = s.id
      WHERE COALESCE(s.is_deleted, false) = false
      ORDER BY s.created_at DESC
    `;
    const { rows } = await getPool().query(query);
    res.json(rows);
  } catch (err) {
    console.error('Error fetching strategies:', err);
    res.status(500).json({ error: 'Database error fetching strategies' });
  }
});

// Helper: fetch available balance for an account from executor (returns Infinity on failure)
async function getAccountAvailableBalance(accountId: string): Promise<number> {
  try {
    const resp = await fetch(`${EXECUTOR_URL}/accounts/${accountId}/balance`);
    if (!resp.ok) return Infinity;
    const data = await resp.json() as any;
    const bal = Number(data.available_balance ?? data.total_balance ?? 0);
    return bal > 0 ? bal : Infinity;
  } catch {
    return Infinity; // executor unreachable — fail open
  }
}

// Helper: total capital_allocation already committed on this account (excluding one strategy id)
async function getAllocatedOnAccount(accountId: string, excludeId: string | null): Promise<number> {
  const result = await getPool().query(
    `SELECT COALESCE(SUM(COALESCE(initial_allocation, capital_allocation)), 0) AS total
     FROM strategies
     WHERE account_id = $1
       AND enabled = true
       AND COALESCE(is_deleted, false) = false
       ${excludeId ? 'AND id != $2' : ''}`,
    excludeId ? [accountId, excludeId] : [accountId],
  );
  return Number(result.rows[0].total);
}

// POST /strategies — create a new strategy
router.post('/', async (req: Request, res: Response) => {
  const {
    name,
    symbol,
    account_id,
    interval                   = '1h',
    description                = '',
    default_leverage           = 1,
    margin_mode                = 'isolated',
    max_leverage               = 10,
    capital_allocation         = 100,
    margin_per_trade           = 5,
    max_drawdown_pct           = 50,
    allow_quote_variants       = false,
    allow_cross_charting       = false,
    strategy_source            = 'tradingview',
  } = req.body;

  if (!name || !symbol || !account_id) {
    return res.status(400).json({
      error: 'Missing required fields',
      required: ['name', 'symbol', 'account_id'],
    });
  }

  // Validate account exists and is active
  try {
    const acct = await getPool().query(
      `SELECT id FROM exchange_accounts WHERE id = $1 AND is_active = true`,
      [account_id]
    );
    if (acct.rowCount === 0) {
      return res.status(400).json({
        error: `Account not found or inactive: ${account_id}`,
      });
    }
  } catch (e: any) {
    return res.status(500).json({ error: e.message });
  }

  // Fund cap check: sum of all active strategies' allocations + new allocation must not exceed account balance
  try {
    const newAlloc       = Number(capital_allocation);
    const alreadyAlloc   = await getAllocatedOnAccount(account_id, null);
    const availableFunds = await getAccountAvailableBalance(account_id);
    if (availableFunds < Infinity && alreadyAlloc + newAlloc > availableFunds) {
      return res.status(422).json({
        error: `Insufficient free funds on account: $${newAlloc} requested, ` +
               `$${(availableFunds - alreadyAlloc).toFixed(2)} available ` +
               `($${alreadyAlloc.toFixed(2)} already allocated of $${availableFunds.toFixed(2)} total).`,
      });
    }
  } catch (e: any) {
    console.warn(`Fund cap check failed (non-fatal): ${e.message}`);
  }

  // Generate ID from name
  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')
    .slice(0, 30);
  const suffix       = crypto.randomBytes(2).toString('hex');
  const id           = `${slug}-${suffix}`;
  const webhookSecret = crypto.randomBytes(16).toString('hex');

  // Normalise symbol: accept "BTC/USDT" or "BTCUSDT", store as "BTC-USDT"
  const normalisedSymbol = symbol
    .toUpperCase()
    .replace('/', '-');

  // Validate symbol against exchange instrument list
  const symbolCheck = await validateSymbolForAccount(account_id, normalisedSymbol);
  if (!symbolCheck.valid) {
    return res.status(422).json({ error: symbolCheck.error });
  }

  try {
    await getPool().query(
      `INSERT INTO strategies (
        id, name, symbol, account_id, interval, description,
        class, config_yaml,
        webhook_secret,
        default_leverage, margin_mode,
        max_leverage,
        capital_allocation, initial_allocation, allocation_peak,
        margin_per_trade, max_drawdown_pct,
        allow_quote_variants, allow_cross_charting,
        strategy_source, enabled
      ) VALUES (
        $1, $2, $3, $4, $5, $6,
        'webhook', '',
        $7,
        $8, $9, $10,
        $11, $11, $11,
        $12, $13,
        $14, $15,
        $16, true
      )`,
      [
        id, name, normalisedSymbol, account_id, interval, description,
        webhookSecret,
        default_leverage, margin_mode,
        max_leverage,
        Number(capital_allocation), Number(margin_per_trade), Number(max_drawdown_pct),
        allow_quote_variants, allow_cross_charting,
        strategy_source,
      ]
    );

    // Return the created strategy with the webhook secret
    // (only returned here — not in GET endpoints)
    res.status(201).json({
      id,
      name,
      symbol:         normalisedSymbol,
      account_id,
      interval,
      enabled:        true,
      webhook_secret: webhookSecret,  // shown once on creation
      allow_quote_variants,
      allow_cross_charting,
      message: 'Strategy created. Save the webhook_secret — it will not be shown again.',
    });
  } catch (e: any) {
    if (e.code === '23505') {
      return res.status(409).json({ error: `Strategy ID conflict: ${id}` });
    }
    res.status(500).json({ error: e.message });
  }
});

router.get('/comparison', async (req: Request, res: Response) => {
  const period = (req.query.period as string) || '7d';
  const interval = PERIOD_FILTER[period] || PERIOD_FILTER['7d'];
  try {
    const query = `
      SELECT
        s.id   AS strategy_id,
        s.name,
        COALESCE(SUM(st.trades_count), 0)::int   AS trades_count,
        COALESCE(SUM(st.trades_won),   0)::int   AS trades_won,
        COALESCE(SUM(st.trades_lost),  0)::int   AS trades_lost,
        COALESCE(AVG(st.win_rate),     0)::float AS win_rate,
        COALESCE(SUM(st.pnl_total),    0)::float AS pnl_total,
        COALESCE(MAX(st.max_drawdown), 0)::float AS max_drawdown,
        (SELECT COUNT(*)::int FROM strategy_positions sp
         WHERE sp.strategy_id = s.id AND sp.status = 'open') AS open_positions,
        (SELECT ROUND(
           NULLIF(SUM(o.pnl) FILTER (WHERE o.pnl > 0), 0) /
           NULLIF(ABS(SUM(o.pnl) FILTER (WHERE o.pnl < 0)), 0),
         2)::float
         FROM orders o
         WHERE o.strategy_id = s.id
           AND o.received_at >= CURRENT_DATE - ${interval}
        ) AS profit_factor
      FROM strategies s
      LEFT JOIN strategy_stats st
        ON s.id = st.strategy_id AND st.period_date >= CURRENT_DATE - ${interval}
      GROUP BY s.id, s.name
    `;
    const { rows } = await getPool().query(query);
    res.json(rows);
  } catch (err) {
    console.error('Error fetching strategy comparison:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

// GET /strategies/tree — L1 tree list: strategies + open-position peek
// MUST stay before /:id to prevent Express capturing "tree" as an id.
router.get('/tree', async (_req: Request, res: Response) => {
  try {
    const { rows } = await getPool().query(`
      SELECT
        s.id,
        s.name,
        s.symbol,
        s.account_id,
        ea.label        AS account_label,
        ea.exchange     AS account_exchange,
        ea.mode         AS account_mode,
        s.enabled,
        s.capital_allocation,
        CASE
          WHEN COALESCE(s.initial_allocation, s.capital_allocation, 0) = 0 THEN 0::float
          ELSE ROUND(
            COALESCE((
              SELECT SUM(sp.pnl_realized)
              FROM strategy_positions sp
              WHERE sp.strategy_id = s.id AND sp.status = 'closed'
            ), 0)::numeric /
            NULLIF(COALESCE(s.initial_allocation, s.capital_allocation), 0)::numeric * 100,
          2)::float
        END AS total_return,
        COALESCE((
          SELECT COUNT(*)
          FROM strategy_positions sp
          WHERE sp.strategy_id = s.id AND sp.status = 'open'
        ), 0)::int AS open_positions_count
      FROM strategies s
      LEFT JOIN exchange_accounts ea ON ea.id = s.account_id
      WHERE COALESCE(s.is_deleted, false) = false
      ORDER BY s.created_at DESC
    `);

    // Fan out to executor once per unique account that has open positions — never once per strategy
    const openRows = rows.filter((r: any) => r.open_positions_count > 0 && r.account_id);
    const uniqueAccounts = [...new Set(openRows.map((r: any) => r.account_id as string))];

    const liveMap = new Map<string, number>(); // key: accountId:symbol:side → unrealized_pnl
    await Promise.all(uniqueAccounts.map(async (accountId) => {
      try {
        const resp = await fetch(`${EXECUTOR_URL}/accounts/${accountId}/positions`, {
          signal: AbortSignal.timeout(5000),
        });
        if (resp.ok) {
          const positions = await resp.json();
          if (Array.isArray(positions)) {
            positions.forEach((p: any) => {
              liveMap.set(`${accountId}:${p.symbol}:${p.side}`, Number(p.unrealized_pnl) || 0);
            });
          }
        }
      } catch (e) {
        console.error(`Tree: live fetch failed for account ${accountId}:`, e);
      }
    }));

    res.json(rows.map((r: any) => {
      let open_pnl = 0;
      if (r.open_positions_count > 0 && r.account_id) {
        const longPnl  = liveMap.get(`${r.account_id}:${r.symbol}:long`)  ?? 0;
        const shortPnl = liveMap.get(`${r.account_id}:${r.symbol}:short`) ?? 0;
        open_pnl = longPnl + shortPnl;
      }
      return {
        id:                   r.id,
        name:                 r.name,
        symbol:               r.symbol,
        account_label:        r.account_label,
        account_exchange:     r.account_exchange,
        account_mode:         r.account_mode,
        enabled:              r.enabled,
        stop_reason:          null,
        capital_allocation:   Number(r.capital_allocation),
        total_return:         Number(r.total_return),
        open_positions_count: r.open_positions_count,
        open_pnl,
      };
    }));
  } catch (err) {
    console.error('Error fetching strategy tree:', err);
    res.status(500).json({ error: 'Database error fetching strategy tree' });
  }
});

// GET /strategies/:id/webhook-info — returns webhook URL and secret
// Used by the edit page to display the TradingView configuration
router.get('/:id/webhook-info', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `SELECT id, name, symbol, webhook_secret
       FROM strategies WHERE id = $1`,
      [req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    const s = result.rows[0];

    // Determine host from request or env
    const host = process.env.PUBLIC_HOST
      || req.get('x-forwarded-host')
      || req.get('host')
      || 'localhost';
    const protocol = req.get('x-forwarded-proto') || 'http';
    const webhookUrl = `${protocol}://${host}/api/listener/webhook/${s.id}`;

    res.json({
      strategy_id:     s.id,
      strategy_name:   s.name,
      symbol:          s.symbol,
      webhook_url:    webhookUrl,
      webhook_secret: s.webhook_secret,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// GET /strategies/:id/ai-config/preview-prompt — assemble prompt preview via AI service
router.get('/:id/ai-config/preview-prompt', async (req: Request, res: Response) => {
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

    const strategy  = strategyResult.rows[0];
    const aiConfig  = aiConfigResult.rows[0];
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

    const aiResponse = await fetch('http://ai-signal-generator:8005/internal/preview-prompt', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ strategy_id: req.params.id, mock_state: mockState }),
    });

    if (!aiResponse.ok) {
      const errText = await aiResponse.text();
      throw new Error(`AI service error ${aiResponse.status}: ${errText}`);
    }

    const data = await aiResponse.json();
    res.json(data);
  } catch (e: any) {
    console.error(`Error building preview prompt for ${req.params.id}:`, e);
    res.status(500).json({ error: e.message });
  }
});

// GET /strategies/:id — single strategy with full config
router.get('/:id', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `SELECT
         s.*,
         ea.exchange  AS account_exchange,
         ea.mode      AS account_mode,
         ea.label     AS account_label,
         COALESCE((
           SELECT COUNT(*)
           FROM strategy_positions sp
           WHERE sp.strategy_id = s.id
           AND sp.status = 'open'
         ), 0)::int   AS open_positions_count
       FROM strategies s
       LEFT JOIN exchange_accounts ea ON ea.id = s.account_id
       WHERE s.id = $1`,
      [req.params.id]
    );
    if (result.rows.length === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    res.json(result.rows[0]);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// PUT /strategies/:id — update strategy fields including coupling flags
router.put('/:id', async (req: Request, res: Response) => {
  const {
    name,
    symbol,
    interval,
    default_leverage,
    margin_mode,
    allow_quote_variants,
    allow_cross_charting,
    max_leverage,
    allocation_delta,
    margin_per_trade,
    max_drawdown_pct,
    account_id,
  } = req.body;

  // Normalise symbol if provided
  const normalisedSymbol = symbol
    ? symbol.toUpperCase().replace('/', '-')
    : null;

  // Validate new account_id when it is being changed
  if (account_id !== undefined) {
    const acctCheck = await getPool().query(
      `SELECT id FROM exchange_accounts WHERE id = $1 AND is_active = true`,
      [account_id]
    );
    if ((acctCheck.rowCount ?? 0) === 0) {
      return res.status(422).json({ error: `Account not found or inactive: ${account_id}` });
    }
  }

  // Validate symbol against exchange instrument list (only when symbol is being changed)
  if (normalisedSymbol) {
    // Effective account: prefer the new one being set, fall back to existing
    const acctRow = await getPool().query(
      `SELECT account_id FROM strategies WHERE id = $1`,
      [req.params.id]
    );
    if (acctRow.rowCount !== 0) {
      const effectiveAccountId = account_id ?? acctRow.rows[0].account_id;
      const symbolCheck = await validateSymbolForAccount(effectiveAccountId, normalisedSymbol);
      if (!symbolCheck.valid) {
        return res.status(422).json({ error: symbolCheck.error });
      }
    }
  }

  // Allocation deposit/withdraw delta checks
  const allocationChanging = allocation_delta !== undefined && Number(allocation_delta) !== 0;
  if (allocationChanging) {
    try {
      const rowFetch = await getPool().query(
        `SELECT account_id, capital_allocation, margin_per_trade, initial_allocation
         FROM strategies WHERE id = $1`,
        [req.params.id]
      );
      if ((rowFetch.rowCount ?? 0) > 0) {
        const { account_id: acctId, capital_allocation: currentAlloc, margin_per_trade: mpt, initial_allocation: initAlloc } = rowFetch.rows[0];
        const delta          = Number(allocation_delta);
        const newAlloc       = Number(currentAlloc) + delta;
        const marginPerTrade = Number(mpt);

        if (newAlloc < marginPerTrade) {
          return res.status(422).json({
            error: `Withdrawal would drop allocation below margin_per_trade ($${marginPerTrade.toFixed(2)}).`,
          });
        }

        if (delta > 0) {
          const newCommitted   = Number(initAlloc) + delta;
          const alreadyAlloc   = await getAllocatedOnAccount(acctId, req.params.id);
          const availableFunds = await getAccountAvailableBalance(acctId);
          if (availableFunds < Infinity && alreadyAlloc + newCommitted > availableFunds) {
            return res.status(422).json({
              error: `Insufficient free funds on account: $${newCommitted.toFixed(2)} committed after deposit, ` +
                     `$${(availableFunds - alreadyAlloc).toFixed(2)} available ` +
                     `($${alreadyAlloc.toFixed(2)} already allocated of $${availableFunds.toFixed(2)} total).`,
            });
          }
        }
      }
    } catch (e: any) {
      console.warn(`Allocation delta check failed (non-fatal): ${e.message}`);
    }
  }

  try {
    const result = await getPool().query(
      `UPDATE strategies SET
         name                       = COALESCE($1, name),
         symbol                     = COALESCE($2, symbol),
         interval                   = COALESCE($3, interval),
         default_leverage           = COALESCE($4, default_leverage),
         margin_mode                = COALESCE($5, margin_mode),
         allow_quote_variants       = COALESCE($6, allow_quote_variants),
         allow_cross_charting       = COALESCE($7, allow_cross_charting),
         max_leverage               = COALESCE($8, max_leverage),
         capital_allocation         = capital_allocation + COALESCE($10, 0),
         initial_allocation         = initial_allocation + COALESCE($10, 0),
         allocation_peak            = allocation_peak    + COALESCE($10, 0),
         margin_per_trade           = COALESCE($11, margin_per_trade),
         max_drawdown_pct           = COALESCE($12, max_drawdown_pct),
         account_id                 = COALESCE($13, account_id),
         updated_at                 = NOW()
       WHERE id = $9
       RETURNING id, name, symbol, interval, enabled,
                 default_leverage, margin_mode,
                 allow_quote_variants, allow_cross_charting, account_id,
                 capital_allocation, initial_allocation, allocation_peak,
                 margin_per_trade, max_drawdown_pct, pnl_total`,
      [
        name ?? null,
        normalisedSymbol,
        interval ?? null,
        default_leverage ?? null,
        margin_mode ?? null,
        allow_quote_variants ?? null,
        allow_cross_charting ?? null,
        max_leverage ?? null,
        req.params.id,
        allocation_delta !== undefined ? Number(allocation_delta) : null,
        margin_per_trade !== undefined ? Number(margin_per_trade) : null,
        max_drawdown_pct !== undefined ? Number(max_drawdown_pct) : null,
        account_id ?? null,
      ]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    const row = result.rows[0];
    res.json({
      ...row,
      allocation_delta_applied: allocationChanging ? Number(allocation_delta) : 0,
    });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /strategies/:id/stop
// Proxies to order-listener: flattens all open legs then disables the strategy.
router.post('/:id/stop', async (req: Request, res: Response) => {
  try {
    const listenerRes = await fetch(`${LISTENER_URL}/strategies/${req.params.id}/stop`, {
      method: 'POST',
    });
    const data = await listenerRes.json() as any;
    if (!listenerRes.ok) {
      return res.status(listenerRes.status).json(data);
    }
    notifyReconcile(req.params.id);
    res.json(data);
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// POST /strategies/:id/start — re-enable a stopped strategy
router.post('/:id/start', async (req: Request, res: Response) => {
  try {
    const result = await getPool().query(
      `UPDATE strategies
       SET enabled = true,
           allocation_peak = CASE WHEN enabled = false THEN capital_allocation ELSE allocation_peak END,
           updated_at = NOW()
       WHERE id = $1
       RETURNING id, enabled`,
      [req.params.id]
    );
    if (result.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    notifyReconcile(req.params.id);
    res.json({ started: req.params.id, enabled: true });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

// DELETE /strategies/:id — soft delete (inactive + no open positions only)
router.delete('/:id', async (req: Request, res: Response) => {
  try {
    // Check strategy state
    const check = await getPool().query(
      `SELECT s.enabled,
              COALESCE((
                SELECT COUNT(*) FROM strategy_positions sp
                WHERE sp.strategy_id = s.id AND sp.status = 'open'
              ), 0)::int AS open_positions_count
       FROM strategies s WHERE s.id = $1`,
      [req.params.id]
    );
    if (check.rowCount === 0) {
      return res.status(404).json({ error: `Strategy not found: ${req.params.id}` });
    }
    const { enabled, open_positions_count } = check.rows[0];
    if (enabled) {
      return res.status(409).json({
        error: 'Cannot delete an active strategy. Stop it first.',
      });
    }
    if (open_positions_count > 0) {
      return res.status(409).json({
        error: `Cannot delete: strategy has ${open_positions_count} open position(s).`,
      });
    }
    await getPool().query(
      `UPDATE strategies
       SET is_deleted = true, updated_at = NOW()
       WHERE id = $1`,
      [req.params.id]
    );
    notifyReconcile(req.params.id);
    res.json({ deleted: req.params.id });
  } catch (e: any) {
    res.status(500).json({ error: e.message });
  }
});

router.get('/:id/performance', async (req: Request, res: Response) => {
  const { period = 'all_time', date } = req.query;
  const { id } = req.params;
  try {
    const query = `
      SELECT * FROM strategy_performance 
      WHERE strategy_id = $1 AND period_type = $2 
      ${date ? 'AND period_date = $3' : ''}
    `;
    const params = date ? [id, period, date] : [id, period];
    const { rows } = await getPool().query(query, params);
    if (rows.length === 0) return res.status(404).json({ message: "No performance data yet" });
    res.json(rows[0]);
  } catch (err) {
    console.error(`Error fetching performance for strategy ${id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.post('/:id/webhook-secret/rotate', async (req: Request, res: Response) => {
  try {
    const newSecret = require('crypto').randomBytes(32).toString('hex');
    await getPool().query('UPDATE strategies SET webhook_secret = $1 WHERE id = $2', [newSecret, req.params.id]);
    res.json({ message: 'Webhook secret rotated', strategy_id: req.params.id, new_secret_preview: newSecret.substring(0, 8) + '...' });
  } catch (err) {
    console.error('Error rotating webhook secret:', err);
    res.status(500).json({ error: 'Database error' });
  }
});

router.get('/:id/webhook-calls', async (req: Request, res: Response) => {
  const { limit = 50, status_filter } = req.query;
  try {
    let query = 'SELECT * FROM strategy_webhook_calls WHERE strategy_id = $1';
    const params: any[] = [req.params.id];
    if (status_filter) {
      query += ' AND http_status = $2';
      params.push(status_filter);
    }
    query += ' ORDER BY received_at DESC LIMIT $' + (params.length + 1);
    params.push(limit);
    const { rows } = await getPool().query(query, params);
    res.json(rows);
  } catch (err) {
    console.error(`Error fetching webhook calls for ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});



router.get('/:id/stats', async (req: Request, res: Response) => {
  const period = (req.query.period as string) || '7d';
  const interval = PERIOD_FILTER[period] || PERIOD_FILTER['7d'];
  try {
    const [statsResult, extraResult] = await Promise.all([
      getPool().query(`
        SELECT
          strategy_id,
          SUM(trades_count)::int                                  AS trades_count,
          SUM(trades_won)::int                                    AS trades_won,
          SUM(trades_lost)::int                                   AS trades_lost,
          AVG(win_rate)::float                                    AS win_rate,
          SUM(pnl_total)::float                                   AS pnl_total,
          AVG(pnl_total / NULLIF(trades_count, 0))::float         AS pnl_avg,
          MAX(max_drawdown)::float                                AS max_drawdown
        FROM strategy_stats
        WHERE strategy_id = $1 AND period_date >= CURRENT_DATE - ${interval}
        GROUP BY strategy_id
      `, [req.params.id]),
      getPool().query(`
        SELECT
          COUNT(*) FILTER (WHERE side = 'buy'  AND status = 'filled')::int AS long_count,
          COUNT(*) FILTER (WHERE side = 'sell' AND status = 'filled')::int AS short_count,
          ROUND(
            NULLIF(SUM(pnl) FILTER (WHERE pnl > 0), 0) /
            NULLIF(ABS(SUM(pnl) FILTER (WHERE pnl < 0)), 0),
          2)::float AS profit_factor,
          (SELECT COALESCE(SUM(pnl_unrealized), 0)
           FROM strategy_positions
           WHERE strategy_id = $1 AND status = 'open') AS unrealized_pnl
        FROM orders
        WHERE strategy_id = $1 AND received_at >= CURRENT_DATE - ${interval}
      `, [req.params.id]),
    ]);

    const base = statsResult.rows[0] ?? {
      strategy_id: req.params.id,
      trades_count: 0, trades_won: 0, trades_lost: 0,
      win_rate: 0, pnl_total: 0, pnl_avg: 0, max_drawdown: 0,
    };
    const extra = extraResult.rows[0] ?? {
      long_count: 0, short_count: 0, profit_factor: null, unrealized_pnl: 0,
    };
    res.json({ ...base, ...extra });
  } catch (err) {
    console.error(`Error fetching stats for strategy ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error' });
  }
});

// GET /strategies/:id/positions?scope=open|all — L2 position list (lazy)
// Account label is derived from the opening order (not the strategy's current account_id)
// to correctly label historical positions after a strategy account change.
// mark_price and unrealized_pnl for open positions are enriched from the live executor feed.
router.get('/:id/positions', async (req: Request, res: Response) => {
  try {
    const scope  = (req.query.scope  as string) || 'open';
    const limit  = Math.min(parseInt((req.query.limit  as string) || '50'), 200);
    const offset = Math.max(parseInt((req.query.offset as string) || '0'),   0);
    const scopeFilter = scope === 'open' ? "AND sp.status = 'open'" : '';

    const { rows } = await getPool().query(`
      SELECT
        sp.id,
        sp.side,
        sp.symbol,
        s.account_id AS strategy_account_id,
        COALESCE(b.symbol, SPLIT_PART(sp.symbol, '-', 1)) AS base_asset,
        COALESCE(q.symbol, SPLIT_PART(sp.symbol, '-', 2)) AS quote_asset,
        sp.size,
        sp.entry_price,
        sp.pnl_unrealized AS unrealized_pnl_db,
        sp.pnl_realized   AS realized_pnl,
        sp.liquidation_price,
        sp.leverage,
        sp.opened_at,
        sp.closed_at,
        sp.close_reason,
        sp.status,
        COALESCE(o_open.account_id, oel.account_id, s.account_id) AS account_id,
        COALESCE(ea_open.label,    ea_oel.label,    ea_s.label)    AS account_label,
        COALESCE(ea_open.exchange, ea_oel.exchange, ea_s.exchange) AS account_exchange,
        (
          SELECT COUNT(*) FROM orders o
          WHERE o.id = sp.opening_order_id
             OR o.closes_position_id = sp.id
        )::int AS order_count
      FROM strategy_positions sp
      JOIN strategies s ON s.id = sp.strategy_id
      LEFT JOIN trading_pairs tp ON tp.id = sp.pair_id
      LEFT JOIN assets b  ON b.id  = tp.base_asset_id
      LEFT JOIN assets q  ON q.id  = tp.quote_asset_id
      LEFT JOIN orders o_open ON o_open.id = sp.opening_order_id
      LEFT JOIN order_execution_log oel
        ON oel.exchange_order_id = o_open.exchange_order_id
        AND o_open.exchange_order_id IS NOT NULL
      LEFT JOIN exchange_accounts ea_open ON ea_open.id = o_open.account_id
      LEFT JOIN exchange_accounts ea_oel  ON ea_oel.id  = oel.account_id
      LEFT JOIN exchange_accounts ea_s    ON ea_s.id    = s.account_id
      WHERE sp.strategy_id = $1
        ${scopeFilter}
      ORDER BY sp.opened_at DESC
      LIMIT $2 OFFSET $3
    `, [req.params.id, limit, offset]);

    // Enrich open positions with live mark_price and unrealized_pnl from executor.
    // Use strategy_account_id (not the derived display account) as the lookup key.
    const liveMap = new Map<string, any>();
    const openRows = rows.filter((r: any) => r.status === 'open');
    if (openRows.length > 0 && openRows[0].strategy_account_id) {
      const accountId = openRows[0].strategy_account_id as string;
      try {
        const resp = await fetch(`${EXECUTOR_URL}/accounts/${accountId}/positions`, {
          signal: AbortSignal.timeout(5000),
        });
        if (resp.ok) {
          const livePositions = await resp.json();
          if (Array.isArray(livePositions)) {
            livePositions.forEach((p: any) => {
              liveMap.set(`${accountId}:${p.symbol}:${p.side}`, p);
            });
          }
        }
      } catch (e) {
        console.error(`Live enrichment failed for account ${accountId}:`, e);
      }
    }

    res.json(rows.map((r: any) => {
      const liveKey = `${r.strategy_account_id}:${r.symbol}:${r.side}`;
      const live    = r.status === 'open' ? liveMap.get(liveKey) : undefined;
      return {
        id:                r.id,
        side:              r.side,
        base_asset:        r.base_asset,
        quote_asset:       r.quote_asset,
        size:              Number(r.size),
        entry_price:       Number(r.entry_price),
        mark_price:        live ? Number(live.mark_price || live.entry_price) : Number(r.entry_price),
        unrealized_pnl:    live ? Number(live.unrealized_pnl) : null,
        realized_pnl:      r.realized_pnl != null ? Number(r.realized_pnl) : null,
        liquidation_price: r.liquidation_price != null ? Number(r.liquidation_price) : null,
        leverage:          r.leverage,
        opened_at:         r.opened_at,
        closed_at:         r.closed_at,
        close_reason:      r.close_reason,
        status:            r.status,
        account_label:     r.account_label,
        account_exchange:  r.account_exchange,
        order_count:       r.order_count,
      };
    }));
  } catch (err) {
    console.error(`Error fetching positions for strategy ${req.params.id}:`, err);
    res.status(500).json({ error: 'Database error fetching positions' });
  }
});

export default router;
