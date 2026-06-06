/**
 * Timestamp formatting utilities.
 * All dates relative to the user's local time.
 */

export function formatRelative(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return '—';

  const now       = new Date();
  const today     = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);

  const hhmm = date.toTimeString().slice(0, 5);
  if (date >= today)     return `Today ${hhmm}`;
  if (date >= yesterday) return `Yesterday ${hhmm}`;

  // YY-MM-DD HH:MM
  const yy = String(date.getFullYear()).slice(2);
  const mm = String(date.getMonth() + 1).padStart(2, '0');
  const dd = String(date.getDate()).padStart(2, '0');
  return `${yy}-${mm}-${dd} ${hhmm}`;
}

export function formatAbsolute(isoString: string | null | undefined): string {
  if (!isoString) return '—';
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return '—';

  const yy   = String(date.getFullYear()).slice(2);
  const mm   = String(date.getMonth() + 1).padStart(2, '0');
  const dd   = String(date.getDate()).padStart(2, '0');
  const hhmm = date.toTimeString().slice(0, 5);
  return `${yy}-${mm}-${dd} ${hhmm}`;
}
