/**
 * Task 27 — per-panel export + deep-link helpers.
 *
 * The pure serialization + URL logic: CSV/JSON are valid and round-trippable, the
 * generic flattener picks the most table-like view, and a deep-link RESTORES the
 * current filter/drill state (its query params parse back to what was set).
 */
import { describe, expect, it } from 'vitest'

import {
  buildShareUrl,
  csvFromData,
  flattenForCsv,
  toCsv,
  toJson,
} from './export'

describe('toJson', () => {
  it('produces valid, re-parseable JSON', () => {
    const data = { entry_count: 4, entries: [{ id: 'a' }, { id: 'b' }] }
    const json = toJson(data)
    expect(JSON.parse(json)).toEqual(data)
  })
})

describe('flattenForCsv', () => {
  it('returns an array payload as-is (objects only)', () => {
    const rows = [{ a: 1 }, { a: 2 }]
    expect(flattenForCsv(rows)).toEqual(rows)
  })

  it('picks the primary array-of-objects property of an object payload', () => {
    const payload = {
      entry_count: 2,
      categories: { learnings: 2 },
      entries: [{ id: 'x' }, { id: 'y' }],
    }
    expect(flattenForCsv(payload)).toEqual([{ id: 'x' }, { id: 'y' }])
  })

  it('falls back to a single row of scalar fields', () => {
    const payload = { wall_s: 12, tokens: 999, nested: { skip: true } }
    expect(flattenForCsv(payload)).toEqual([{ wall_s: 12, tokens: 999 }])
  })
})

describe('toCsv', () => {
  it('emits a header row + escaped cells (valid CSV)', () => {
    const csv = toCsv([
      { name: 'plain', note: 'has, comma' },
      { name: 'quote "x"', note: 'line\nbreak' },
    ])
    const lines = csv.split('\n')
    expect(lines[0]).toBe('name,note')
    // comma-bearing cell is quoted
    expect(csv).toContain('"has, comma"')
    // embedded quotes are doubled
    expect(csv).toContain('"quote ""x"""')
    // newline-bearing cell is quoted (so it stays one logical field)
    expect(csv).toContain('"line\nbreak"')
  })

  it('returns empty string for no rows', () => {
    expect(toCsv([])).toBe('')
  })

  it('unions keys across heterogeneous rows', () => {
    const csv = csvFromData([{ a: 1 }, { b: 2 }])
    expect(csv.split('\n')[0]).toBe('a,b')
  })
})

describe('buildShareUrl — deep-link restores filter/drill state', () => {
  it('preserves existing search params and layers in new ones', () => {
    const href = buildShareUrl(
      {
        origin: 'http://127.0.0.1:8765',
        pathname: '/loops/outcomes',
        search: '?range=30d',
      },
      { sort: 'desc', feature: '260626-observability-suite' },
    )
    const url = new URL(href)
    expect(url.origin).toBe('http://127.0.0.1:8765')
    expect(url.pathname).toBe('/loops/outcomes')
    // Opening the link RESTORES all of the filter state.
    expect(url.searchParams.get('range')).toBe('30d')
    expect(url.searchParams.get('sort')).toBe('desc')
    expect(url.searchParams.get('feature')).toBe('260626-observability-suite')
  })

  it('drops empty/nullish params and renders no "?" when none remain', () => {
    const href = buildShareUrl(
      { origin: 'http://x', pathname: '/cost', search: '' },
      { empty: '', missing: undefined },
    )
    expect(href).toBe('http://x/cost')
  })
})
