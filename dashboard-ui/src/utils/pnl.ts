/**
 * P&L formatting and color utilities.
 */

export function formatPnl(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (isNaN(num)) return '—';
  const sign = num >= 0 ? '+' : '';
  return `${sign}${num.toFixed(2)}`;
}

export function formatPct(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (isNaN(num)) return '—';
  const sign = num >= 0 ? '+' : '';
  return `${sign}${num.toFixed(2)}%`;
}

export type PnlClass = 'pos' | 'neg' | 'zero' | 'stale';

export function pnlColor(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return 'var(--text)';
  const num = Number(value);
  if (isNaN(num)) return 'var(--text)';
  if (num > 0)  return 'var(--green)';
  if (num < 0)  return 'var(--red)';
  return 'var(--text)';
}

export function pnlColorStale(): string {
  return 'var(--failed-color)';
}
