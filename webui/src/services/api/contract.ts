/**
 * agentware dashboard — /api/* contract (Task 13).
 *
 * Single source of truth for the read-only dashboard API: a zod schema per
 * endpoint with inferred TS types, so the SPA validates every payload at the
 * boundary (runtime rejection of malformed data) and gets fully typed data.
 *
 * The backend (scripts/agentware_dashboard.py) adds fields ADDITIVELY and never
 * removes them, so object schemas that the panels render use `.passthrough()` to
 * tolerate new backend fields without a breaking parse. Dynamic-key maps
 * (per-model / per-day / per-feature / per-phase rollups) use `z.record`.
 *
 * Recorded SYNTHETIC fixtures for each route live under `webui/src/fixtures/`
 * (built from a synthetic KB via scripts — never the operator's real data,
 * R-LOC-03) so the SPA is independently runnable before/without the live backend.
 *
 * Endpoints added by later panels (loop-analytics, trace, alerts, failures,
 * evals, assessments — plan Tasks 28–33) extend this contract in those tasks.
 */
import { z } from 'zod'

/* -------------------------------------------------------------------------- */
/* Shared primitives                                                          */
/* -------------------------------------------------------------------------- */

/** The four token-usage keys parsed from transcripts (cmd_metrics `_USAGE_KEYS`). */
export const UsageSchema = z
  .object({
    input_tokens: z.number(),
    output_tokens: z.number(),
    cache_creation_input_tokens: z.number(),
    cache_read_input_tokens: z.number(),
  })
  .passthrough()
export type Usage = z.infer<typeof UsageSchema>

/** A cost rollup bucket (by_model / by_day / by_feature / by_phase). */
export const CostRecordSchema = z
  .object({
    tokens: UsageSchema,
    total_tokens: z.number(),
    cost_usd: z.number(),
    session_count: z.number(),
    cache_read_ratio: z.number(),
  })
  .passthrough()
export type CostRecord = z.infer<typeof CostRecordSchema>

/** A knowledge-base index entry (projected; the full record carries extras). */
export const KbEntrySchema = z
  .object({
    id: z.string().nullable().optional(),
    title: z.string().nullable().optional(),
    category: z.string().nullable().optional(),
    path: z.string().nullable().optional(),
    summary: z.string().nullable().optional(),
    tags: z.array(z.string()).optional(),
  })
  .passthrough()
export type KbEntry = z.infer<typeof KbEntrySchema>

/** Knowledge-dir-unconfigured fallback fields, merged into every payload shape. */
export const NoKdirShape = {
  error: z.string().optional(),
  available: z.boolean().optional(),
}

/* -------------------------------------------------------------------------- */
/* /api/health                                                                */
/* -------------------------------------------------------------------------- */

export const HealthCheckSchema = z
  .object({
    name: z.string(),
    ok: z.boolean(),
    details: z.array(z.string()),
  })
  .passthrough()
export type HealthCheck = z.infer<typeof HealthCheckSchema>

export const HealthResponseSchema = z
  .object({
    ok: z.boolean().optional(),
    checks: z.array(HealthCheckSchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type HealthResponse = z.infer<typeof HealthResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/quality                                                               */
/* -------------------------------------------------------------------------- */

export const LedgerRowSchema = z
  .object({
    run: z.string().nullable().optional(),
    commit: z.string().nullable().optional(),
    strategy: z.string().nullable().optional(),
    suite: z.string().nullable().optional(),
    reliability: z.number().nullable().optional(),
    corpus_size: z.number().nullable().optional(),
    metrics: z.record(z.number().nullable()).default({}),
  })
  .passthrough()
export type LedgerRow = z.infer<typeof LedgerRowSchema>

export const QualityResponseSchema = z
  .object({
    ledger: z.array(LedgerRowSchema).default([]),
    series: z.array(LedgerRowSchema).default([]),
    count: z.number().default(0),
    latest: LedgerRowSchema.nullable().default(null),
    ...NoKdirShape,
  })
  .passthrough()
export type QualityResponse = z.infer<typeof QualityResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/loop                                                                  */
/* -------------------------------------------------------------------------- */

/** Terminal-outcome record (derive_outcome). */
export const OutcomeSchema = z
  .object({
    outcome: z.string(),
    source: z.string().optional(),
    iterations_used: z.number().nullable().optional(),
    self_heal_count: z.number().nullable().optional(),
    signals: z.record(z.unknown()).optional(),
  })
  .passthrough()
export type Outcome = z.infer<typeof OutcomeSchema>

/** One line of the loop emission channel (logs/metrics.jsonl). */
export const LoopEventSchema = z
  .object({
    ts: z.string().optional(),
    feature: z.string().optional(),
    event: z.string().optional(),
    stage: z.string().optional(),
    phase: z.string().optional(),
    iteration: z.number().optional(),
    max: z.number().optional(),
    tasks_total: z.number().optional(),
    tasks_remaining: z.number().optional(),
    tasks_done_delta: z.number().optional(),
    tasks_done: z.number().optional(),
    promise_status: z.string().optional(),
    result: z.string().optional(),
    phase_wall_s: z.number().optional(),
    self_heal_count: z.number().optional(),
    outcome: z.string().optional(),
    iterations_used: z.number().optional(),
    task: z.string().optional(),
    from: z.string().optional(),
    to: z.string().optional(),
    approx: z.boolean().optional(),
  })
  .passthrough()
export type LoopEvent = z.infer<typeof LoopEventSchema>

export const LoopFeatureStateSchema = z
  .object({
    feature: z.string(),
    iteration: z.number().nullable(),
    done: z.boolean(),
    tasks_open: z.number(),
    tasks_done: z.number(),
    tasks_total: z.number(),
    outcome: OutcomeSchema.nullable(),
    event_count: z.number(),
    last_event: LoopEventSchema.nullable(),
    last_ts: z.string().nullable(),
  })
  .passthrough()
export type LoopFeatureState = z.infer<typeof LoopFeatureStateSchema>

export const LoopResponseSchema = z
  .object({
    features: z.array(LoopFeatureStateSchema).default([]),
    active: z.string().nullable().default(null),
    recent_events: z.array(LoopEventSchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type LoopResponse = z.infer<typeof LoopResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/loop-analytics                                                        */
/* -------------------------------------------------------------------------- */

/** One point on the tasks-remaining burndown across MAIN-loop iterations. */
export const BurndownPointSchema = z
  .object({
    iteration: z.number(),
    tasks_remaining: z.number().nullable(),
    tasks_done_delta: z.number().default(0),
  })
  .passthrough()
export type BurndownPoint = z.infer<typeof BurndownPointSchema>

/** A pre/post-hook gate outcome emitted by a loop phase. */
export const LoopGateSchema = z
  .object({
    iteration: z.number().nullable().optional(),
    result: z.string().nullable().optional(),
    promise_status: z.string().nullable().optional(),
    ts: z.string().nullable().optional(),
    ok: z.boolean().optional(),
  })
  .passthrough()
export type LoopGate = z.infer<typeof LoopGateSchema>

/** Per-feature loop analytics (derive_loop_analytics). */
export const LoopAnalyticsFeatureSchema = z
  .object({
    feature: z.string(),
    outcome: z.string().default('unknown'),
    iterations_to_completion: z.number().nullable().default(0),
    max_iterations: z.number().nullable().optional(),
    max_iteration_utilization: z.number().nullable().optional(),
    iteration_efficiency: z.number().nullable().optional(),
    tasks_closed: z.number().default(0),
    tasks_total: z.number().nullable().optional(),
    tasks_done: z.number().nullable().optional(),
    self_heal_count: z.number().default(0),
    promise_status: z.string().nullable().optional(),
    latency_s: z.number().nullable().optional(),
    burndown: z.array(BurndownPointSchema).default([]),
    phase_wall_s: z.record(z.number()).default({}),
    phase_tokens: z.record(z.number()).default({}),
    gates: z
      .object({
        pre: z.array(LoopGateSchema).default([]),
        post: z.array(LoopGateSchema).default([]),
      })
      .passthrough()
      .default({ pre: [], post: [] }),
    event_count: z.number().default(0),
  })
  .passthrough()
export type LoopAnalyticsFeature = z.infer<typeof LoopAnalyticsFeatureSchema>

export const LoopThroughputSchema = z
  .object({
    by_day: z.record(z.number()).default({}),
    by_week: z.record(z.number()).default({}),
    completed_total: z.number().default(0),
  })
  .passthrough()
export type LoopThroughput = z.infer<typeof LoopThroughputSchema>

export const LoopAnalyticsResponseSchema = z
  .object({
    features: z.array(LoopAnalyticsFeatureSchema).default([]),
    throughput: LoopThroughputSchema.partial().default({}),
    ...NoKdirShape,
  })
  .passthrough()
export type LoopAnalyticsResponse = z.infer<typeof LoopAnalyticsResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/loop-health                                                           */
/* -------------------------------------------------------------------------- */

/** OK / at-risk / critical — the loop-health badge severity (Task 30). */
export const LoopHealthStatusSchema = z.enum(['ok', 'at_risk', 'critical'])
export type LoopHealthStatus = z.infer<typeof LoopHealthStatusSchema>

/** One runaway-detection check (duplicate calls, no-progress, burn, context). */
export const LoopHealthCheckSchema = z
  .object({
    name: z.string(),
    status: LoopHealthStatusSchema,
    flagged: z.boolean().default(false),
    detail: z.string().nullable().optional(),
    tool: z.string().nullable().optional(),
    iteration: z.number().nullable().optional(),
  })
  .passthrough()
export type LoopHealthCheck = z.infer<typeof LoopHealthCheckSchema>

/** The offending tool/iteration the badge points at (null when healthy). */
export const LoopHealthOffenderSchema = z
  .object({
    check: z.string(),
    tool: z.string().nullable().optional(),
    iteration: z.number().nullable().optional(),
    detail: z.string().nullable().optional(),
  })
  .passthrough()
export type LoopHealthOffender = z.infer<typeof LoopHealthOffenderSchema>

/** Per-feature loop-health (derive_loop_health for one feature). */
export const LoopHealthFeatureSchema = z
  .object({
    feature: z.string(),
    status: LoopHealthStatusSchema,
    checks: z.record(LoopHealthCheckSchema).default({}),
    flagged_checks: z.array(z.string()).default([]),
    offender: LoopHealthOffenderSchema.nullable().default(null),
    outcome: z.string().nullable().optional(),
  })
  .passthrough()
export type LoopHealthFeature = z.infer<typeof LoopHealthFeatureSchema>

export const LoopHealthResponseSchema = z
  .object({
    features: z.array(LoopHealthFeatureSchema).default([]),
    summary: z
      .object({
        ok: z.number().default(0),
        at_risk: z.number().default(0),
        critical: z.number().default(0),
      })
      .passthrough()
      .default({ ok: 0, at_risk: 0, critical: 0 }),
    status: LoopHealthStatusSchema.default('ok'),
    ...NoKdirShape,
  })
  .passthrough()
export type LoopHealthResponse = z.infer<typeof LoopHealthResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/cost                                                                  */
/* -------------------------------------------------------------------------- */

export const ContextTaxDaySchema = z
  .object({
    turns: z.number(),
    cache_read_input_tokens: z.number(),
    peak_input_tokens: z.number(),
    cache_read_per_turn: z.number(),
    context_window_pct: z.number(),
    truncation_risk: z.boolean(),
  })
  .passthrough()
export type ContextTaxDay = z.infer<typeof ContextTaxDaySchema>

export const ContextTaxSchema = z
  .object({
    injected_tokens: z.number(),
    main_md_bytes: z.number(),
    cache_read_per_turn: z.number(),
    context_window: z.number(),
    truncation_threshold: z.number(),
    peak_input_tokens: z.number(),
    context_window_pct: z.number(),
    truncation_risk: z.boolean(),
    by_day: z.record(ContextTaxDaySchema).default({}),
  })
  .passthrough()
export type ContextTax = z.infer<typeof ContextTaxSchema>

export const AuthoringSchema = z
  .object({
    wall_s: z.number(),
    tokens: z.number(),
    session_count: z.number(),
    sessions: z.array(z.string()),
  })
  .passthrough()
export type Authoring = z.infer<typeof AuthoringSchema>

export const PhaseCostsSchema = z
  .object({
    by_phase: z.record(CostRecordSchema).default({}),
    total_tokens: z.number(),
    cost_usd: z.number(),
  })
  .passthrough()
export type PhaseCosts = z.infer<typeof PhaseCostsSchema>

/** A per-session metric row (parse_session + apply_pricing annotations). */
export const SessionRowSchema = z
  .object({
    session_id: z.string(),
    feature: z.string().nullable().optional(),
    stage: z.string().nullable().optional(),
    date: z.string().nullable().optional(),
    start: z.string().nullable().optional(),
    end: z.string().nullable().optional(),
    turns: z.number().optional(),
    tool_calls: z.number().optional(),
    duration_seconds: z.number().optional(),
    tokens: UsageSchema.optional(),
    total_tokens: z.number().optional(),
    cost_usd: z.number().optional(),
    cache_read_ratio: z.number().optional(),
    models: z.record(z.unknown()).optional(),
  })
  .passthrough()
export type SessionRow = z.infer<typeof SessionRowSchema>

/** The cmd_metrics aggregate (shared by /api/cost). Documented fields typed; the
 *  block stays open for additive backend fields. */
export const MetricsAggregateSchema = z
  .object({
    session_count: z.number(),
    turns: z.number(),
    tool_calls: z.number(),
    subagent_count: z.number(),
    duration_seconds: z.number(),
    tokens: UsageSchema,
    total_tokens: z.number(),
    tools: z.record(z.number()).default({}),
    cost_usd: z.number().optional(),
    cache_read_ratio: z.number().optional(),
    cost_anomaly_dates: z.array(z.string()).optional(),
    by_model: z.record(CostRecordSchema).optional(),
    by_day: z.record(CostRecordSchema).optional(),
    by_feature: z.record(CostRecordSchema).optional(),
    authoring: AuthoringSchema.optional(),
    context_tax: ContextTaxSchema.optional(),
    phase_costs: PhaseCostsSchema.optional(),
  })
  .passthrough()
export type MetricsAggregate = z.infer<typeof MetricsAggregateSchema>

export const CostResponseSchema = z
  .object({
    session_count: z.number().default(0),
    sessions: z.array(SessionRowSchema).default([]),
    aggregate: MetricsAggregateSchema.partial().default({}),
    ...NoKdirShape,
  })
  .passthrough()
export type CostResponse = z.infer<typeof CostResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/authoring                                                             */
/* -------------------------------------------------------------------------- */

export const AuthoringResponseSchema = z
  .object({
    authoring: AuthoringSchema.partial().default({}),
    session_count: z.number().default(0),
    ...NoKdirShape,
  })
  .passthrough()
export type AuthoringResponse = z.infer<typeof AuthoringResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/context-tax                                                           */
/* -------------------------------------------------------------------------- */

export const ContextTaxResponseSchema = z
  .object({
    context_tax: ContextTaxSchema.partial().default({}),
    session_count: z.number().default(0),
    ...NoKdirShape,
  })
  .passthrough()
export type ContextTaxResponse = z.infer<typeof ContextTaxResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/scaling                                                               */
/* -------------------------------------------------------------------------- */

export const ScalingPointSchema = z
  .object({
    corpus_size: z.number().nullable(),
    recall_at_k: z.number().nullable(),
    commit: z.string().nullable().optional(),
    run: z.string().nullable().optional(),
    strategy: z.string().nullable().optional(),
  })
  .passthrough()
export type ScalingPoint = z.infer<typeof ScalingPointSchema>

export const ScalingResponseSchema = z
  .object({
    points: z.array(ScalingPointSchema).default([]),
    slope: z.number().nullable().default(null),
    count: z.number().default(0),
    measured: z.number().default(0),
    ...NoKdirShape,
  })
  .passthrough()
export type ScalingResponse = z.infer<typeof ScalingResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/outcomes                                                              */
/* -------------------------------------------------------------------------- */

export const OutcomeRowSchema = OutcomeSchema.extend({
  feature: z.string(),
})
export type OutcomeRow = z.infer<typeof OutcomeRowSchema>

export const OutcomesResponseSchema = z
  .object({
    features: z.array(OutcomeRowSchema).default([]),
    summary: z.record(z.number()).default({}),
    ...NoKdirShape,
  })
  .passthrough()
export type OutcomesResponse = z.infer<typeof OutcomesResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/kb (+ drill-downs)                                                     */
/* -------------------------------------------------------------------------- */

export const KbResponseSchema = z
  .object({
    entry_count: z.number().default(0),
    categories: z.record(z.number()).default({}),
    category_count: z.number().optional(),
    tag_count: z.number().optional(),
    entries: z.array(KbEntrySchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type KbResponse = z.infer<typeof KbResponseSchema>

export const KbCategoryResponseSchema = z
  .object({
    category: z.string(),
    count: z.number().default(0),
    entries: z.array(KbEntrySchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type KbCategoryResponse = z.infer<typeof KbCategoryResponseSchema>

export const KbLearningDetailResponseSchema = z
  .object({
    entry: KbEntrySchema.optional(),
    body: z.string().nullable().optional(),
    id: z.string().optional(),
    ...NoKdirShape,
  })
  .passthrough()
export type KbLearningDetailResponse = z.infer<typeof KbLearningDetailResponseSchema>

export const KbTagResponseSchema = z
  .object({
    tag: z.string(),
    count: z.number().default(0),
    entries: z.array(KbEntrySchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type KbTagResponse = z.infer<typeof KbTagResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/features                                                              */
/* -------------------------------------------------------------------------- */

export const FeaturesResponseSchema = z
  .object({
    categories: z.record(z.array(KbEntrySchema)).default({}),
    category_count: z.number().optional(),
    entry_count: z.number().default(0),
    ...NoKdirShape,
  })
  .passthrough()
export type FeaturesResponse = z.infer<typeof FeaturesResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/tasks/<feature>                                                       */
/* -------------------------------------------------------------------------- */

export const TaskTransitionSchema = z
  .object({
    event: z.string().optional(),
    ts: z.string().optional(),
    feature: z.string().optional(),
    stage: z.string().optional(),
    iteration: z.number().optional(),
    task: z.string().optional(),
    from: z.string().optional(),
    to: z.string().optional(),
    approx: z.boolean().optional(),
  })
  .passthrough()
export type TaskTransition = z.infer<typeof TaskTransitionSchema>

export const TasksResponseSchema = z
  .object({
    feature: z.string(),
    transitions: z.array(TaskTransitionSchema).default([]),
    transition_count: z.number().default(0),
    plan: z
      .object({
        open: z.number(),
        done: z.number(),
        total: z.number(),
      })
      .passthrough()
      .optional(),
    ...NoKdirShape,
  })
  .passthrough()
export type TasksResponse = z.infer<typeof TasksResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/trace/<session|feature>                                               */
/* -------------------------------------------------------------------------- */

/** One tool-call step in a run trace (derive_trace). */
export const TraceStepSchema = z
  .object({
    index: z.number(),
    session_id: z.string().optional(),
    ts: z.string().nullable().optional(),
    tool: z.string(),
    status: z.string().default('ok'),
    args: z.string().default(''),
    args_truncated: z.boolean().optional(),
    result: z.string().default(''),
    result_truncated: z.boolean().optional(),
    tokens: z.number().nullable().optional(),
    duration_s: z.number().nullable().optional(),
    iteration: z.number().nullable().optional(),
  })
  .passthrough()
export type TraceStep = z.infer<typeof TraceStepSchema>

/** A marker/decision transition attached to its loop iteration. */
export const TraceTransitionSchema = z
  .object({
    ts: z.string().nullable().optional(),
    task: z.string().nullable().optional(),
    from: z.string().nullable().optional(),
    to: z.string().nullable().optional(),
    approx: z.boolean().nullable().optional(),
  })
  .passthrough()
export type TraceTransition = z.infer<typeof TraceTransitionSchema>

/** Steps + transitions grouped under one loop iteration (null = ungrouped). */
export const TraceIterationSchema = z
  .object({
    iteration: z.number().nullable(),
    step_count: z.number().default(0),
    steps: z.array(TraceStepSchema).default([]),
    transitions: z.array(TraceTransitionSchema).default([]),
  })
  .passthrough()
export type TraceIteration = z.infer<typeof TraceIterationSchema>

export const TraceSessionMetaSchema = z
  .object({
    session_id: z.string(),
    step_count: z.number().default(0),
  })
  .passthrough()
export type TraceSessionMeta = z.infer<typeof TraceSessionMetaSchema>

export const TraceResponseSchema = z
  .object({
    scope: z.string().optional(),
    session: z.string().nullable().optional(),
    feature: z.string().nullable().optional(),
    sessions: z.array(TraceSessionMetaSchema).default([]),
    step_count: z.number().default(0),
    err_count: z.number().default(0),
    tool_summary: z.record(z.number()).default({}),
    truncated: z.boolean().optional(),
    iterations: z.array(TraceIterationSchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type TraceResponse = z.infer<typeof TraceResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/alerts                                                                 */
/* -------------------------------------------------------------------------- */

/** Alert severity — info < warning < critical (color = MEANING). */
export const AlertSeveritySchema = z.enum(['info', 'warning', 'critical'])
export type AlertSeverity = z.infer<typeof AlertSeveritySchema>

/** The symptom class an alert belongs to (drives its icon + deep-link). */
export const AlertCategorySchema = z.enum([
  'regression',
  'scaling',
  'cost',
  'loop',
  'kb_stale',
  'kb_conflict',
  'unpromoted',
])
export type AlertCategory = z.infer<typeof AlertCategorySchema>

/** One symptom-based alert (derive_alerts). Extra per-class fields pass through. */
export const AlertSchema = z
  .object({
    id: z.string(),
    category: AlertCategorySchema,
    severity: AlertSeveritySchema,
    title: z.string(),
    detail: z.string().default(''),
    feature: z.string().nullable().default(null),
    deep_link: z.string().nullable().default(null),
  })
  .passthrough()
export type Alert = z.infer<typeof AlertSchema>

/** A ledger commit marker for the trend charts (commit + headline metrics). */
export const CommitMarkerSchema = z
  .object({
    commit: z.string().nullable().optional(),
    run: z.string().nullable().optional(),
    strategy: z.string().nullable().optional(),
    reliability: z.number().nullable().optional(),
    recall_at_k: z.number().nullable().optional(),
  })
  .passthrough()
export type CommitMarker = z.infer<typeof CommitMarkerSchema>

export const AlertsResponseSchema = z
  .object({
    alerts: z.array(AlertSchema).default([]),
    summary: z
      .object({
        critical: z.number().default(0),
        warning: z.number().default(0),
        info: z.number().default(0),
      })
      .passthrough()
      .default({ critical: 0, warning: 0, info: 0 }),
    open_count: z.number().default(0),
    status: AlertSeveritySchema.or(z.literal('ok')).default('ok'),
    commit_markers: z.array(CommitMarkerSchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type AlertsResponse = z.infer<typeof AlertsResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/failures/<feature>                                                     */
/* -------------------------------------------------------------------------- */

/** Per-kind worklog marker tally (total + still-unpromoted). */
export const FailureMarkerTallySchema = z
  .object({
    total: z.number().default(0),
    unpromoted: z.number().default(0),
  })
  .passthrough()
export type FailureMarkerTally = z.infer<typeof FailureMarkerTallySchema>

/** Failure-ladder & error-recovery for one feature (derive_failures, Task 32). */
export const FailuresResponseSchema = z
  .object({
    feature: z.string().nullable().optional(),
    scope: z.string().default('feature'),
    step_count: z.number().default(0),
    err_count: z.number().default(0),
    err_rate: z.number().default(0),
    err_by_tool: z.record(z.number()).default({}),
    /** R-FAIL ladder tier usage: kb / reasoning / inputs / switch / web. */
    ladder: z.record(z.number()).default({}),
    ladder_order: z.array(z.string()).default([]),
    unrecovered: z.number().default(0),
    web_search_count: z.number().default(0),
    kb_lookup_count: z.number().default(0),
    self_heal_count: z.number().default(0),
    markers: z
      .object({
        learned: FailureMarkerTallySchema.default({ total: 0, unpromoted: 0 }),
        decision: FailureMarkerTallySchema.default({ total: 0, unpromoted: 0 }),
      })
      .passthrough()
      .default({
        learned: { total: 0, unpromoted: 0 },
        decision: { total: 0, unpromoted: 0 },
      }),
    tool_summary: z.record(z.number()).default({}),
    sessions: z.array(TraceSessionMetaSchema).default([]),
    ...NoKdirShape,
  })
  .passthrough()
export type FailuresResponse = z.infer<typeof FailuresResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/evals + /api/assessments/<feature>  (Evaluation & quality, Task 33)    */
/* -------------------------------------------------------------------------- */

/** One eval-ledger run: reliability + the retrieval metrics flattened up. */
export const EvalRunSchema = z
  .object({
    run: z.string().nullable().optional(),
    commit: z.string().nullable().optional(),
    strategy: z.string().nullable().optional(),
    suite: z.string().nullable().optional(),
    mode: z.string().nullable().optional(),
    reliability: z.number().nullable().optional(),
    recall_at_k: z.number().nullable().optional(),
    ndcg_at_k: z.number().nullable().optional(),
    mrr: z.number().nullable().optional(),
    precision_at_k: z.number().nullable().optional(),
    corpus_size: z.number().nullable().optional(),
  })
  .passthrough()
export type EvalRun = z.infer<typeof EvalRunSchema>

/** One ACR-gate decision row (split out of the eval series). */
export const AcrRowSchema = z
  .object({
    run: z.string().nullable().optional(),
    commit: z.string().nullable().optional(),
    decided_strategy: z.string().nullable().optional(),
    passed: z.boolean().nullable().optional(),
    checks: z.record(z.unknown()).default({}),
  })
  .passthrough()
export type AcrRow = z.infer<typeof AcrRowSchema>

/** `/api/evals` — eval-ledger trend split from ACR-gate decisions (Task 33). */
export const EvalsResponseSchema = z
  .object({
    series: z.array(EvalRunSchema).default([]),
    acr: z.array(AcrRowSchema).default([]),
    count: z.number().default(0),
    acr_count: z.number().default(0),
    latest: EvalRunSchema.nullable().default(null),
    latest_acr: AcrRowSchema.nullable().default(null),
    ...NoKdirShape,
  })
  .passthrough()
export type EvalsResponse = z.infer<typeof EvalsResponseSchema>

/** `/api/assessments/<feature>` — the post-phase self-assessment text (Task 33). */
export const AssessmentResponseSchema = z
  .object({
    feature: z.string().nullable().optional(),
    exists: z.boolean().default(false),
    text: z.string().default(''),
    bytes: z.number().default(0),
    path: z.string().nullable().default(null),
    ...NoKdirShape,
  })
  .passthrough()
export type AssessmentResponse = z.infer<typeof AssessmentResponseSchema>

/* -------------------------------------------------------------------------- */
/* /api/agents — PLAN_AW / WORK_AW per-agent activity                          */
/* -------------------------------------------------------------------------- */

/** One persisted agent session (planner or worker), render-ready projection. */
export const AgentSessionSchema = z
  .object({
    session_id: z.string().nullable().optional(),
    stage: z.string().nullable().optional(),
    // Resolved identity (action-based): the honest display name, the agent kind,
    // whether the feature is confidently attributed, and the run's terminal state.
    kind: z.string().nullable().optional(),
    name: z.string().nullable().optional(),
    confidence: z.string().nullable().optional(), // high | provisional | pending
    complete: z.boolean().optional(),
    status: z.string().nullable().optional(), // active | complete | ended | incomplete
    feature: z.string().nullable().optional(),
    start: z.string().nullable().optional(),
    end: z.string().nullable().optional(),
    date: z.string().nullable().optional(),
    turns: z.number().default(0),
    main_turns: z.number().default(0),
    subagent_turns: z.number().default(0),
    tool_calls: z.number().default(0),
    subagent_count: z.number().default(0),
    duration_seconds: z.number().default(0),
    total_tokens: z.number().default(0),
    peak_input_tokens: z.number().default(0),
    cache_read_ratio: z.number().default(0),
    cost_usd: z.number().default(0),
    models: z.array(z.string()).default([]),
  })
  .passthrough()
export type AgentSession = z.infer<typeof AgentSessionSchema>

/** A per-day or per-feature activity bucket. */
export const AgentBucketSchema = z
  .object({
    sessions: z.number().default(0),
    total_tokens: z.number().default(0),
    cost_usd: z.number().default(0),
  })
  .passthrough()

/** One agent's full activity rollup (active marker + history + aggregates). */
export const AgentActivitySchema = z
  .object({
    kind: z.string().nullable().optional(),
    stages: z.array(z.string()).default([]),
    active: z.boolean().default(false),
    active_session: AgentSessionSchema.nullable().default(null),
    session_count: z.number().default(0),
    attributed_count: z.number().default(0),
    incomplete_count: z.number().default(0),
    aggregate: z
      .object({
        total_tokens: z.number().default(0),
        cost_usd: z.number().default(0),
        turns: z.number().default(0),
        tool_calls: z.number().default(0),
        subagent_count: z.number().default(0),
        duration_seconds: z.number().default(0),
        cache_read_ratio: z.number().default(0),
        tokens: UsageSchema.optional(),
      })
      .passthrough()
      .default({}),
    by_day: z.record(AgentBucketSchema).default({}),
    by_feature: z.record(AgentBucketSchema).default({}),
    sessions: z.array(AgentSessionSchema).default([]),
    features: z.array(z.string()).default([]),
  })
  .passthrough()
export type AgentActivity = z.infer<typeof AgentActivitySchema>

/** One authored plan — the PLANNER's output (see derive_plan_authoring). */
export const AuthoredPlanSchema = z
  .object({
    feature: z.string(),
    authored: z.string().nullable().optional(),
    authored_session: z.string().nullable().optional(),
    tasks_total: z.number().default(0),
    tasks_done: z.number().default(0),
    complete: z.boolean().optional(),
    status: z.string().nullable().optional(), // complete | in_progress | open
  })
  .passthrough()
export type AuthoredPlan = z.infer<typeof AuthoredPlanSchema>

export const AgentsResponseSchema = z
  .object({
    plan: AgentActivitySchema.extend({
      plans: z.array(AuthoredPlanSchema).default([]),
    }),
    work: AgentActivitySchema,
    ...NoKdirShape,
  })
  .passthrough()
export type AgentsResponse = z.infer<typeof AgentsResponseSchema>

/* -------------------------------------------------------------------------- */
/* Route registry — the canonical map of every /api/* route to its schema +    */
/* recorded synthetic fixture. The typed API client (Task 16) parses each      */
/* response with `schema`; tests validate each `fixture` against its `schema`.  */
/* -------------------------------------------------------------------------- */

/** Static, exact-match endpoints. */
export const API_CONTRACT = {
  health: { route: '/api/health', fixture: 'health', schema: HealthResponseSchema },
  quality: { route: '/api/quality', fixture: 'quality', schema: QualityResponseSchema },
  loop: { route: '/api/loop', fixture: 'loop', schema: LoopResponseSchema },
  agents: { route: '/api/agents', fixture: 'agents', schema: AgentsResponseSchema },
  loopAnalytics: {
    route: '/api/loop-analytics',
    fixture: 'loopAnalytics',
    schema: LoopAnalyticsResponseSchema,
  },
  loopHealth: {
    route: '/api/loop-health',
    fixture: 'loopHealth',
    schema: LoopHealthResponseSchema,
  },
  cost: { route: '/api/cost', fixture: 'cost', schema: CostResponseSchema },
  authoring: { route: '/api/authoring', fixture: 'authoring', schema: AuthoringResponseSchema },
  contextTax: { route: '/api/context-tax', fixture: 'contextTax', schema: ContextTaxResponseSchema },
  scaling: { route: '/api/scaling', fixture: 'scaling', schema: ScalingResponseSchema },
  outcomes: { route: '/api/outcomes', fixture: 'outcomes', schema: OutcomesResponseSchema },
  evals: { route: '/api/evals', fixture: 'evals', schema: EvalsResponseSchema },
  alerts: { route: '/api/alerts', fixture: 'alerts', schema: AlertsResponseSchema },
  kb: { route: '/api/kb', fixture: 'kb', schema: KbResponseSchema },
  kbProjects: { route: '/api/kb/projects', fixture: 'kbProjects', schema: KbCategoryResponseSchema },
  kbLearnings: { route: '/api/kb/learnings', fixture: 'kbLearnings', schema: KbCategoryResponseSchema },
  features: { route: '/api/features', fixture: 'features', schema: FeaturesResponseSchema },
} as const

/**
 * Parameterized drill-down endpoints. `path(param)` builds the concrete route;
 * `fixture` is the recorded synthetic example.
 */
export const API_PARAM_CONTRACT = {
  kbLearningDetail: {
    path: (id: string) => `/api/kb/learnings/${encodeURIComponent(id)}`,
    fixture: 'kbLearningDetail',
    schema: KbLearningDetailResponseSchema,
  },
  kbTag: {
    path: (tag: string) => `/api/kb/tags/${encodeURIComponent(tag)}`,
    fixture: 'kbTag',
    schema: KbTagResponseSchema,
  },
  tasks: {
    path: (feature: string) => `/api/tasks/${encodeURIComponent(feature)}`,
    fixture: 'tasks',
    schema: TasksResponseSchema,
  },
  trace: {
    path: (target: string) => `/api/trace/${encodeURIComponent(target)}`,
    fixture: 'trace',
    schema: TraceResponseSchema,
  },
  failures: {
    path: (feature: string) => `/api/failures/${encodeURIComponent(feature)}`,
    fixture: 'failures',
    schema: FailuresResponseSchema,
  },
  assessments: {
    path: (feature: string) => `/api/assessments/${encodeURIComponent(feature)}`,
    fixture: 'assessments',
    schema: AssessmentResponseSchema,
  },
} as const

export type ApiContractKey = keyof typeof API_CONTRACT
export type ApiParamContractKey = keyof typeof API_PARAM_CONTRACT
