import React, { useEffect, useState } from 'react';
import { getStrategy, promoteToMaTP, ToMaTPResponse } from '../api';

interface PromoteSheetProps {
  strategyId: string;
  onClose:    () => void;
  onDone?:    () => void;
}

const inputStyle: React.CSSProperties = {
  fontFamily: 'JetBrains Mono, monospace', fontSize: '13px',
  padding: '7px 10px', border: '1px solid var(--border)',
  borderRadius: 'var(--pill-r)', background: 'var(--bg3)',
  color: 'var(--text)', width: '100%',
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
      <label style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--dim)' }}>
        {label}
      </label>
      {children}
    </div>
  );
}

export function PromoteSheet({ strategyId, onClose, onDone }: PromoteSheetProps) {
  const [stratName,  setStratName]  = useState('');
  const [symbol,     setSymbol]     = useState('');
  const [interval,   setInterval]   = useState('');
  const [accountId,  setAccountId]  = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error,      setError]      = useState<string | null>(null);
  const [result,     setResult]     = useState<ToMaTPResponse | null>(null);

  useEffect(() => {
    getStrategy(strategyId)
      .then(s => { setStratName(s.name); setSymbol(s.symbol); setInterval(s.interval); })
      .catch(() => {});
  }, [strategyId]);

  const canSubmit = accountId.trim().length > 0 && !submitting && !result;

  const handlePromote = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await promoteToMaTP(strategyId, { account_id: accountId.trim() });
      setResult(res);
    } catch (e) {
      setError(String(e));
      setSubmitting(false);
    }
  };

  const handleDone = () => { onDone?.(); onClose(); };

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.55)', display: 'flex', alignItems: 'flex-end', justifyContent: 'center', zIndex: 100 }}
      onClick={onClose}
    >
      <div
        style={{ width: '100%', maxWidth: '375px', background: 'var(--bg2)', borderRadius: 'var(--r) var(--r) 0 0', border: '1px solid var(--border)', maxHeight: '90vh', overflowY: 'auto' }}
        onClick={e => e.stopPropagation()}
      >
        {/* header */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 16px 10px', borderBottom: '1px solid var(--border)' }}>
          <div>
            <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text)' }}>Promote to MATP</div>
            {stratName && (
              <div style={{ fontSize: '11px', color: 'var(--dim)', marginTop: '2px' }}>
                {stratName} · {symbol} · {interval}
              </div>
            )}
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--dim)', fontSize: '18px', cursor: 'pointer', lineHeight: 1 }}>✕</button>
        </div>

        <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {result ? (
            <>
              <div style={{ background: 'var(--green-a)', border: '1px solid var(--green-b)', borderRadius: 'var(--pill-r)', padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <p style={{ fontSize: '13px', fontWeight: 700, color: 'var(--green)', margin: 0 }}>✓ Promotion complete</p>
                <p style={{ fontSize: '10px', color: 'var(--green)', margin: 0, lineHeight: 1.4 }}>A new DISABLED strategy was created in the live system.</p>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <span style={{ fontSize: '9px', fontWeight: 600, letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--dim)' }}>Public Strategy ID</span>
                <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: '12px', fontWeight: 600, color: 'var(--muted)', background: 'var(--bg3)', border: '1px solid var(--border)', borderRadius: 'var(--pill-r)', padding: '6px 10px', wordBreak: 'break-all' }}>
                  {result.public_strategy_id}
                </span>
              </div>
              <div style={{ background: 'var(--failed-color-a)', border: '1px solid var(--failed-color-b)', borderRadius: 'var(--pill-r)', padding: '8px 12px' }}>
                <p style={{ fontSize: '11px', fontWeight: 600, color: 'var(--failed-color)', margin: 0 }}>⚠ enabled: false</p>
                <p style={{ fontSize: '10px', color: 'var(--failed-color)', margin: '3px 0 0', lineHeight: 1.4 }}>Manually activate this strategy in the MATP dashboard after review. It will not run until you enable it.</p>
              </div>
              <button onClick={handleDone} style={{ padding: '11px', border: 'none', borderRadius: 'var(--pill-r)', background: 'var(--green)', color: '#fff', fontSize: '12px', fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', cursor: 'pointer' }}>
                Done
              </button>
            </>
          ) : (
            <>
              <div style={{ background: 'var(--failed-color-a)', border: '1px solid var(--failed-color-b)', borderRadius: 'var(--pill-r)', padding: '8px 12px' }}>
                <p style={{ fontSize: '11px', fontWeight: 600, color: 'var(--failed-color)', margin: 0 }}>⚠ Creates a DISABLED strategy in the live system</p>
                <p style={{ fontSize: '10px', color: 'var(--failed-color)', margin: '3px 0 0', lineHeight: 1.4 }}>
                  The promoted strategy starts with <code style={{ fontSize: '10px' }}>enabled = false</code> and{' '}
                  <code style={{ fontSize: '10px' }}>webhook_enabled = false</code>. You must manually activate it in the dashboard after reviewing the config.
                </p>
              </div>
              <Field label="Account ID">
                <input
                  type="text"
                  placeholder="e.g. acc_blofin_demo_default"
                  value={accountId}
                  onChange={e => setAccountId(e.target.value)}
                  style={inputStyle}
                  autoFocus
                />
              </Field>
              {error && <p style={{ color: 'var(--red)', fontSize: '11px', margin: 0, lineHeight: 1.4 }}>{error}</p>}
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', paddingTop: '4px' }}>
                <button onClick={onClose} style={{ padding: '11px', border: '1px solid var(--border)', borderRadius: 'var(--pill-r)', background: 'var(--bg3)', color: 'var(--muted)', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}>
                  Cancel
                </button>
                <button
                  onClick={handlePromote}
                  disabled={!canSubmit}
                  style={{ padding: '11px', border: 'none', borderRadius: 'var(--pill-r)', background: canSubmit ? 'var(--orange)' : 'var(--gray-a)', color: canSubmit ? '#fff' : 'var(--dim)', fontSize: '12px', fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', cursor: canSubmit ? 'pointer' : 'default', transition: 'background .15s' }}
                >
                  {submitting ? 'Promoting…' : '⇑ Promote'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
