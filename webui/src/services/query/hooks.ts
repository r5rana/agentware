/**
 * Typed TanStack Query hooks — one per /api/* endpoint (Task 17).
 *
 * Each hook wraps the typed, zod-validated client method from `services/api`
 * (so the data is schema-validated at the boundary) with a centralized query key
 * + stale time from `keys.ts`. The live `/api/loop` hook additionally carries a
 * `refetchInterval`, so the execution panel polls while a run is in flight; every
 * other endpoint reads persisted data and stays idle-resilient (no interval).
 *
 * The hooks forward TanStack's `AbortSignal` into the client fetcher, so a query
 * that unmounts or is superseded cancels its in-flight request.
 */
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { api } from '@/services/api/client'
import type {
  ApiContractKey,
  ApiParamContractKey,
} from '@/services/api/contract'
import { PARAM_QUERY_CONFIG, queryKeys, STATIC_QUERY_CONFIG } from './keys'

/** Per-call overrides a panel may pass (e.g. pause polling, gate on a tab). */
export interface QueryOverrides {
  /** Disable the query (e.g. until a drill-down param is chosen). */
  enabled?: boolean
  /** Override the polling interval (`false` stops the live poll). */
  refetchInterval?: number | false
}

/** Inferred data type for a static endpoint, straight from its client method. */
type StaticData<K extends ApiContractKey> = Awaited<ReturnType<(typeof api)[K]>>
/** Inferred data type for a parameterized drill-down endpoint. */
type ParamData<K extends ApiParamContractKey> = Awaited<
  ReturnType<(typeof api)[K]>
>

/** Shared factory for a static, exact-match endpoint query. */
function useStaticEndpoint<K extends ApiContractKey>(
  key: K,
  overrides?: QueryOverrides,
): UseQueryResult<StaticData<K>> {
  const cfg = STATIC_QUERY_CONFIG[key]
  const fetcher = api[key] as (opts: {
    signal?: AbortSignal
  }) => Promise<StaticData<K>>
  return useQuery({
    queryKey: queryKeys.static(key),
    queryFn: ({ signal }) => fetcher({ signal }),
    staleTime: cfg.staleTime,
    refetchInterval: overrides?.refetchInterval ?? cfg.refetchInterval ?? false,
    enabled: overrides?.enabled,
  })
}

/** Shared factory for a parameterized drill-down query (disabled until param set). */
function useParamEndpoint<K extends ApiParamContractKey>(
  key: K,
  param: string | undefined,
  overrides?: QueryOverrides,
): UseQueryResult<ParamData<K>> {
  const cfg = PARAM_QUERY_CONFIG[key]
  const fetcher = api[key] as (
    param: string,
    opts: { signal?: AbortSignal },
  ) => Promise<ParamData<K>>
  const hasParam = param != null && param !== ''
  return useQuery({
    queryKey: queryKeys.param(key, param),
    queryFn: ({ signal }) => fetcher(param as string, { signal }),
    staleTime: cfg.staleTime,
    refetchInterval: overrides?.refetchInterval ?? cfg.refetchInterval ?? false,
    enabled: hasParam && (overrides?.enabled ?? true),
  })
}

/* -------------------------------------------------------------------------- */
/* Static endpoint hooks                                                       */
/* -------------------------------------------------------------------------- */

/** `/api/health` — audit health checks. */
export const useHealth = (o?: QueryOverrides) => useStaticEndpoint('health', o)
/** `/api/quality` — retrieval-quality ledger trend. */
export const useQuality = (o?: QueryOverrides) =>
  useStaticEndpoint('quality', o)
/** `/api/loop` — LIVE execution-loop state (interval-polled). */
export const useLoop = (o?: QueryOverrides) => useStaticEndpoint('loop', o)
/** `/api/agents` — LIVE PLAN_AW + WORK_AW per-agent activity (interval-polled). */
export const useAgents = (o?: QueryOverrides) => useStaticEndpoint('agents', o)
/** `/api/loop-analytics` — per-run phase split, burndown, gates + throughput. */
export const useLoopAnalytics = (o?: QueryOverrides) =>
  useStaticEndpoint('loopAnalytics', o)
/** `/api/loop-health` — runaway detection: dup calls, no-progress, burn, context. */
export const useLoopHealth = (o?: QueryOverrides) =>
  useStaticEndpoint('loopHealth', o)
/** `/api/cost` — cost attribution (by feature/day/model). */
export const useCost = (o?: QueryOverrides) => useStaticEndpoint('cost', o)
/** `/api/authoring` — plan-authoring size + attributed time. */
export const useAuthoring = (o?: QueryOverrides) =>
  useStaticEndpoint('authoring', o)
/** `/api/context-tax` — context re-read + injected-footprint series. */
export const useContextTax = (o?: QueryOverrides) =>
  useStaticEndpoint('contextTax', o)
/** `/api/scaling` — retrieval recall vs corpus size. */
export const useScaling = (o?: QueryOverrides) =>
  useStaticEndpoint('scaling', o)
/** `/api/outcomes` — terminal run outcomes + success rate. */
export const useOutcomes = (o?: QueryOverrides) =>
  useStaticEndpoint('outcomes', o)
/** `/api/evals` — eval-ledger quality trend split from ACR-gate decisions. */
export const useEvals = (o?: QueryOverrides) => useStaticEndpoint('evals', o)
/** `/api/alerts` — symptom-based, severity-ranked alerts + commit markers. */
export const useAlerts = (o?: QueryOverrides) => useStaticEndpoint('alerts', o)
/** `/api/kb` — knowledge-base summary. */
export const useKb = (o?: QueryOverrides) => useStaticEndpoint('kb', o)
/** `/api/kb/projects` — KB project entries. */
export const useKbProjects = (o?: QueryOverrides) =>
  useStaticEndpoint('kbProjects', o)
/** `/api/kb/learnings` — KB learning entries. */
export const useKbLearnings = (o?: QueryOverrides) =>
  useStaticEndpoint('kbLearnings', o)
/** `/api/features` — feature registry. */
export const useFeatures = (o?: QueryOverrides) =>
  useStaticEndpoint('features', o)

/* -------------------------------------------------------------------------- */
/* Parameterized drill-down hooks                                              */
/* -------------------------------------------------------------------------- */

/** `/api/kb/learnings/<id>` — a single learning's detail. */
export const useKbLearningDetail = (
  id: string | undefined,
  o?: QueryOverrides,
) => useParamEndpoint('kbLearningDetail', id, o)
/** `/api/kb/tags/<tag>` — entries carrying a tag. */
export const useKbTag = (tag: string | undefined, o?: QueryOverrides) =>
  useParamEndpoint('kbTag', tag, o)
/** `/api/tasks/<feature>` — per-task transition timeline for a feature. */
export const useTasks = (feature: string | undefined, o?: QueryOverrides) =>
  useParamEndpoint('tasks', feature, o)
/** `/api/trace/<session|feature>` — step-level run trace grouped by iteration. */
export const useTrace = (target: string | undefined, o?: QueryOverrides) =>
  useParamEndpoint('trace', target, o)
/** `/api/failures/<feature>` — failure-ladder & error-recovery for one feature. */
export const useFailures = (feature: string | undefined, o?: QueryOverrides) =>
  useParamEndpoint('failures', feature, o)
/** `/api/assessments/<feature>` — the post-phase self-assessment text. */
export const useAssessment = (feature: string | undefined, o?: QueryOverrides) =>
  useParamEndpoint('assessments', feature, o)
