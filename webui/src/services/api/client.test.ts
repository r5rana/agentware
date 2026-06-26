/**
 * Task 16 — typed validated API client tests.
 *
 *   1. Every /api/* fixture validates against its contract schema (the recorded
 *      synthetic payloads are a faithful sample of the live shape).
 *   2. The client returns schema-validated typed data over a mocked fetch.
 *   3. A malformed payload is REJECTED at the boundary (ApiError 'validation').
 *   4. The same-origin guard blocks cross-origin / non-/api routes before fetch.
 *   5. HTTP and transport failures surface as typed ApiErrors.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, apiFetch, ApiError, resolveSameOrigin } from './client'
import { API_CONTRACT, API_PARAM_CONTRACT, TasksResponseSchema } from './contract'

/* Load every recorded synthetic fixture, keyed by its bare name (e.g. "health"). */
const fixtureModules = import.meta.glob('../../fixtures/*.json', { eager: true }) as Record<
  string,
  { default: unknown }
>
const fixtures: Record<string, unknown> = {}
for (const [path, mod] of Object.entries(fixtureModules)) {
  const name = path.split('/').pop()!.replace(/\.json$/, '')
  fixtures[name] = mod.default
}

function getFixture(name: string): unknown {
  if (!(name in fixtures)) throw new Error(`missing fixture: ${name}`)
  return fixtures[name]
}

/** The subset of fetch's init we assert on (avoids DOM lib type-name lint). */
type FetchInit = { method?: string; credentials?: string; signal?: unknown }

/** Stub global fetch to return `body` with the given status as JSON. */
function stubFetch(body: unknown, init: { ok?: boolean; status?: number; json?: boolean } = {}) {
  const { ok = true, status = 200, json = true } = init
  const res = {
    ok,
    status,
    json: async () => {
      if (!json) throw new SyntaxError('not json')
      return body
    },
  } as Response
  const spy = vi.fn(async (_input: unknown, _init?: FetchInit): Promise<Response> => res)
  vi.stubGlobal('fetch', spy)
  return spy
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('contract fixtures validate against their schemas', () => {
  for (const [key, entry] of Object.entries(API_CONTRACT)) {
    it(`static ${key} (${entry.route})`, () => {
      const result = entry.schema.safeParse(getFixture(entry.fixture))
      if (!result.success) throw result.error
      expect(result.success).toBe(true)
    })
  }
  for (const [key, entry] of Object.entries(API_PARAM_CONTRACT)) {
    it(`param ${key}`, () => {
      const result = entry.schema.safeParse(getFixture(entry.fixture))
      if (!result.success) throw result.error
      expect(result.success).toBe(true)
    })
  }
})

describe('typed client returns validated data over a mocked fetch', () => {
  for (const [key, entry] of Object.entries(API_CONTRACT)) {
    it(`api.${key}() resolves parsed data`, async () => {
      const spy = stubFetch(getFixture(entry.fixture))
      const fetcher = api[key as keyof typeof api] as () => Promise<unknown>
      const data = await fetcher()
      expect(data).toBeTruthy()
      // Hit the same-origin /api/ route, GET, no credentials.
      const [calledUrl, calledInit] = spy.mock.calls[0]
      expect(String(calledUrl)).toContain(entry.route)
      expect(calledInit?.method).toBe('GET')
      expect(calledInit?.credentials).toBe('omit')
    })
  }

  it('param fetcher builds the concrete drill-down route', async () => {
    const spy = stubFetch(getFixture('tasks'))
    await api.tasks('260101-observability-demo')
    expect(String(spy.mock.calls[0][0])).toContain(
      '/api/tasks/260101-observability-demo',
    )
  })

  it('encodes a param with special characters', async () => {
    stubFetch(getFixture('kbTag'))
    const spy = vi.mocked(globalThis.fetch)
    await api.kbTag('a/b tag')
    expect(String(spy.mock.calls[0][0])).toContain('/api/kb/tags/a%2Fb%20tag')
  })
})

describe('malformed payloads are rejected at the boundary', () => {
  it('throws ApiError(validation) when a required field has the wrong type', async () => {
    stubFetch({ feature: 123, transitions: 'nope' })
    await expect(api.tasks('x')).rejects.toBeInstanceOf(ApiError)
    try {
      await api.tasks('x')
    } catch (e) {
      const err = e as ApiError
      expect(err.kind).toBe('validation')
      expect(err.issues?.length).toBeGreaterThan(0)
    }
  })

  it('apiFetch surfaces zod issues for a malformed body', async () => {
    stubFetch({ feature: 5 })
    await expect(apiFetch('/api/tasks/x', TasksResponseSchema)).rejects.toMatchObject({
      kind: 'validation',
    })
  })
})

describe('same-origin guard', () => {
  it('resolves a relative /api/ route to the current origin', () => {
    expect(resolveSameOrigin('/api/loop')).toMatch(/\/api\/loop$/)
  })

  it.each([
    'http://evil.example/api/loop',
    '//evil.example/api/loop',
    'https://localhost/api/loop', // different scheme/port → different origin
  ])('blocks cross-origin route %s', (route) => {
    try {
      resolveSameOrigin(route)
      throw new Error('expected rejection')
    } catch (e) {
      expect(e).toBeInstanceOf(ApiError)
      expect((e as ApiError).kind).toBe('origin')
    }
  })

  it('rejects a same-origin route outside the /api/ namespace', () => {
    expect(() => resolveSameOrigin('/secrets/index.json')).toThrow(ApiError)
  })

  it('never calls fetch when the origin guard rejects', async () => {
    const spy = stubFetch({})
    await expect(apiFetch('http://evil.example/api/loop', TasksResponseSchema)).rejects.toMatchObject(
      { kind: 'origin' },
    )
    expect(spy).not.toHaveBeenCalled()
  })
})

describe('transport + http failures are typed', () => {
  it('wraps a non-2xx response as ApiError(http) with the status', async () => {
    stubFetch({}, { ok: false, status: 503 })
    await expect(apiFetch('/api/loop', TasksResponseSchema)).rejects.toMatchObject({
      kind: 'http',
      status: 503,
    })
  })

  it('wraps a non-JSON body as ApiError(parse)', async () => {
    stubFetch(null, { json: false })
    await expect(apiFetch('/api/loop', TasksResponseSchema)).rejects.toMatchObject({
      kind: 'parse',
    })
  })

  it('wraps a transport failure as ApiError(network)', async () => {
    const spy = vi.fn(async () => {
      throw new TypeError('boom')
    })
    vi.stubGlobal('fetch', spy)
    await expect(apiFetch('/api/loop', TasksResponseSchema)).rejects.toMatchObject({
      kind: 'network',
    })
  })
})
