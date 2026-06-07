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
  api_key?:        string;   // Blofin — full key, non-sensitive without secret
  wallet_address?: string;   // HL — API/agent wallet
  main_wallet?:    string;   // HL — main wallet (if agent wallet is used)
  error?:          string;
}

const API = '/api/dashboard';

export default function Accounts() {
  const [accounts, setAccounts]   = useState<Account[]>([]);
  const [balances, setBalances]   = useState<Record<string, Balance>>({});
  const [metas,    setMetas]      = useState<Record<string, AccountMeta>>({});
  const [loading,  setLoading]    = useState(true);
  const [showAdd,      setShowAdd]      = useState(false);
  const [credAccount,  setCredAccount]  = useState<Account | null>(null);
  const [credFields,   setCredFields]   = useState<Record<string, string>>({});
  const [credStatus,   setCredStatus]   = useState<string | null>(null);
  const [addForm,      setAddForm]      = useState({ name: '', exchange: 'blofin', mode: 'demo' });
  const [addCredFields,setAddCredFields]= useState<Record<string, string>>({});
  const [addError,     setAddError]     = useState<string | null>(null);
  const [addLoading,   setAddLoading]   = useState(false);

  const CRED_FIELDS: Record<string, { key: string; label: string; type: 'text' | 'password'; placeholder: string }[]> = {
    blofin: [
      { key: 'api_key',        label: 'API Key',        type: 'text',     placeholder: 'bfkey-...' },
      { key: 'api_secret',     label: 'API Secret',     type: 'password', placeholder: '' },
      { key: 'api_passphrase', label: 'API Passphrase', type: 'password', placeholder: '' },
    ],
    hyperliquid: [
      { key: 'api_wallet',  label: 'API Wallet Address',     type: 'text',     placeholder: '0x...' },
      { key: 'private_key', label: 'API Wallet Private Key', type: 'password', placeholder: '0x...' },
      { key: 'main_wallet', label: 'Main Wallet Address',    type: 'text',     placeholder: '0x...' },
    ],
  };

  const slugify = (name: string, exchange: string): string => {
    const base = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 22);
    const rand = Math.random().toString(36).slice(2, 6);
    return `${exchange}-${base}-${rand}`;
  };

  const emptyCredFields = (exchange: string): Record<string, string> => {
    const empty: Record<string, string> = {};
    (CRED_FIELDS[exchange] || []).forEach(f => { empty[f.key] = ''; });
    return empty;
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
    if (!addForm.name.trim()) { setAddError('Name is required'); return; }
    const missingCreds = (CRED_FIELDS[addForm.exchange] || []).filter(f => !addCredFields[f.key]?.trim());
    if (missingCreds.length > 0) {
      setAddError(`Fill in: ${missingCreds.map(f => f.label).join(', ')}`);
      return;
    }
    setAddLoading(true);
    const id = slugify(addForm.name, addForm.exchange);
    try {
      // Step 1: create account record
      const res = await fetch(`${API}/accounts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id, exchange: addForm.exchange, mode: addForm.mode, label: addForm.name }),
      });
      const data = await res.json();
      if (!res.ok) { setAddError(data.error || 'Failed to create account'); return; }

      // Step 2: validate + save credentials (rolls back on failure)
      const credRes = await fetch(`${API}/accounts/${id}/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials_json: JSON.stringify(addCredFields) }),
      });
      const credData = await credRes.json();
      if (!credRes.ok) {
        await fetch(`${API}/accounts/${id}`, { method: 'DELETE' });
        setAddError(credData.error || 'Credential validation failed');
        return;
      }

      setShowAdd(false);
      setAddForm({ name: '', exchange: 'blofin', mode: 'demo' });
      setAddCredFields({});
      fetchAccounts();
    } catch (e: any) { setAddError(e.message); }
    finally { setAddLoading(false); }
  };

  const handleUpdateCreds = async () => {
    if (!credAccount) return;
    const fields = CRED_FIELDS[credAccount.exchange] || [];
    const missing = fields.filter(f => !credFields[f.key]?.trim());
    if (missing.length > 0) {
      setCredStatus(`Error: fill in ${missing.map(f => f.label).join(', ')}`);
      return;
    }
    setCredStatus('Validating…');
    try {
      const res = await fetch(`${API}/accounts/${credAccount.id}/credentials`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credentials_json: JSON.stringify(credFields) }),
      });
      const data = await res.json();
      if (!res.ok) { setCredStatus(`Error: ${data.error}`); return; }
      setCredStatus(`Saved — ${data.detail || 'credentials updated'}`);
      setMetas(prev => { const n = {...prev}; delete n[credAccount.id]; return n; });
      setTimeout(() => { setCredAccount(null); setCredFields({}); setCredStatus(null); }, 2500);
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
            onClick={() => { setAddCredFields(emptyCredFields('blofin')); setShowAdd(true); }}
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
                    {meta?.api_key || meta?.wallet_address || 'Credentials hidden'}
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
                    onClick={() => {
                      setCredAccount(acc);
                      const meta = metas[acc.id] || {};
                      const prefill: Record<string, string> = {};
                      (CRED_FIELDS[acc.exchange] || []).forEach(f => {
                        // Pre-fill non-sensitive fields from cached meta; leave sensitive blank
                        if (f.key === 'api_key')     prefill[f.key] = (meta as any).api_key        || '';
                        else if (f.key === 'api_wallet')  prefill[f.key] = (meta as any).wallet_address || '';
                        else if (f.key === 'main_wallet') prefill[f.key] = (meta as any).main_wallet   || '';
                        else prefill[f.key] = '';
                      });
                      setCredFields(prefill);
                    }}
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
          padding:'20px', overflowY:'auto',
        }}>
          <div style={{
            background:'var(--bg2)', border:'1px solid var(--border)',
            borderRadius:'var(--r)', width:'100%', maxWidth:'420px',
            padding:'20px', display:'flex', flexDirection:'column', gap:'14px',
            margin:'auto',
          }}>
            <span style={{ fontSize:'18px', fontWeight:800, color:'var(--text)' }}>
              Add Exchange Account
            </span>
            {addError && <p style={{ color:'var(--red)', fontSize:'12px', margin:0 }}>{addError}</p>}

            {/* Name */}
            <div style={{ display:'flex', flexDirection:'column', gap:'5px' }}>
              <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)',
                              textTransform:'uppercase', letterSpacing:'.04em' }}>Name</label>
              <input
                value={addForm.name}
                onChange={e => setAddForm({...addForm, name: e.target.value})}
                placeholder="e.g. My Blofin Demo"
                style={{
                  background:'var(--bg3)', border:'1px solid var(--border)',
                  borderRadius:'4px', padding:'8px 10px', color:'var(--text)', fontSize:'13px',
                }}
              />
              {addForm.name.trim() && (
                <span style={{
                  fontFamily:'JetBrains Mono, monospace', fontSize:'10px',
                  color:'var(--dim)', paddingLeft:'2px',
                }}>
                  ID: {addForm.exchange}-{addForm.name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 22)}-••••
                </span>
              )}
            </div>

            {/* Exchange + Mode */}
            <div style={{ display:'flex', gap:'10px' }}>
              <div style={{ flex:1, display:'flex', flexDirection:'column', gap:'5px' }}>
                <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)',
                                textTransform:'uppercase', letterSpacing:'.04em' }}>Exchange</label>
                <select
                  value={addForm.exchange}
                  onChange={e => {
                    const ex = e.target.value;
                    setAddForm(prev => ({...prev, exchange: ex}));
                    setAddCredFields(emptyCredFields(ex));
                  }}
                  style={{
                    background:'var(--bg3)', border:'1px solid var(--border)',
                    borderRadius:'4px', padding:'8px 10px', color:'var(--text)', fontSize:'13px',
                  }}>
                  <option value="blofin">Blofin</option>
                  <option value="hyperliquid">Hyperliquid</option>
                </select>
              </div>
              <div style={{ flex:1, display:'flex', flexDirection:'column', gap:'5px' }}>
                <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)',
                                textTransform:'uppercase', letterSpacing:'.04em' }}>Mode</label>
                <select
                  value={addForm.mode}
                  onChange={e => setAddForm(prev => ({...prev, mode: e.target.value}))}
                  style={{
                    background:'var(--bg3)', border:'1px solid var(--border)',
                    borderRadius:'4px', padding:'8px 10px', color:'var(--text)', fontSize:'13px',
                  }}>
                  <option value="demo">Demo</option>
                  <option value="live">Live</option>
                </select>
              </div>
            </div>

            {/* Credential fields */}
            <div style={{
              display:'flex', flexDirection:'column', gap:'10px',
              borderTop:'1px solid var(--border)', paddingTop:'12px',
            }}>
              <span style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)',
                             textTransform:'uppercase', letterSpacing:'.04em' }}>
                Credentials
              </span>
              {(CRED_FIELDS[addForm.exchange] || []).map(field => (
                <div key={field.key} style={{ display:'flex', flexDirection:'column', gap:'4px' }}>
                  <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)',
                                  letterSpacing:'.03em' }}>
                    {field.label}
                  </label>
                  <input
                    type={field.type}
                    value={addCredFields[field.key] || ''}
                    onChange={e => setAddCredFields(prev => ({...prev, [field.key]: e.target.value}))}
                    placeholder={field.placeholder}
                    autoComplete="off"
                    style={{
                      background:'var(--bg3)', border:'1px solid var(--border)',
                      borderRadius:'4px', padding:'8px 10px', color:'var(--text)',
                      fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
                      boxSizing:'border-box', width:'100%',
                    }}
                  />
                </div>
              ))}
              <p style={{ fontSize:'10px', color:'var(--muted)', margin:0 }}>
                Credentials are validated against the exchange then encrypted before storage.
              </p>
            </div>

            <div style={{ display:'flex', gap:'10px', marginTop:'4px' }}>
              <button
                onClick={() => { setShowAdd(false); setAddError(null); }}
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
                {addLoading ? 'Validating…' : 'Add Account'}
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

            <div style={{ display:'flex', flexDirection:'column', gap:'12px' }}>
              {(CRED_FIELDS[credAccount.exchange] || []).map(field => (
                <div key={field.key} style={{ display:'flex', flexDirection:'column', gap:'5px' }}>
                  <label style={{ fontSize:'11px', fontWeight:600, color:'var(--dim)',
                                  textTransform:'uppercase', letterSpacing:'.04em' }}>
                    {field.label}
                  </label>
                  <input
                    type={field.type}
                    value={credFields[field.key] || ''}
                    onChange={e => setCredFields(prev => ({ ...prev, [field.key]: e.target.value }))}
                    placeholder={field.placeholder}
                    autoComplete="off"
                    style={{
                      background:'var(--bg3)', border:'1px solid var(--border)',
                      borderRadius:'4px', padding:'8px 10px', color:'var(--text)',
                      fontFamily:'JetBrains Mono, monospace', fontSize:'12px',
                      width:'100%', boxSizing:'border-box',
                    }}
                  />
                </div>
              ))}
              <p style={{ fontSize:'10px', color:'var(--muted)', margin:0 }}>
                Credentials are encrypted by the executor before storage. Never transmitted in plain text.
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
                onClick={() => { setCredAccount(null); setCredFields({}); setCredStatus(null); }}
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
