/**
 * Panel formatting helpers (Task 21) — small, pure, shared display formatters so
 * every panel renders numbers with the same conventions (compact token counts,
 * USD, percentages, wall-clock). Kept dependency-free and deterministic.
 */

/** Compact integer (1_234 → "1.2k", 1_050_000 → "1.05M"). */
export function compactNumber(value: number): string {
  if (!Number.isFinite(value)) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 2)}M`
  if (abs >= 1_000) return `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}k`
  return `${Math.round(value)}`
}

/** USD with sensible precision for small cents-level costs. */
export function usd(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (value !== 0 && Math.abs(value) < 1) return `$${value.toFixed(3)}`
  return `$${value.toFixed(2)}`
}

/** A 0..1 ratio (or already-percent if >1) as a percentage string. */
export function percent(ratio: number, digits = 1): string {
  if (!Number.isFinite(ratio)) return '—'
  const pct = ratio <= 1 ? ratio * 100 : ratio
  return `${pct.toFixed(digits)}%`
}

/** Seconds → a compact "Xm Ys" / "Xs" wall-clock. */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return s ? `${m}m ${s}s` : `${m}m`
}

/** Short date/label from an ISO-ish timestamp (keeps the date part). */
export function shortDate(ts: string | null | undefined): string {
  if (!ts) return '—'
  return ts.slice(0, 10)
}

/** Short commit sha (first 7). */
export function shortSha(sha: string | null | undefined): string {
  if (!sha) return '—'
  return sha.slice(0, 7)
}
