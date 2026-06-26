/**
 * Per-panel data export + copyable deep-links (Task 27).
 *
 * Pure, dependency-free helpers so every panel can ship a CSV/JSON export and a
 * shareable deep-link with ZERO per-panel boilerplate (the `PanelActions` toolbar
 * in `PanelFrame` wires these for all panels). Kept side-effect-free except the
 * single `downloadFile` browser trigger, so the serialization + URL logic is unit
 * testable without a DOM.
 */

/** Pretty-print any validated panel payload as JSON. */
export function toJson(data: unknown): string {
  return JSON.stringify(data, null, 2)
}

/**
 * Derive tabular CSV rows from an arbitrary panel payload. Panels render many
 * shapes (an array, an object whose primary content is an array, or a flat
 * aggregate), so this picks the most table-like view:
 *   - an array of objects → those rows;
 *   - an object with a primary array-of-objects property (entries / sessions /
 *     features / points …) → that array;
 *   - otherwise → a single row of the object's scalar fields.
 */
export function flattenForCsv(data: unknown): Record<string, unknown>[] {
  if (Array.isArray(data)) {
    return data.filter(
      (r): r is Record<string, unknown> => r != null && typeof r === 'object',
    )
  }
  if (data != null && typeof data === 'object') {
    const obj = data as Record<string, unknown>
    for (const value of Object.values(obj)) {
      if (
        Array.isArray(value) &&
        value.length > 0 &&
        value.every((v) => v != null && typeof v === 'object')
      ) {
        return value as Record<string, unknown>[]
      }
    }
    const row: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(obj)) {
      if (v == null || typeof v !== 'object') row[k] = v
    }
    return Object.keys(row).length > 0 ? [row] : []
  }
  return []
}

/** RFC-4180 escape a single CSV cell. */
function escapeCsvCell(value: unknown): string {
  const s =
    value == null
      ? ''
      : typeof value === 'object'
        ? JSON.stringify(value)
        : String(value)
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** Serialize rows to a CSV string (header = union of all row keys, stable order). */
export function toCsv(rows: Record<string, unknown>[]): string {
  if (rows.length === 0) return ''
  const headers: string[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (!seen.has(key)) {
        seen.add(key)
        headers.push(key)
      }
    }
  }
  const lines = [headers.join(',')]
  for (const row of rows) {
    lines.push(headers.map((h) => escapeCsvCell(row[h])).join(','))
  }
  return lines.join('\n')
}

/** Build a CSV string straight from a panel payload (flatten → serialize). */
export function csvFromData(data: unknown): string {
  return toCsv(flattenForCsv(data))
}

/**
 * Trigger a client-side file download. No-op when there is no DOM (SSR / a test
 * environment without `URL.createObjectURL`), so callers never need to guard.
 */
export function downloadFile(
  filename: string,
  content: string,
  mime: string,
): void {
  if (typeof document === 'undefined') return
  if (typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') {
    return
  }
  const blob = new Blob([content], { type: mime })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/** A location shape sufficient to build a deep-link (matches `window.location`). */
export interface DeepLinkLocation {
  origin: string
  pathname: string
  search?: string
}

/**
 * Build a copyable deep-link that RESTORES the current view + its filter/drill
 * state. The route path already encodes drill state (e.g. `/memory/kb/learnings/
 * <id>`); `extraParams` layers in any in-panel filter state as query params so
 * opening the link reproduces exactly what the user is looking at.
 */
export function buildShareUrl(
  location: DeepLinkLocation,
  extraParams?: Record<string, string | number | boolean | undefined | null>,
): string {
  const params = new URLSearchParams(location.search ?? '')
  if (extraParams) {
    for (const [key, value] of Object.entries(extraParams)) {
      if (value == null || value === '') params.delete(key)
      else params.set(key, String(value))
    }
  }
  const qs = params.toString()
  return `${location.origin}${location.pathname}${qs ? `?${qs}` : ''}`
}
