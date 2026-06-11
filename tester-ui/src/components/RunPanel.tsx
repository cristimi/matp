import React, { useState, useEffect, useRef, useCallback } from 'react';
import { getStrategy, estimateCost, createRun, EstimateResponse } from '../api';

interface RunPanelProps {
  strategyId: string;
  onClose:    () => void;
  onStarted:  (runId?: string) => void;
}

const TIMEFRAMES = ['1m','5m','15m','30m','1h','2h','4h','8h','1d'];

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:'4px' }}>
      <label style={{ fontSize:'9px', fontWeight:600, letterSpacing:'.1em',
        textTransform:'uppercase', color:'var(--dim)' }}>
        {label}
      </label>
      {children}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  fontFamily:   'JetBrains Mono, monospace',
  fontSize:     '13px',
  padding:      '7px 10px',
  border:       '1px solid var(--border)',
  borderRadius: 'var(--pill-r)',
  background:   'var(--bg3)',
  color:        'var(--text)',
  width:        '100%',
};

export function RunPanel({ strategyId, onClose, onStarted }: RunPanelProps) {
  const [stratName,  setStratName]  = useState('');
  const [symbol,     setSymbol]     = useState('BTC-USDT');
  const [model,      setModel]      = useState('gemini-2.0-flash');
  const [provider,   setProvider]   = useState('google');

  // form state
  const [dateFrom,    setDateFrom]    = useState(daysAgo(90));
  const [dateTo,      setDateTo]      = useState(today());
  const [timeframe,   setTimeframe]   = useState('1h');
  const [balance,     setBalance]     = useState(1000);
  const [slippage,    setSlippage]    = useState(0.05);
  const [fee,         setFee]         = useState(0.02);
  const [lookback,    setLookback]    = useState(90);
  const [modelOver,   setModelOver]   = useState('');
  const [dryRun,      setDryRun]      = useState(false);   // dev-only toggle

  // cost estimate
  const [estimate,    setEstimate]    = useState<EstimateResponse | null>(null);
  const [estLoading,  setEstLoading]  = useState(false);
  const [estError,    setEstError]    = useState<string | null>(null);

  // submission
  const [submitting,  setSubmitting]  = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // load strategy defaults
  useEffect(() => {
    getStrategy(strategyId).then(s => {
      setStratName(s.name);
      setSymbol(s.symbol);
      setModel(s.llm_model ?? 'gemini-2.0-flash');
      setProvider(s.llm_provider ?? 'google');
      setTimeframe(s.interval ?? '1h');
    }).catch(() => {});
  }, [strategyId]);

  const fetchEstimate = useCallback(() => {
    if (!dateFrom || !dateTo || dateFrom >= dateTo) {
      setEstimate(null);
      setEstError(null);
      return;
    }
    setEstLoading(true);
    setEstError(null);
    estimateCost({
      strategy_id:   strategyId,
      date_from:     dateFrom,
      date_to:       dateTo,
      timeframe,
      lookback_days: lookback,
    })
      .then(r => { setEstimate(r); setEstLoading(false); })
      .catch(e => { setEstError(String(e)); setEstLoading(false); setEstimate(null); });
  }, [strategyId, dateFrom, dateTo, timeframe, lookback]);

  // debounce estimate on any field change
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(fetchEstimate, 600);
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [fetchEstimate]);

  const effectiveModel = modelOver.trim() || model;

  // zero-candle guard
  const zeroCandles = estimate != null && estimate.active_candles === 0;
  const canSubmit   = !submitting && !zeroCandles && estimate != null && !estLoading;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const res = await createRun({
        strategy_id:        strategyId,
        date_from:          dateFrom,
        date_to:            dateTo,
        timeframe,
        initial_balance:    balance,
        slippage_pct:       slippage,
        fee_pct:            fee,
        lookback_days:      lookback,
        dry_signal:         dryRun,
        llm_model_override: modelOver.trim() || undefined,
      });
      onStarted(res.run_id);
    } catch (e) {
      setSubmitError(String(e));
      setSubmitting(false);
    }
  };

  const costDisplay = () => {
    if (zeroCandles) return null;
    if (estLoading)  return <CostBand text="Calculating…" color="var(--dim)" />;
    if (estError)    return <CostBand text={`Estimate unavailable: ${estError}`} color="var(--failed-color)" />;
    if (!estimate)   return null;
    const low  = (estimate.estimated_cost_usd * 0.8).toFixed(4);
    const high = (estimate.estimated_cost_usd * 1.2).toFixed(4);
    const m    = effectiveModel.replace('gemini-', 'g-');
    return (
      <CostBand
        text={`Estimated cost: $${low} – $${high}  (${m}, ${estimate.active_candles} active candles)`}
        color="var(--blue)"
      />
    );
  };

  return (
    <div
      style={{
        position:'fixed', inset:0, background:'rgba(15,23,42,.55)',
        display:'flex', alignItems:'flex-end', justifyContent:'center', zIndex:100,
      }}
      onClick={onClose}
    >
      <div
        style={{
          width:'100%', maxWidth:'375px', background:'var(--bg2)',
          borderRadius:'var(--r) var(--r) 0 0',
          border:'1px solid var(--border)', maxHeight:'90vh', overflowY:'auto',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* header */}
        <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
          padding:'14px 16px 10px', borderBottom:'1px solid var(--border)' }}>
          <div>
            <div style={{ fontSize:'14px', fontWeight:700, color:'var(--text)' }}>
              Run Backtest
            </div>
            <div style={{ fontSize:'11px', color:'var(--dim)', marginTop:'2px' }}>
              {stratName} · {symbol}
            </div>
          </div>
          <button onClick={onClose} style={{ background:'none', border:'none',
            color:'var(--dim)', fontSize:'18px', cursor:'pointer', lineHeight:1 }}>
            ✕
          </button>
        </div>

        <div style={{ padding:'14px 16px', display:'flex', flexDirection:'column', gap:'12px' }}>

          {/* date range */}
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px' }}>
            <Field label="Date From">
              <input type="date" value={dateFrom} onChange={e => setDateFrom(e.target.value)} style={inputStyle} />
            </Field>
            <Field label="Date To">
              <input type="date" value={dateTo} onChange={e => setDateTo(e.target.value)} style={inputStyle} />
            </Field>
          </div>

          {/* timeframe + lookback */}
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px' }}>
            <Field label="Timeframe">
              <select value={timeframe} onChange={e => setTimeframe(e.target.value)} style={inputStyle}>
                {TIMEFRAMES.map(tf => <option key={tf} value={tf}>{tf}</option>)}
              </select>
            </Field>
            <Field label="Lookback (days)">
              <input type="number" min={1} max={365} value={lookback}
                onChange={e => setLookback(Number(e.target.value))} style={inputStyle} />
            </Field>
          </div>

          {/* balance + fees */}
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:'10px' }}>
            <Field label="Balance ($)">
              <input type="number" min={1} value={balance}
                onChange={e => setBalance(Number(e.target.value))} style={inputStyle} />
            </Field>
            <Field label="Slippage %">
              <input type="number" min={0} step={0.01} value={slippage}
                onChange={e => setSlippage(Number(e.target.value))} style={inputStyle} />
            </Field>
            <Field label="Fee %">
              <input type="number" min={0} step={0.01} value={fee}
                onChange={e => setFee(Number(e.target.value))} style={inputStyle} />
            </Field>
          </div>

          {/* model override */}
          <Field label={`Model (default: ${model})`}>
            <input type="text" placeholder={`${provider}/${model}`} value={modelOver}
              onChange={e => setModelOver(e.target.value)} style={inputStyle} />
          </Field>

          {/* cost estimate band */}
          {zeroCandles ? (
            <div style={{
              background:'var(--failed-color-a)', border:'1px solid var(--failed-color-b)',
              borderRadius:'var(--pill-r)', padding:'10px 12px',
            }}>
              <p style={{ fontSize:'11px', fontWeight:600, color:'var(--failed-color)', margin:0 }}>
                ⚠ Zero tradeable candles
              </p>
              <p style={{ fontSize:'10px', color:'var(--failed-color)', margin:'4px 0 0', lineHeight:1.4 }}>
                The lookback period ({lookback} days) consumes the entire date range.
                Widen the date range or reduce lookback. Start Run is disabled.
              </p>
            </div>
          ) : costDisplay()}

          {/* dev dry-run checkbox */}
          <label style={{ display:'flex', alignItems:'center', gap:'8px', cursor:'pointer' }}>
            <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} />
            <span style={{ fontSize:'10px', color:'var(--dim)', fontFamily:'JetBrains Mono,monospace',
              fontWeight:600, letterSpacing:'.06em', textTransform:'uppercase' }}>
              Dry run (no LLM — dev/test only)
            </span>
          </label>

          {submitError && (
            <p style={{ color:'var(--red)', fontSize:'11px', margin:0 }}>{submitError}</p>
          )}

          {/* action buttons */}
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'10px', paddingTop:'4px' }}>
            <button onClick={onClose} style={{
              padding:'11px', border:'1px solid var(--border)', borderRadius:'var(--pill-r)',
              background:'var(--bg3)', color:'var(--muted)', fontSize:'12px', fontWeight:600,
              cursor:'pointer',
            }}>
              Cancel
            </button>
            <button
              onClick={handleSubmit}
              disabled={!canSubmit}
              style={{
                padding:'11px', border:'none', borderRadius:'var(--pill-r)',
                background: canSubmit ? 'var(--green)' : 'var(--gray-a)',
                color: canSubmit ? '#fff' : 'var(--dim)',
                fontSize:'12px', fontWeight:700, letterSpacing:'.04em',
                textTransform:'uppercase', cursor: canSubmit ? 'pointer' : 'default',
                transition:'background .15s',
              }}
            >
              {submitting ? 'Starting…' : dryRun ? 'Start Dry Run' : 'Start Run'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function CostBand({ text, color }: { text: string; color: string }) {
  return (
    <div style={{
      background:'var(--blue-a)', border:'1px solid var(--blue-b)',
      borderRadius:'var(--pill-r)', padding:'8px 12px',
    }}>
      <p style={{ fontSize:'11px', fontWeight:500, color, margin:0, lineHeight:1.4 }}>
        {text}
      </p>
    </div>
  );
}
