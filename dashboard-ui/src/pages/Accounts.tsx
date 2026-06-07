import { useState, useEffect, useCallback } from 'react';

interface Account {
  id:         string;
  exchange:   string;
  mode:       string;
  label:      string;
  is_active:  boolean;
  created_at: string;
}

interface Balance {
  total_balance:     number;
  available_balance: number;
  used_margin:       number;
  currency:          string;
  error?:            string;
}

interface AccountMeta {
  api_key_preview?: string;
  wallet_address?:  string;
  error?:           string;
}

const API = '/api/dashboard';

export default function Accounts() {
  const [accounts, setAccounts]   = useState<Account[]>([]);
  const [balances, setBalances]   = useState<Record<string, Balance>>({});
  const [metas,    setMetas]      = useState<Record<string, AccountMeta>>({});
  const [loading,  setLoading]    = useState(true);
  const [showAdd,  setShowAdd]    = useState(false);
  const [credAccount, setCredAccount] = useState<Account | null>(null);
  const [credJson,    setCredJson]    = useState('');
  const [credStatus,  setCredStatus]  = useState<string | null>(null);
  const [addForm,  setAddForm]    = useState({
    id: '', exchange: 'blofin', mode: 'demo', label: ''
  });
  const [addError,   setAddError]   = useState<string | null>(null);
  const [addLoading, setAddLoading] = useState(false);

  const CRED_PLACEHOLDERS: Record<string, string> = {
    blofin:      '{"api_key": "", "api_secret": "", "api_passphrase": ""}',
    hyperliquid: '{"private_key": "0x..."}',
  };

  const fetchAccounts = useCallback(async () => {
    try {
      const res  = await fetch(`${API}/accounts`);
      const data = await res.json();
      const list = Array.isArray(data) ? data : [];
      setAccounts(list);

      // Fetch balance and meta for each active account in parallel
      list.filter((a: Account) => a.is_active).forEach(async (acc: Account) => {
        try {
          const [balRes, metaRes] = await Promise.all([
            fetch(`${API}/accounts/${acc.id}/balance`),
            fetch(`${API}/accounts/${acc.id}/meta`),
          ]);
          if (balRes.ok) {
            const bal = await balRes.json();
            setBalances(prev => ({ ...prev, [acc.id]: bal }));
          }
          if (metaRes.ok) {
            const meta = await metaRes.json();
            setMetas(prev => ({ ...prev, [acc.id]: meta }));
          }
        } catch {}
      });
    } catch {
      setAccounts([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchAccounts();
    const interval = setInterval(fetchAccounts, 60000);
    return () => clearInterval(interval);
  }, [fetchAccounts]);

  // Aggregate totals across all active accounts
  const totalBalance     = Object.values(balances).reduce((s, b) => s + (b.total_balance     || 0), 0);
  const totalAvailable   = Object.values(balances).reduce((s, b) => s + (b.available_balance || 0), 0);
  const activeCount      = accounts.filter(a => a.is_active).length;

  const handleAdd = async () => {
    setAddError(null);
    setAddLoading(true);
    try {
      const res  = await fetch(`${API}/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(addForm),
      });
      const data = await res.json();
      if (!res.ok) { setAddError(data.error || 'Failed'); return; }
      setShowAdd(false);
      setAddForm({ id: '', exchange: 'blofin', mode: 'demo', label: '' });
      fetchAccounts();
    } catch (e: any) { setAddError(e.message); }
    finally { setAddLoading(false); }
  };

  const handleUpdateCreds = async () => {
    if (!credAccount) return;
    setCredStatus('Saving...');
    try {
      const res = await fetch(`${API}/accounts/${credAccount.id}/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials_json: credJson }),
      });
      const data = await res.json();
      if (!res.ok) { setCredStatus(`Error: ${data.error}`); return; }
      setCredStatus('Credentials updated');
      // Invalidate meta cache for this account
      setMetas(prev => { const n = {...prev}; delete n[credAccount.id]; return n; });
      setTimeout(() => { setCredAccount(null); setCredJson(''); setCredStatus(null); }, 1500);
    } catch (e: any) { setCredStatus(`Error: ${e.message}`); }
  };

  const handleDelete = async (id: string, label: string) => {
    if (!confirm(`Delete account "${label}"? This cannot be undone.`)) return;
    const res = await fetch(`${API}/accounts/${id}`, { method: 'DELETE' });
    if (res.ok) {
      setAccounts(prev => prev.filter(a => a.id !== id));
      setBalances(prev => { const n = {...prev}; delete n[id]; return n; });
      setMetas(prev =>    { const n = {...prev}; delete n[id]; return n; });
    }
  };

  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>

      {/* Top bar */}
      <div style={{
        display:'flex', alignItems:'center', justifyContent:'space-between',
        padding:'18px 20px 12px', background:'var(--bg2)',
        borderBottom:'1px solid var(--border)', flexShrink:0,
      }}>
        <span style={{ fontSize:'23px', fontWeight:800,
                        letterSpacing:'-.02em', color:'var(--text)' }}>
          Accounts
        </span>
        <div style={{ display:'flex', gap:'6px', alignItems:'center' }}>
          <span style={{
            background:'var(--bg3)', border:'1px solid var(--border)',
            borderRadius:'20px', padding:'4px 11px',
            fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
            color:'var(--muted)',
          }}>
            {activeCount} Active
          </span>
          <button
            onClick={() => setShowAdd(true)}
            style={{
              background:'var(--bg3)', border:'1px solid var(--border)',
              borderRadius:'20px', padding:'5px 12px',
              fontFamily:'JetBrains Mono, monospace', fontSize:'11px',
              color:'var(--muted)', cursor:'pointer',
            }}>
            ＋
          </button>
        </div>
      </div>

      {/* Summary bar */}
      <div style={{
        display:'flex', background:'var(--bg2)',
        borderBottom:'1px solid var(--border)', flexShrink:0,
      }}>
        {[
          { label:'Total Balance',   value: `${totalBalance.toFixed(2)} USDT`,   color:'var(--text)' },
          { label:'Available',       value: `${totalAvailable.toFixed(2)} USDT`, color:'var(--green)' },
          { label:'Active Accounts', value: String(activeCount),                  color:'var(--blue)' },
        ].map((cell, idx, arr) => (
          <div key={cell.label} style={{
            flex:1, display:'flex', flexDirection:'column', alignItems:'center',
            padding:'10px 0 9px', gap:'3px',
            borderRight: idx < arr.length - 1 ? '1px solid var(--border)' : 'none',
          }}>
            <span style={{
              fontFamily:'JetBrains Mono, monospace', fontSize:'16px',
              fontWeight:700, color: cell.color, lineHeight:1,
            }}>
              {cell.value}
            </span>
            <span style={{
              fontSize:'10px', fontWeight:600, letterSpacing:'.08em',
              textTransform:'uppercase', color:'var(--dim)',
            }}>
              {cell.label}
            </span>
          </div>
        ))}
      </div>

      {/* Account list */}
      <div style={{
        flex:1, overflowY:'auto', padding:'14px 14px 80px',
        scrollbarWidth:'none',
      }}>
        {loading ? (
          <p style={{ color:'var(--dim)', padding:'20px 0' }}>Loading...</p>
        ) : accounts.length === 0 ? (
          <p style={{ color:'var(--dim)', textAlign:'center', padding:'40px 0' }}>
            No accounts configured. Add one to get started.
          </p>
        ) : (
          accounts.map(acc => {
            const bal  = balances[acc.id];
            const meta = metas[acc.id];
            const isLive = acc.mode === 'live';
            const barColor = isLive ? 'var(--green)' : 'var(--blue)';

            return (
              <div key={acc.id} style={{
                background:    'var(--bg3)',
                borderRadius:  'var(--r)',
                border:        '1px solid var(--border)',
                marginBottom:  '10px',
                position:      'relative',
                display:       'flex',
                flexDirection: 'column',
                overflow:      'hidden',
              }}>
                {/* Left bar */}
                <div style={{
                  position:'absolute', left:0, top:0, bottom:0,
                  width:'4px', background: barColor, zIndex:1,
                }} />

                {/* Row 1: label + exchange + mode badge */}
                <div style={{
                  display:'flex', alignItems:'center', gap:'6px',
                  padding:'12px 12px 0 18px', lineHeight:1,
                }}>
                  <span style={{
                    fontSize:'16px', fontWeight:700, letterSpacing:'-.01em',
                    color:'var(--text)', flexShrink:0,
                  }}>
                    {acc.label}
                  </span>
                  <span style={{
                    fontSize:'11px', fontWeight:600, color:'var(--dim)',
                    textTransform:'uppercase', letterSpacing:'.04em',
                    marginTop:'1px',
                  }}>
                    {acc.exchange}
                  </span>
                  <div style={{ flex:1 }} />
                  <span style={{
                    background: barColor, color:'white',
                    fontSize:'9px', fontWeight:800, padding:'2px 6px',
                    borderRadius:'10px', textTransform:'uppercase',
                  }}>
                    {acc.mode}
                  </span>
                </div>

                {/* Row 2: ID + Credentials snippet */}
                <div style={{
                  display:'flex', alignItems:'center', gap:'8px',
                  padding:'6px 12px 12px 18px',
                }}>
                   <span style={{
                    fontFamily:'JetBrains Mono, monospace', fontSize:'11px',
                    color:'var(--dim)',
                  }}>
                    ID: {acc.id}
                  </span>
                  <div style={{ width:'1px', height:'10px', background:'var(--border)' }} />
                  <span style={{
                    fontFamily:'JetBrains Mono, monospace', fontSize:'11px',
                    color:'var(--muted)',
                  }}>
                    {meta?.api_key_preview || meta?.wallet_address || 'Credentials hidden'}
                  </span>
                </div>

                {/* Row 3: Balance Data */}
                <div style={{
                  display:'flex', background:'var(--bg2)',
                  borderTop:'1px solid var(--border)',
                }}>
                  {[
                    { label:'Equity', value: bal ? `${bal.total_balance.toFixed(2)} ${bal.currency}` : '---' },
                    { label:'Available', value: bal ? `${bal.available_balance.toFixed(2)} ${bal.currency}` : '---' },
                    { label:'Used', value: bal ? `${bal.used_margin.toFixed(2)} ${bal.currency}` : '---' },
                  ].map((cell, idx) => (
                    <div key={cell.label} style={{
                      flex:1, padding:'10px 12px', display:'flex',
                      flexDirection:'column', gap:'2px',
                      borderRight: idx < 2 ? '1px solid var(--border)' : 'none',
                    }}>
                      <span style={{
                        fontSize:'10px', fontWeight:600, textTransform:'uppercase',
                        color:'var(--dim)', letterSpacing:'.04em',
                      }}>
                        {cell.label}
                      </span>
                      <span style={{
                        fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
                        fontWeight:700, color:'var(--text)',
                      }}>
                        {cell.value}
                      </span>
                    </div>
                  ))}
                </div>

                {/* Hover Actions */}
                <div style={{
                   display:'flex', gap:'8px', padding:'10px 12px',
                   background:'var(--bg4)', borderTop:'1px solid var(--border)',
                }}>
                  <button
                    onClick={() => { setCredAccount(acc); setCredJson(CRED_PLACEHOLDERS[acc.exchange] || ''); }}
                    style={{
                      background:'none', border:'1px solid var(--border)',
                      borderRadius:'4px', padding:'4px 10px', color:'var(--muted)',
                      fontSize:'11px', fontWeight:600, cursor:'pointer',
                    }}>
                    Update Credentials
                  </button>
                  <div style={{ flex:1 }} />
                  <button
                    onClick={() => handleDelete(acc.id, acc.label)}
                    style={{
                      background:'none', border:'1px solid var(--border)',
                      borderRadius:'4px', padding:'4px 10px', color:'var(--red)',
                      fontSize:'11px', fontWeight:600, cursor:'pointer',
                    }}>
                    Delete
                  </button>
                </div>
              </div>
            );
          })
        )}
      </div>

      {/* Add Modal */}
      {showAdd && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,0.8)',
          zIndex:100, display:'flex', alignItems:'center', justifyContent:'center',
          padding:'20px',
        }}>
          <div style={{
            background:'var(--bg2)', border:'1px solid var(--border)',
            borderRadius:'var(--r)', width:'100%', maxWidth:'400px',
            padding:'20px', display:'flex', flexDirection:'column', gap:'15px',
          }}>
            <span style={{ fontSize:'18px', fontWeight:800, color:'var(--text)' }}>
              Add Exchange Account
            </span>
            {addError && <p style={{ color:'var(--red)', fontSize:'12px' }}>{addError}</p>}
            
            <div style={{ display:'flex', flexDirection:'column', gap:'5px' }}>
              <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)' }}>ACCOUNT ID</label>
              <input
                value={addForm.id}
                onChange={e => setAddForm({...addForm, id: e.target.value})}
                placeholder="e.g. main-blofin"
                style={{
                  background:'var(--bg3)', border:'1px solid var(--border)',
                  borderRadius:'4px', padding:'8px 10px', color:'var(--text)',
                  fontFamily:'JetBrains Mono, monospace', fontSize:'13px',
                }}
              />
            </div>

            <div style={{ display:'flex', flexDirection:'column', gap:'5px' }}>
              <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)' }}>LABEL</label>
              <input
                value={addForm.label}
                onChange={e => setAddForm({...addForm, label: e.target.value})}
                placeholder="e.g. My Blofin Account"
                style={{
                  background:'var(--bg3)', border:'1px solid var(--border)',
                  borderRadius:'4px', padding:'8px 10px', color:'var(--text)',
                  fontSize:'13px',
                }}
              />
            </div>

            <div style={{ display:'flex', gap:'10px' }}>
              <div style={{ flex:1, display:'flex', flexDirection:'column', gap:'5px' }}>
                <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)' }}>EXCHANGE</label>
                <select
                  value={addForm.exchange}
                  onChange={e => setAddForm({...addForm, exchange: e.target.value})}
                  style={{
                    background:'var(--bg3)', border:'1px solid var(--border)',
                    borderRadius:'4px', padding:'8px 10px', color:'var(--text)',
                    fontSize:'13px',
                  }}>
                  <option value="blofin">Blofin</option>
                  <option value="hyperliquid">Hyperliquid</option>
                </select>
              </div>
              <div style={{ flex:1, display:'flex', flexDirection:'column', gap:'5px' }}>
                <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)' }}>MODE</label>
                <select
                  value={addForm.mode}
                  onChange={e => setAddForm({...addForm, mode: e.target.value})}
                  style={{
                    background:'var(--bg3)', border:'1px solid var(--border)',
                    borderRadius:'4px', padding:'8px 10px', color:'var(--text)',
                    fontSize:'13px',
                  }}>
                  <option value="demo">Demo</option>
                  <option value="live">Live</option>
                </select>
              </div>
            </div>

            <div style={{ display:'flex', gap:'10px', marginTop:'10px' }}>
              <button
                onClick={() => setShowAdd(false)}
                style={{
                  flex:1, background:'none', border:'1px solid var(--border)',
                  borderRadius:'4px', padding:'10px', color:'var(--muted)',
                  fontWeight:600, cursor:'pointer',
                }}>
                Cancel
              </button>
              <button
                onClick={handleAdd}
                disabled={addLoading}
                style={{
                  flex:1, background:'var(--blue)', border:'none',
                  borderRadius:'4px', padding:'10px', color:'white',
                  fontWeight:700, cursor:'pointer', opacity: addLoading ? 0.5 : 1,
                }}>
                {addLoading ? 'Saving...' : 'Add Account'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Credentials Modal */}
      {credAccount && (
        <div style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,0.8)',
          zIndex:100, display:'flex', alignItems:'center', justifyContent:'center',
          padding:'20px',
        }}>
          <div style={{
            background:'var(--bg2)', border:'1px solid var(--border)',
            borderRadius:'var(--r)', width:'100%', maxWidth:'500px',
            padding:'20px', display:'flex', flexDirection:'column', gap:'15px',
          }}>
            <div style={{ display:'flex', flexDirection:'column' }}>
              <span style={{ fontSize:'18px', fontWeight:800, color:'var(--text)' }}>
                Update Credentials
              </span>
              <span style={{ fontSize:'12px', color:'var(--dim)' }}>
                {credAccount.label} ({credAccount.id})
              </span>
            </div>

            <div style={{ display:'flex', flexDirection:'column', gap:'5px' }}>
              <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)' }}>
                JSON CREDENTIALS
              </label>
              <textarea
                value={credJson}
                onChange={e => setCredJson(e.target.value)}
                rows={6}
                style={{
                  background:'var(--bg3)', border:'1px solid var(--border)',
                  borderRadius:'4px', padding:'10px', color:'var(--text)',
                  fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
                  resize:'none',
                }}
              />
              <p style={{ fontSize:'10px', color:'var(--muted)', marginTop:'5px' }}>
                Note: These will be encrypted by the executor and stored as ciphertext. 
                They are never stored or transmitted in plain text.
              </p>
            </div>

            {credStatus && (
              <p style={{
                fontSize:'12px', fontWeight:600,
                color: credStatus.includes('Error') ? 'var(--red)' : 'var(--green)'
              }}>
                {credStatus}
              </p>
            )}

            <div style={{ display:'flex', gap:'10px', marginTop:'10px' }}>
              <button
                onClick={() => { setCredAccount(null); setCredJson(''); setCredStatus(null); }}
                style={{
                  flex:1, background:'none', border:'1px solid var(--border)',
                  borderRadius:'4px', padding:'10px', color:'var(--muted)',
                  fontWeight:600, cursor:'pointer',
                }}>
                Cancel
              </button>
              <button
                onClick={handleUpdateCreds}
                style={{
                  flex:1, background:'var(--green)', border:'none',
                  borderRadius:'4px', padding:'10px', color:'white',
                  fontWeight:700, cursor:'pointer',
                }}>
                Save Credentials
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}
