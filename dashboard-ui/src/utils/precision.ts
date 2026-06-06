/**
 * Decimal precision rules per symbol and exchange.
 * price_dp: decimal places for price display
 * size_dp:  decimal places for size display
 */
interface PrecisionRule {
  price_dp: number;
  size_dp:  number;
}

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
  // Normalise: "BTC/USDT" → "BTC-USDT"
  const normalised = symbol.replace('/', '-').toUpperCase();
  return RULES[normalised] ?? DEFAULT_RULE;
}

export function formatPrice(symbol: string, value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (isNaN(num)) return '—';
  const { price_dp } = getRule(symbol);
  return num.toFixed(price_dp);
}

export function formatSize(symbol: string, value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const num = Number(value);
  if (isNaN(num)) return '—';
  const { size_dp } = getRule(symbol);
  return size_dp === 0
    ? Math.round(num).toString()
    : num.toFixed(size_dp);
}
