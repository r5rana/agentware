import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import { cn } from '@/lib/utils'
import type { SemanticStatus } from '@/lib/design-tokens'
import type {
  LoopHealthCheck,
  LoopHealthFeature,
  LoopHealthResponse,
  LoopHealthStatus,
} from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'

/**
 * LOOP-HEALTH & runaway detection panel (Task 30) — the most-cited autonomous-
 * agent failure modes made glanceable: dead loops (duplicate tool calls),
 * runaway token burn, context-window overflow, and silent no-progress (maps to
 * agentware R-FAIL-04). Bound to `/api/loop-health`, it renders an OK / at-risk /
 * critical badge PER run that NAMES the offending tool + iteration, plus a
 * per-check breakdown. Purely prop-driven + idle-resilient (reads persisted
 * derivations; no active run required). Also surfaced compactly on the Overview.
 */

const STATUS_LABEL: Record<LoopHealthStatus, string> = {
  ok: 'OK',
  at_risk: 'At risk',
  critical: 'Critical',
}

/** Loop-health severity → the semantic color it carries (color = MEANING). */
export function loopHealthStatusToSemantic(
  status: LoopHealthStatus,
): SemanticStatus {
  if (status === 'critical') return 'danger'
  if (status === 'at_risk') return 'warning'
  return 'success'
}

/** Human label for a check id. */
const CHECK_LABEL: Record<string, string> = {
  duplicate_tool_calls: 'Duplicate tool calls',
  no_progress: 'No progress',
  token_burn: 'Token burn-rate',
  context_window: 'Context window',
}

/** A status pill (OK / At risk / Critical) with a semantic dot. */
export function HealthBadge({
  status,
  label,
}: {
  status: LoopHealthStatus
  label?: string
}) {
  const semantic = loopHealthStatusToSemantic(status)
  return (
    <span
      data-testid={`health-badge-${status}`}
      data-status={status}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium',
        semantic === 'danger' && 'border-danger/40 text-danger',
        semantic === 'warning' && 'border-warning/40 text-warning',
        semantic === 'success' && 'border-success/40 text-success',
      )}
    >
      <span
        className={cn(
          'inline-block h-1.5 w-1.5 rounded-full',
          semantic === 'danger' && 'bg-danger',
          semantic === 'warning' && 'bg-warning',
          semantic === 'success' && 'bg-success',
        )}
      />
      {label ?? STATUS_LABEL[status]}
    </span>
  )
}

const CHECK_ORDER = [
  'duplicate_tool_calls',
  'no_progress',
  'token_burn',
  'context_window',
] as const

/** Order a feature's checks deterministically for display. */
function orderedChecks(feature: LoopHealthFeature): LoopHealthCheck[] {
  const out: LoopHealthCheck[] = []
  for (const id of CHECK_ORDER) {
    const c = feature.checks[id]
    if (c) out.push(c)
  }
  // Any extra checks the backend adds later still surface (forward-compatible).
  for (const [id, c] of Object.entries(feature.checks)) {
    if (!(CHECK_ORDER as readonly string[]).includes(id)) out.push(c)
  }
  return out
}

/** The worst (most-severe) run drives the headline tiles. */
function pickWorst(
  features: LoopHealthFeature[],
): LoopHealthFeature | undefined {
  if (features.length === 0) return undefined
  const rank: Record<LoopHealthStatus, number> = {
    critical: 2,
    at_risk: 1,
    ok: 0,
  }
  return [...features].sort((a, b) => rank[b.status] - rank[a.status])[0]
}

export function LoopHealthPanel({
  panel,
  query,
}: PanelProps<LoopHealthResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Runaway detection — dead loops, token burn, context overflow, no-progress"
      query={query}
      span={12}
      isEmpty={(d) => d.features.length === 0}
      emptyTitle="No loop runs to assess"
      emptyDescription="Run ./agentware.sh <feature> to populate loop-health signals."
    >
      {(data) => {
        const summary = data.summary ?? { ok: 0, at_risk: 0, critical: 0 }
        const worst = pickWorst(data.features)
        const checkColumns: DataTableColumn<LoopHealthCheck>[] = [
          {
            key: 'check',
            header: 'Check',
            cell: (c) => CHECK_LABEL[c.name] ?? c.name,
          },
          {
            key: 'status',
            header: 'Status',
            cell: (c) => <HealthBadge status={c.status} />,
          },
          {
            key: 'detail',
            header: 'Detail',
            cell: (c) => (
              <span className="text-2xs text-muted-foreground">
                {c.detail ??
                  (c.tool ? `tool ${c.tool}` : c.flagged ? 'flagged' : '—')}
              </span>
            ),
          },
        ]
        return (
          <div className="flex flex-col gap-4" data-testid="loop-health-body">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile
                label="Overall"
                value={STATUS_LABEL[data.status ?? 'ok']}
                status={loopHealthStatusToSemantic(data.status ?? 'ok')}
                hint="worst run"
              />
              <StatTile
                label="Healthy"
                value={summary.ok}
                status="success"
                hint="runs OK"
              />
              <StatTile
                label="At risk"
                value={summary.at_risk}
                status={summary.at_risk > 0 ? 'warning' : 'neutral'}
                hint="runs"
              />
              <StatTile
                label="Critical"
                value={summary.critical}
                status={summary.critical > 0 ? 'danger' : 'neutral'}
                hint="runs"
              />
            </div>

            {worst && worst.offender ? (
              <div
                data-testid="loop-health-offender"
                className="rounded-md border border-border bg-muted/30 px-3 py-2 text-2xs"
              >
                <span className="font-medium text-card-foreground">
                  {worst.feature}
                </span>{' '}
                <HealthBadge status={worst.status} />{' '}
                <span className="text-muted-foreground">
                  {worst.offender.detail ??
                    `${worst.offender.check}${
                      worst.offender.tool ? ` · ${worst.offender.tool}` : ''
                    }${
                      worst.offender.iteration != null
                        ? ` · iteration ${worst.offender.iteration}`
                        : ''
                    }`}
                </span>
              </div>
            ) : null}

            <div className="flex flex-col gap-4">
              {data.features.map((f) => (
                <div key={f.feature} data-testid={`loop-health-run-${f.feature}`}>
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className="text-2xs text-muted-foreground">
                      {f.feature}
                    </span>
                    <HealthBadge status={f.status} />
                  </div>
                  <DataTable
                    columns={checkColumns}
                    rows={orderedChecks(f)}
                    rowKey={(c) => c.name}
                    empty="No checks"
                  />
                </div>
              ))}
            </div>
          </div>
        )
      }}
    </PanelFrame>
  )
}
