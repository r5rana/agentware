/**
 * Task 17 — TanStack Query data-layer tests.
 *
 *   1. Every static endpoint hook returns SCHEMA-VALIDATED typed data over a
 *      mocked fetch (the data flows through the zod client, so a hook can never
 *      hand a panel an unvalidated payload).
 *   2. Every parameterized drill-down hook fetches with its param — and stays
 *      DISABLED (no network) until a param is provided.
 *   3. The LIVE loop hook sets a `refetchInterval` (= LIVE_REFETCH_MS); persisted
 *      endpoints set none — proving the idle-resilient polling policy.
 *   4. Query keys are centralized + stable (cache identity lives in one place).
 */
import { createElement, type ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { API_CONTRACT, API_PARAM_CONTRACT } from '@/services/api/client'
import {
  LIVE_REFETCH_MS,
  queryKeys,
  useAuthoring,
  useContextTax,
  useCost,
  useFeatures,
  useHealth,
  useKb,
  useKbLearningDetail,
  useKbLearnings,
  useKbProjects,
  useKbTag,
  useLoop,
  useLoopAnalytics,
  useOutcomes,
  useQuality,
  useScaling,
  useTasks,
  useTrace,
  type QueryOverrides,
} from './index'

/* Load every recorded synthetic fixture, keyed by its bare name (e.g. "health"). */
const fixtureModules = import.meta.glob('../../fixtures/*.json', {
  eager: true,
}) as Record<string, { default: unknown }>
const fixtures: Record<string, unknown> = {}
for (const [path, mod] of Object.entries(fixtureModules)) {
  const name = path
    .split('/')
    .pop()!
    .replace(/\.json$/, '')
  fixtures[name] = mod.default
}
function getFixture(name: string): unknown {
  if (!(name in fixtures)) throw new Error(`missing fixture: ${name}`)
  return fixtures[name]
}

/** Stub global fetch to return `body` as a 200 JSON response. */
function stubFetch(body: unknown) {
  const res = { ok: true, status: 200, json: async () => body } as Response
  const spy = vi.fn(async (..._args: unknown[]): Promise<Response> => res)
  vi.stubGlobal('fetch', spy)
  return spy
}

/** A fresh isolated cache per test (retry off → failures surface immediately). */
function makeHarness() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client }, children)
  return { client, wrapper }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

/* Static endpoint hooks paired with their contract key (fixture + schema). */
const STATIC_HOOKS = {
  health: useHealth,
  quality: useQuality,
  loop: useLoop,
  loopAnalytics: useLoopAnalytics,
  cost: useCost,
  authoring: useAuthoring,
  contextTax: useContextTax,
  scaling: useScaling,
  outcomes: useOutcomes,
  kb: useKb,
  kbProjects: useKbProjects,
  kbLearnings: useKbLearnings,
  features: useFeatures,
} as const

describe('static endpoint hooks', () => {
  for (const key of Object.keys(
    STATIC_HOOKS,
  ) as (keyof typeof STATIC_HOOKS)[]) {
    it(`use ${key} returns schema-validated typed data`, async () => {
      const entry = API_CONTRACT[key]
      const fixture = getFixture(entry.fixture)
      const fetchSpy = stubFetch(fixture)
      const { wrapper } = makeHarness()

      const hook = STATIC_HOOKS[key] as (
        o?: QueryOverrides,
      ) => ReturnType<typeof useHealth>
      const { result } = renderHook(() => hook(), { wrapper })

      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      // The hook never hands back unvalidated data: it equals the schema parse.
      expect(result.current.data).toEqual(entry.schema.parse(fixture))
      expect(fetchSpy).toHaveBeenCalledTimes(1)
    })
  }
})

describe('parameterized drill-down hooks', () => {
  const cases = [
    {
      key: 'kbLearningDetail' as const,
      hook: useKbLearningDetail,
      param: 'learn-x',
    },
    { key: 'kbTag' as const, hook: useKbTag, param: 'geofence' },
    { key: 'tasks' as const, hook: useTasks, param: '260101-demo' },
    { key: 'trace' as const, hook: useTrace, param: '260101-demo' },
  ]

  for (const { key, hook, param } of cases) {
    it(`use ${key} fetches + validates when a param is supplied`, async () => {
      const entry = API_PARAM_CONTRACT[key]
      const fixture = getFixture(entry.fixture)
      const fetchSpy = stubFetch(fixture)
      const { wrapper } = makeHarness()

      const { result } = renderHook(() => hook(param), { wrapper })
      await waitFor(() => expect(result.current.isSuccess).toBe(true))

      expect(result.current.data).toEqual(entry.schema.parse(fixture))
      expect(fetchSpy).toHaveBeenCalledTimes(1)
      // The concrete drill-down route carries the encoded param.
      const calledUrl = String(fetchSpy.mock.calls[0]?.[0])
      expect(calledUrl).toContain(encodeURIComponent(param))
    })

    it(`use ${key} stays DISABLED (no network) until a param is set`, async () => {
      const fetchSpy = stubFetch(getFixture(API_PARAM_CONTRACT[key].fixture))
      const { wrapper } = makeHarness()

      const { result } = renderHook(() => hook(undefined), { wrapper })
      // enabled:false ⇒ the query is pending but never fetches.
      expect(result.current.fetchStatus).toBe('idle')
      expect(result.current.data).toBeUndefined()
      expect(fetchSpy).not.toHaveBeenCalled()
    })
  }
})

describe('live polling policy', () => {
  it('the loop hook sets a refetch interval (LIVE_REFETCH_MS)', async () => {
    stubFetch(getFixture('loop'))
    const { client, wrapper } = makeHarness()

    const { result } = renderHook(() => useLoop(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const query = client
      .getQueryCache()
      .find({ queryKey: queryKeys.static('loop') })
    expect(query).toBeDefined()
    expect(query!.observers[0]?.options.refetchInterval).toBe(LIVE_REFETCH_MS)
  })

  it('a persisted (non-live) hook sets NO refetch interval', async () => {
    stubFetch(getFixture('health'))
    const { client, wrapper } = makeHarness()

    const { result } = renderHook(() => useHealth(), { wrapper })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const query = client
      .getQueryCache()
      .find({ queryKey: queryKeys.static('health') })
    expect(query!.observers[0]?.options.refetchInterval).toBe(false)
  })

  it('a panel can override the live interval (e.g. pause polling)', async () => {
    stubFetch(getFixture('loop'))
    const { client, wrapper } = makeHarness()

    const { result } = renderHook(() => useLoop({ refetchInterval: false }), {
      wrapper,
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))

    const query = client
      .getQueryCache()
      .find({ queryKey: queryKeys.static('loop') })
    expect(query!.observers[0]?.options.refetchInterval).toBe(false)
  })
})

describe('centralized query keys', () => {
  it('static keys are stable + namespaced', () => {
    expect(queryKeys.static('loop')).toEqual(['api', 'loop'])
    expect(queryKeys.static('health')).toEqual(['api', 'health'])
  })

  it('param keys embed the param (distinct cache entries)', () => {
    expect(queryKeys.param('kbTag', 'geofence')).toEqual([
      'api',
      'kbTag',
      'geofence',
    ])
    expect(queryKeys.param('tasks', undefined)).toEqual(['api', 'tasks', null])
  })
})
