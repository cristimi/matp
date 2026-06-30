/**
 * Price and size formatting. Call sites pass a per-row spec (from the positions API);
 * the static RULES map is kept only as a last-resort fallback when no spec is available.
 */

export interface PriceSpec {
  price_mode?: 'tick' | 'sigfig' | null;
  price_tick?: number | null;
  price_sigfigs?: number | null;
}

export interface SizeSpec {
  size_dp?: number | null;
}

// ---- helpers ----

function countDecimals(tick: number): number {
  const s = tick.toFixed(10).replace(/0+$/, '');
  const dot = s.indexOf('.');
  return dot === -1 ? 0 : s.length - dot - 1;
}

function toSigFigs(value: number, sigfigs: number): string {
  if (value === 0) return '0';
  const mag = Math.floor(Math.log10(Math.abs(value)));
  const dp = Math.max(0, sigfigs - 1 - mag);
  return value.toFixed(dp);
}

// ---- static fallback map ----

interface PrecisionRule { price_dp: number; size_dp: number }

const RULES: Record<string, PrecisionRule> = {
  'BTC-USDT':  { price_dp: 1, size_dp: 3 },
  'ETH-USDT':  { price_dp: 2, size_dp: 3 },
  'SOL-USDT':  { price_dp: 3, size_dp: 2 },
  'AVAX-USDT': { price_dp: 3, size_dp: 2 },
  'DOGE-USDT': { price_dp: 5, size_dp: 0 },
  'DOT-USDT':  { price_dp: 3, size_dp: 1 },
  'XRP-USDT':  { price_dp: 4, size_dp: 0 },
};

const DEFAULT_RULE: PrecisionRule = { price_dp: 2, size_dp: 3 };

function getRule(symbol: string): PrecisionRule {
  const normalised = symbol.replace('/', '-').toUpperCase();
  return RULES[normalised] ?? DEFAULT_RULE;
}

// ---- public API ----

export function formatPrice(
  symbol: string,
  value: number | string | null | undefined,
  spec?: PriceSpec | null,
): string {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (isNaN(num)) return '—';

  if (spec?.price_mode === 'tick' && spec.price_tick != null) {
    const tick = spec.price_tick;
    const rounded = Math.round(num / tick) * tick;
    return rounded.toFixed(countDecimals(tick));
  }
  if (spec?.price_mode === 'sigfig') {
    return toSigFigs(num, spec.price_sigfigs ?? 5);
  }

  return num.toFixed(getRule(symbol).price_dp);
}

export function formatSize(
  symbol: string,
  value: number | string | null | undefined,
  spec?: SizeSpec | null,
): string {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (isNaN(num)) return '—';

  const dp = spec?.size_dp != null ? spec.size_dp : getRule(symbol).size_dp;
  if (dp === 0) return Math.round(num).toString();
  return num.toFixed(dp).replace(/\.?0+$/, '');
}
