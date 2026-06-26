import { useMemo, useState, type ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

import { LineTrend } from '@/components/charts'
import { StatTile } from '@/components/ui'
import type { EvalRun, EvalsResponse } from '@/services/api/contract'
import { useAssessment, useLoop } from '@/services/query'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { percent, shortSha } from '@/panels/format'

/**
 * EVALUATION & quality panel (Task 33) — quality, not just infra. It renders the
 * eval-ledger trend (Recall@k / nDCG / MRR / reliability across runs, split from
 * the ACR-gate decisions) from `/api/evals`, AND the post-phase self-ASSESSMENT
 * text for a picked feature from `/api/assessments/<feature>`, so quality drift
 * is visible alongside the trend. Part of the Memory + Health pillars.
 * Idle-resilient — both sources read persisted data; no active run required.
 */

/** Latest value of a metric on an eval run (0 when absent). */
function num(v: number | null | undefined): number {
  return typeof v === 'number' ? v : 0
}

export interface EvaluationViewProps {
  panelId: string
  panelTitle: string
  query: UseQueryResult<EvalsResponse>
  /** Rendered assessment section (feature selector + text); composed by the panel. */
  assessmentSlot?: ReactNode
}

/** Presentational evaluation view — driven entirely by its (validated) query. */
export function EvaluationView({
  panelId,
  panelTitle,
  query,
  assessmentSlot,
}: EvaluationViewProps) {
  return (
    <PanelFrame
      id={panelId}
      title={panelTitle}
      description="Eval-ledger quality trend (Recall@k / nDCG / MRR / reliability) + the post-phase self-assessment"
      query={query}
      span={12}
      exportName="evals"
      isEmpty={(d) => d.series.length === 0 && d.acr.length === 0}
      emptyTitle="No evaluation data recorded"
      emptyDescription="Run scripts/agentware eval --record — Recall@k / nDCG / MRR stream to benchmarks/history.jsonl."
    >
      {(data) => <EvaluationBody data={data} assessmentSlot={assessmentSlot} />}
    </PanelFrame>
  )
}

function EvaluationBody({
  data,
  assessmentSlot,
}: {
  data: EvalsResponse
  assessmentSlot?: ReactNode
}) {
  const rows = data.series
  const latest: EvalRun | undefined = data.latest ?? rows[rows.length - 1]
  const prev = rows.length > 1 ? rows[rows.length - 2] : undefined
  const latestRel = num(latest?.reliability)
  const delta =
    prev?.reliability != null && latest?.reliability != null
      ? latest.reliability - prev.reliability
      : 0

  const categories = useMemo(
    () => rows.map((r) => shortSha(r.commit) || (r.run ?? '')),
    [rows],
  )
  const series = useMemo(
    () => [
      { name: 'Recall@k', data: rows.map((r) => num(r.recall_at_k)) },
      { name: 'nDCG@k', data: rows.map((r) => num(r.ndcg_at_k)) },
      { name: 'MRR', data: rows.map((r) => num(r.mrr)) },
    ],
    [rows],
  )

  const acr = data.latest_acr ?? data.acr[data.acr.length - 1]

  return (
    <div className="flex flex-col gap-4" data-testid="evals-body">
      {/* North-star tiles — latest eval quality at a glance. */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label="Reliability"
          value={`${latestRel.toFixed(0)}%`}
          status={
            latestRel >= 80 ? 'success' : latestRel >= 60 ? 'warning' : 'danger'
          }
          trend={{
            value: `${delta >= 0 ? '+' : ''}${delta.toFixed(0)}`,
            goodWhen: 'up',
          }}
          hint="latest eval"
        />
        <StatTile
          label="Recall@k"
          value={percent(num(latest?.recall_at_k))}
          status="neutral"
          hint="latest"
        />
        <StatTile
          label="nDCG@k"
          value={percent(num(latest?.ndcg_at_k))}
          status="neutral"
          hint="latest"
        />
        <StatTile
          label="MRR"
          value={percent(num(latest?.mrr))}
          status="neutral"
          hint="latest"
        />
      </div>

      {/* Eval trend across runs. */}
      <div data-testid="evals-trend">
        <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
          Quality trend · {rows.length} eval{rows.length === 1 ? '' : 's'}
        </h3>
        <LineTrend
          categories={categories}
          series={series}
          valueFormatter={(v) => v.toFixed(2)}
          ariaLabel="Evaluation quality trend"
        />
      </div>

      {/* ACR-gate decision (split out of the eval series). */}
      {acr ? (
        <div data-testid="evals-acr" className="flex flex-wrap items-center gap-3">
          <span className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
            ACR gate
          </span>
          <span
            className={
              'rounded px-2 py-0.5 text-2xs font-medium ' +
              (acr.passed
                ? 'bg-success/15 text-success'
                : 'bg-warning/15 text-warning')
            }
            data-testid="evals-acr-badge"
          >
            {acr.passed ? 'PASS' : 'no-change'}
          </span>
          <span className="text-2xs text-muted-foreground">
            decided strategy:{' '}
            <span className="font-medium text-card-foreground">
              {acr.decided_strategy ?? '—'}
            </span>
            {acr.commit ? ` · ${shortSha(acr.commit)}` : ''}
          </span>
        </div>
      ) : null}

      {/* Post-phase self-assessment for a picked feature. */}
      {assessmentSlot}
    </div>
  )
}

/**
 * The assessment sub-section: a feature selector (from `/api/loop`) + the picked
 * feature's post-phase `assessment.md` text via the parameterized
 * `useAssessment` hook. Rendered inside the panel (which provides the query
 * context); the isolated `EvaluationView` test passes a static slot instead.
 */
function AssessmentSection({
  features,
  active,
}: {
  features: string[]
  active: string | null
}) {
  const [picked, setPicked] = useState<string | undefined>(undefined)
  const target = picked ?? active ?? features[0]
  const aq = useAssessment(target)
  const data = aq.data

  return (
    <div data-testid="evals-assessment" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
          Post-phase assessment
        </h3>
        {features.length > 0 ? (
          <select
            data-testid="evals-assessment-select"
            aria-label="Select feature for the post-phase assessment"
            value={target ?? ''}
            onChange={(e) => setPicked(e.target.value)}
            className="rounded border border-border bg-background px-2 py-1 text-2xs"
          >
            {features.map((f) => (
              <option key={f} value={f}>
                {f}
                {f === active ? ' (active)' : ''}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      {data && data.exists ? (
        <pre
          data-testid="evals-assessment-text"
          className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-border bg-muted/30 p-3 text-2xs leading-relaxed text-card-foreground"
        >
          {data.text}
        </pre>
      ) : (
        <p className="text-2xs text-muted-foreground">
          {target
            ? `No assessment.md recorded for ${target} yet.`
            : 'No feature selected.'}
        </p>
      )}
    </div>
  )
}

/**
 * Registry panel: bound to `/api/evals` for the quality trend; it composes the
 * `/api/loop` feature list to drive the assessment selector and renders the
 * picked feature's `assessment.md` via `useAssessment`. One registry entry.
 */
export function EvaluationPanel({ panel, query }: PanelProps<EvalsResponse>) {
  const loopQ = useLoop()
  const features = (loopQ.data?.features ?? []).map((f) => f.feature)
  const active = loopQ.data?.active ?? null

  return (
    <EvaluationView
      panelId={panel.id}
      panelTitle={panel.title}
      query={query}
      assessmentSlot={<AssessmentSection features={features} active={active} />}
    />
  )
}
