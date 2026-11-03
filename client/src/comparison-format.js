/** Keep small measured distances positive rather than rounding them to zero. */
export function formatComparisonNumber(value) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  const magnitude = Math.abs(value);
  const scientific = magnitude > 0 && (magnitude < 0.0001 || magnitude >= 100000000);
  return new Intl.NumberFormat('es-ES', {
    maximumSignificantDigits: 8, notation: scientific ? 'scientific' : 'standard',
  }).format(value);
}
