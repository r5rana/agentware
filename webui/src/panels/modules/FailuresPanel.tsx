import { useMemo, useState, type ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

import { BarSeries } from '@/components/charts'
import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import type { FailuresResponse, LoopResponse } from '@/services/api/contract'
import { useFailures } from '@/services/query'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { compactNumber, percent } from '@/panels/format'

/**
 * FAILURE-LADDER & error-recovery panel (Task 32) — agentware-specific, high
 * value. It answers WHERE runs get stuck (tool ERR rate from the live.jsonl
 * stream + the per-tool tally) and HOW they recovered along the R-FAIL
 * escalation ladder (KB → reasoning → inputs → switch → web). Bound to
 * `/api/failures/<feature>`, plus the web-search escalation count, self-heal
 * re-engagements, and the DECISION/LEARNED marker tallies. Idle-resilient —
 * reads persisted derivations; no active run required.
 */

/** Human label for each R-FAIL ladder tier (in order). */
const LADDER_LABELS: Record<string, string> = {
  kb: 'Knowledge base',
  reasoning: 'Own reasoning',
  inputs: 'Change inputs',
  switch: 'Switch approach',
  web: 'Web search',
}

/** One row of the per-tool ERR table. */
interface ErrToolRow {
  tool: string
  errors: number
}

export interface FailuresViewProps {
  panelId: string
  panelTitle: string
  target: string | undefined
  query: UseQueryResult<FailuresResponse>
  /** Optional header-right slot (the run selector). */
  headerRight?: ReactNode
}

/** Presentational failures view — driven entirely by its (validated) query. */
export function FailuresView({
  panelId,
  panelTitle,
  target,
  query,
  headerRight,
}: FailuresViewProps) {
  return (
    <PanelFrame
      id={panelId}
      title={panelTitle}
      description="Where runs get stuck (tool ERR rate) and how they recovered along the R-FAIL ladder"
      query={query}
      span={12}
      headerRight={headerRight}
      exportName={`failures-${target ?? 'run'}`}
      isEmpty={(d) => d.step_count === 0}
      emptyTitle="No failure data recorded"
      emptyDescription="Run ./agentware.sh <feature> — tool failures + recovery stream to live.jsonl."
    >
      {(data) => <FailuresBody data={data} />}
    </PanelFrame>
  )
}

function FailuresBody({ data }: { data: FailuresResponse }) {
  const order = data.ladder_order.length
    ? data.ladder_order
    : ['kb', 'reasoning', 'inputs', 'switch', 'web']

  // Ladder usage as a horizontal bar chart (one category per tier, in order).
  const ladderCategories = useMemo(
    () => order.map((t) => LADDER_LABELS[t] ?? t),
    [order],
  )
  const ladderSeries = useMemo(
    () => [
      {
        name: 'Recoveries',
        data: order.map((t) => data.ladder[t] ?? 0),
      },
    ],
    [order, data.ladder],
  )
  const ladderTotal = order.reduce((sum, t) => sum + (data.ladder[t] ?? 0), 0)

  // Per-tool ERR breakdown (where runs get stuck), worst-first.
  const errRows: ErrToolRow[] = useMemo(
    () =>
      Object.entries(data.err_by_tool)
        .map(([tool, errors]) => ({ tool, errors }))
        .sort((a, b) => b.errors - a.errors),
    [data.err_by_tool],
  )
  const errColumns: DataTableColumn<ErrToolRow>[] = [
    {
      key: 'tool',
      header: 'Tool',
      cell: (r) => <span className="font-medium text-card-foreground">{r.tool}</span>,
    },
    {
      key: 'errors',
      header: 'Errors',
      align: 'right',
      sortValue: (r) => r.errors,
      cell: (r) => <span className="tabular-nums text-danger">{r.errors}</span>,
    },
  ]

  const learned = data.markers.learned
  const decision = data.markers.decision

  return (
    <div className="flex flex-col gap-4" data-testid="failures-body">
      {/* Headline tiles — WHERE runs get stuck + the recovery effort. */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label="ERR rate"
          value={percent(data.err_rate)}
          status={data.err_rate > 0 ? 'warning' : 'success'}
          hint={`${data.err_count}/${data.step_count} tool calls`}
        />
        <StatTile
          label="Web escalations"
          value={data.web_search_count}
          status="neutral"
          hint="R-FAIL tier 5"
        />
        <StatTile
          label="KB lookups"
          value={data.kb_lookup_count}
          status="neutral"
          hint="recall / query"
        />
        <StatTile
          label="Self-heal"
          value={data.self_heal_count}
          status={data.self_heal_count > 0 ? 'warning' : 'neutral'}
          hint="loop re-engagements"
        />
      </div>

      {/* Recovery ladder — how runs climbed R-FAIL to recover from a failure. */}
      <div data-testid="failures-ladder">
        <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
          Recovery ladder (R-FAIL) · {ladderTotal} recover
          {ladderTotal === 1 ? 'y' : 'ies'}
          {data.unrecovered > 0 ? ` · ${data.unrecovered} unrecovered` : ''}
        </h3>
        <BarSeries
          categories={ladderCategories}
          series={ladderSeries}
          horizontal
          ariaLabel="R-FAIL recovery-ladder tier usage"
          valueFormatter={(v) => compactNumber(v)}
        />
      </div>

      {/* Per-tool ERR table + marker tallies. */}
      <div className="grid gap-4 md:grid-cols-2">
        <div data-testid="failures-err-by-tool">
          <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
            Failures by tool
          </h3>
          <DataTable
            columns={errColumns}
            rows={errRows}
            rowKey={(r) => r.tool}
            empty="No tool failures recorded"
          />
        </div>

        <div data-testid="failures-markers" className="flex flex-col gap-3">
          <h3 className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
            Decisions & learnings
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <StatTile
              label="Learnings"
              value={learned.total}
              status={learned.unpromoted > 0 ? 'warning' : 'neutral'}
              hint={
                learned.unpromoted > 0
                  ? `${learned.unpromoted} unpromoted`
                  : 'all promoted'
              }
            />
            <StatTile
              label="Decisions"
              value={decision.total}
              status={decision.unpromoted > 0 ? 'warning' : 'neutral'}
              hint={
                decision.unpromoted > 0
                  ? `${decision.unpromoted} unpromoted`
                  : 'all promoted'
              }
            />
          </div>
        </div>
      </div>
    </div>
  )
}

/**
 * Registry panel: bound to `/api/loop` for the run list, it lets the operator
 * pick a feature (default = the active run) and renders that run's failure-ladder
 * via the parameterized `useFailures` hook. Adding it is one registry entry.
 */
export function FailuresPanel({ panel, query }: PanelProps<LoopResponse>) {
  const features = query.data?.features ?? []
  const active = query.data?.active ?? null
  const options = features.map((f) => f.feature)
  const [picked, setPicked] = useState<string | undefined>(undefined)
  const target = picked ?? active ?? options[0]
  const failuresQuery = useFailures(target)

  const selector =
    options.length > 0 ? (
      <select
        data-testid="failures-run-select"
        aria-label="Select run for the failure ladder"
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
    <FailuresView
      panelId={panel.id}
      panelTitle={panel.title}
      target={target}
      query={failuresQuery}
      headerRight={selector}
    />
  )
}
