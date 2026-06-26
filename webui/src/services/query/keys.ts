/**
 * Centralized query keys + per-endpoint cache config (Task 17).
 *
 * Every TanStack Query hook draws its `queryKey`, `staleTime`, and (for the live
 * loop panel) `refetchInterval` from HERE, so cache identity and freshness are
 * defined in one place instead of scattered through hook call-sites. Keys are
 * derived from the Task-13 contract registry (`API_CONTRACT` / `API_PARAM_CONTRACT`)
 * so a new endpoint inherits a stable key shape with no drift.
 */
import { API_CONTRACT, API_PARAM_CONTRACT } from '@/services/api/client'
import type {
  ApiContractKey,
  ApiParamContractKey,
} from '@/services/api/contract'

const SECOND = 1000

/**
 * How often the LIVE loop panel re-polls `/api/loop` while mounted (the only
 * endpoint that reflects an in-flight run; everything else reads persisted data
 * and is idle-resilient, so it needs no interval).
 */
export const LIVE_REFETCH_MS = 5 * SECOND

/** Default freshness for persisted (non-live) reads. */
export const DEFAULT_STALE_MS = 30 * SECOND

/** Drill-down detail reads change rarely; let them sit longer. */
export const DRILLDOWN_STALE_MS = 60 * SECOND

/** Per-query cache tuning. `refetchInterval` is set only on live panels. */
export interface QueryConfig {
  staleTime: number
  refetchInterval?: number
}

/** Static, exact-match endpoints → cache config. */
export const STATIC_QUERY_CONFIG: Record<ApiContractKey, QueryConfig> = {
  // LIVE: the active-run panel polls on an interval.
  loop: { staleTime: 2 * SECOND, refetchInterval: LIVE_REFETCH_MS },
  // LIVE: the PLAN_AW / WORK_AW pillars poll so a fresh agent appears active.
  agents: { staleTime: 2 * SECOND, refetchInterval: LIVE_REFETCH_MS },
  // Persisted reads — fresh enough for 30s, no interval (idle-resilient).
  loopAnalytics: { staleTime: DEFAULT_STALE_MS },
  loopHealth: { staleTime: DEFAULT_STALE_MS },
  health: { staleTime: DEFAULT_STALE_MS },
  quality: { staleTime: DEFAULT_STALE_MS },
  cost: { staleTime: DEFAULT_STALE_MS },
  authoring: { staleTime: DEFAULT_STALE_MS },
  contextTax: { staleTime: DEFAULT_STALE_MS },
  scaling: { staleTime: DEFAULT_STALE_MS },
  outcomes: { staleTime: DEFAULT_STALE_MS },
  evals: { staleTime: DEFAULT_STALE_MS },
  alerts: { staleTime: DEFAULT_STALE_MS },
  kb: { staleTime: DEFAULT_STALE_MS },
  kbProjects: { staleTime: DEFAULT_STALE_MS },
  kbLearnings: { staleTime: DEFAULT_STALE_MS },
  features: { staleTime: DEFAULT_STALE_MS },
}

/** Parameterized drill-down endpoints → cache config. */
export const PARAM_QUERY_CONFIG: Record<ApiParamContractKey, QueryConfig> = {
  kbLearningDetail: { staleTime: DRILLDOWN_STALE_MS },
  kbTag: { staleTime: DRILLDOWN_STALE_MS },
  tasks: { staleTime: DEFAULT_STALE_MS },
  trace: { staleTime: DEFAULT_STALE_MS },
  failures: { staleTime: DEFAULT_STALE_MS },
  assessments: { staleTime: DRILLDOWN_STALE_MS },
}

/**
 * Query-key factory. A static key is `['api', <endpoint>]`; a drill-down key is
 * `['api', <endpoint>, <param>]`. The `as const` tuples make the keys readonly
 * and structurally comparable for invalidation/prefetch.
 */
export const queryKeys = {
  static: <K extends ApiContractKey>(key: K) => ['api', key] as const,
  param: <K extends ApiParamContractKey>(key: K, param: string | undefined) =>
    ['api', key, param ?? null] as const,
} as const

/** Exhaustiveness guards: the config maps must cover every contract entry. */
export const STATIC_ENDPOINT_KEYS = Object.keys(
  API_CONTRACT,
) as ApiContractKey[]
export const PARAM_ENDPOINT_KEYS = Object.keys(
  API_PARAM_CONTRACT,
) as ApiParamContractKey[]
