import { useMemo, useState, type ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

import { TraceWaterfall, type TraceStep as WaterfallStep } from '@/components/charts'
import { StatTile } from '@/components/ui'
import { cn } from '@/lib/utils'
import type {
  LoopResponse,
  TraceIteration,
  TraceResponse,
  TraceStep,
} from '@/services/api/contract'
import { useTrace } from '@/services/query'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { compactNumber, duration, shortDate } from '@/panels/format'

/**
 * TRACE / RUN-EXPLORER panel (Task 29) — the step-level view that answers the #1
 * LLM/agent-observability pain: "see the REASONING, not just inputs/outputs". It
 * renders an ordered tool-call WATERFALL grouped by loop iteration (from
 * `/api/trace/<session|feature>`), expandable step detail (truncated tool I/O,
 * status, per-step duration + tokens), and a step/replay control that walks the
 * run iteration-by-iteration. Idle-resilient (reads persisted live.jsonl/main.jsonl).
 */

/** Map a status string to the chart's semantic color. */
function stepStatus(s: TraceStep): WaterfallStep['status'] {
  return s.status === 'ERR' ? 'danger' : 'success'
}

/** Cumulative-offset waterfall steps for one iteration's tool calls. */
function toWaterfall(steps: TraceStep[]): WaterfallStep[] {
  let offset = 0
  return steps.map((s) => {
    const dur = Math.max(0, s.duration_s ?? 0)
    const start = offset
    offset += dur || 0.5 // give zero-duration steps a sliver so the bar shows
    return {
      label: `${s.index}· ${s.tool}`,
      start,
      duration: dur || 0.5,
      status: stepStatus(s),
    }
  })
}

/** Expandable detail for a single step (truncated tool I/O + status). */
function StepRow({ step }: { step: TraceStep }) {
  const [open, setOpen] = useState(false)
  const err = step.status === 'ERR'
  return (
    <li data-testid="trace-step" className="rounded-md border border-border/60">
      <button
        type="button"
        data-testid={`trace-step-toggle-${step.index}`}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted/40"
      >
        <span
          className={cn(
            'inline-block h-1.5 w-1.5 shrink-0 rounded-full',
            err ? 'bg-danger' : 'bg-success',
          )}
        />
        <span className="w-6 shrink-0 text-2xs tabular-nums text-muted-foreground">
          {step.index}
        </span>
        <span className="font-medium">{step.tool}</span>
        <span
          className={cn(
            'text-2xs',
            err ? 'text-danger' : 'text-muted-foreground',
          )}
        >
          {step.status}
        </span>
        <span className="ml-auto flex shrink-0 items-center gap-3 text-2xs text-muted-foreground tabular-nums">
          {step.tokens != null ? <span>{compactNumber(step.tokens)} tok</span> : null}
          {step.duration_s != null ? <span>{duration(step.duration_s)}</span> : null}
        </span>
      </button>
      {open ? (
        <div
          data-testid={`trace-step-detail-${step.index}`}
          className="space-y-2 border-t border-border/60 px-3 py-2"
        >
          <div>
            <p className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
              Input{step.args_truncated ? ' (truncated)' : ''}
            </p>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-2xs">
              {step.args || '—'}
            </pre>
          </div>
          <div>
            <p className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
              Output{step.result_truncated ? ' (truncated)' : ''}
            </p>
            <pre className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-muted/40 p-2 text-2xs">
              {step.result || '—'}
            </pre>
          </div>
        </div>
      ) : null}
    </li>
  )
}

export interface TraceViewProps {
  panelId: string
  panelTitle: string
  target: string | undefined
  query: UseQueryResult<TraceResponse>
  /** Optional header-right slot (the run selector). */
  headerRight?: ReactNode
}

/** Presentational trace view — driven entirely by its (validated) query. */
export function TraceView({
  panelId,
  panelTitle,
  target,
  query,
  headerRight,
}: TraceViewProps) {
  return (
    <PanelFrame
      id={panelId}
      title={panelTitle}
      description="Step-level tool-call timeline, grouped by loop iteration — replay a run"
      query={query}
      span={12}
      headerRight={headerRight}
      exportName={`trace-${target ?? 'run'}`}
      isEmpty={(d) => d.step_count === 0}
      emptyTitle="No trace steps recorded"
      emptyDescription="Run ./agentware.sh <feature> — per-action steps stream to live.jsonl."
    >
      {(data) => <TraceBody data={data} />}
    </PanelFrame>
  )
}

function TraceBody({ data }: { data: TraceResponse }) {
  const iterations = data.iterations
  // Replay cursor: which iteration group is in view. 0-based over `iterations`.
  const [cursor, setCursor] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const safeCursor = Math.min(cursor, Math.max(0, iterations.length - 1))
  const current: TraceIteration | undefined = iterations[safeCursor]

  const visibleSteps: TraceStep[] = useMemo(
    () =>
      showAll
        ? iterations.flatMap((it) => it.steps)
        : (current?.steps ?? []),
    [showAll, iterations, current],
  )
  const waterfall = useMemo(() => toWaterfall(visibleSteps), [visibleSteps])
  const iterLabel = (it: TraceIteration | undefined) =>
    it?.iteration == null ? 'ungrouped' : `iteration ${it.iteration}`

  return (
    <div className="flex flex-col gap-4" data-testid="trace-body">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Steps" value={data.step_count} status="neutral" hint="tool calls" />
        <StatTile
          label="Errors"
          value={data.err_count}
          status={data.err_count > 0 ? 'danger' : 'success'}
          hint="failed tool calls"
        />
        <StatTile
          label="Iterations"
          value={iterations.length}
          status="neutral"
          hint="loop groups"
        />
        <StatTile
          label="Tools"
          value={Object.keys(data.tool_summary).length}
          status="neutral"
          hint="distinct"
        />
      </div>

      {/* Replay control — walk the run iteration-by-iteration. */}
      <div
        data-testid="trace-replay"
        className="flex flex-wrap items-center gap-2 rounded-md border border-border/60 px-3 py-2"
      >
        <button
          type="button"
          data-testid="trace-replay-prev"
          disabled={showAll || safeCursor <= 0}
          onClick={() => setCursor((c) => Math.max(0, c - 1))}
          className="rounded border border-border px-2 py-1 text-2xs font-medium disabled:opacity-40"
        >
          ‹ Prev
        </button>
        <span
          data-testid="trace-replay-label"
          className="min-w-32 text-center text-2xs font-medium tabular-nums"
        >
          {showAll
            ? `All steps (${visibleSteps.length})`
            : `${iterLabel(current)} · ${safeCursor + 1}/${iterations.length}`}
        </span>
        <button
          type="button"
          data-testid="trace-replay-next"
          disabled={showAll || safeCursor >= iterations.length - 1}
          onClick={() =>
            setCursor((c) => Math.min(iterations.length - 1, c + 1))
          }
          className="rounded border border-border px-2 py-1 text-2xs font-medium disabled:opacity-40"
        >
          Next ›
        </button>
        <button
          type="button"
          data-testid="trace-replay-all"
          aria-pressed={showAll}
          onClick={() => setShowAll((v) => !v)}
          className={cn(
            'ml-auto rounded border px-2 py-1 text-2xs font-medium',
            showAll
              ? 'border-ring bg-muted text-foreground'
              : 'border-border text-muted-foreground',
          )}
        >
          {showAll ? 'Replay mode' : 'Show all'}
        </button>
      </div>

      {/* Step-level waterfall for the visible steps. */}
      {waterfall.length > 0 ? (
        <div>
          <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
            Step waterfall
          </h3>
          <TraceWaterfall
            steps={waterfall}
            ariaLabel="Tool-call step waterfall for the current iteration"
          />
        </div>
      ) : null}

      {/* Marker/decision transitions for the current iteration. */}
      {!showAll && current?.transitions.length ? (
        <div data-testid="trace-transitions" className="text-2xs text-muted-foreground">
          {current.transitions.map((t, i) => (
            <span key={i} className="mr-3 inline-block">
              task {t.task}: {t.from} → {t.to}
              {t.approx ? ' (approx)' : ''} · {shortDate(t.ts)}
            </span>
          ))}
        </div>
      ) : null}

      {/* Expandable step list. */}
      <ul className="flex flex-col gap-1.5" data-testid="trace-steps">
        {visibleSteps.map((s) => (
          <StepRow key={`${s.session_id ?? ''}-${s.index}`} step={s} />
        ))}
      </ul>
    </div>
  )
}

/**
 * Registry panel: bound to `/api/loop` for the run list, it lets the operator
 * pick a feature (default = the active run) and renders that run's trace via the
 * parameterized `useTrace` hook. Adding it is one registry entry (Loops section).
 */
export function TracePanel({ panel, query }: PanelProps<LoopResponse>) {
  const features = query.data?.features ?? []
  const active = query.data?.active ?? null
  const options = features.map((f) => f.feature)
  const [picked, setPicked] = useState<string | undefined>(undefined)
  const target = picked ?? active ?? options[0]
  const traceQuery = useTrace(target)

  const selector =
    options.length > 0 ? (
      <select
        data-testid="trace-run-select"
        aria-label="Select run to trace"
        value={target ?? ''}
        onChange={(e) => setPicked(e.target.value)}
        className="rounded border border-border bg-background px-2 py-1 text-2xs"
      >
        {options.map((f) => (
          <option key={f} value={f}>
            {f}
            {f === active ? ' (active)' : ''}
          </option>
        ))}
      </select>
    ) : null

  return (
    <TraceView
      panelId={panel.id}
      panelTitle={panel.title}
      target={target}
      query={traceQuery}
      headerRight={selector}
    />
  )
}
