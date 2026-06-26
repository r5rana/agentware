import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Brain, Repeat } from 'lucide-react'

import { GridCell } from '@/components/layout/ContentGrid'
import { StatTile } from '@/components/ui'
import type { SemanticStatus } from '@/lib/design-tokens'
import { cn } from '@/lib/utils'
import type { LoopResponse } from '@/services/api/contract'
import { percent } from '@/panels/format'
import type { PanelProps } from '@/panels/registry'
import {
  useAlerts,
  useKb,
  useLoopHealth,
  useOutcomes,
  useQuality,
} from '@/services/query'
import { loopHealthStatusToSemantic } from '@/panels/modules/LoopHealthPanel'
import { alertSeverityToSemantic } from '@/panels/modules/AlertsPanel'

/**
 * Overview landing page (Task 27) — the dual-pillar north-star surface.
 *
 * agentware is BOTH a looping agent AND a memory system, so the Overview answers
 * "is everything okay?" across the two pillars side-by-side BEFORE any drill-down
 * (Stripe-style restraint + progressive disclosure):
 *
 *  - **Agent / Loop health** — active loop, run success rate, iteration
 *    efficiency, open alerts.
 *  - **Knowledge Base / Memory health** — KB growth, recall quality, freshness.
 *
 * Every tile DEEP-LINKS to its dedicated panel, so the Overview organises the IA
 * without burying anything. It composes its own hooks (loop is the bound
 * `useData`; outcomes / kb / quality are read here) rather than a single endpoint,
 * so it reflects the whole system at a glance.
 */

/** A deep-linking metric tile (or a plain tile when no `to` is given). */
function TileLink({
  to,
  label,
  children,
}: {
  to?: string
  label: string
  children: ReactNode
}) {
  if (!to) return <>{children}</>
  return (
    <Link
      to={to}
      aria-label={`${label} — open panel`}
      className={cn(
        'rounded-lg outline-none transition-transform duration-75',
        'hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-ring',
        '[&_[data-testid=stat-tile]]:hover:border-foreground/30',
      )}
    >
      {children}
    </Link>
  )
}

/** A titled group of tiles representing one pillar. */
function PillarGroup({
  testId,
  title,
  icon: Icon,
  children,
}: {
  testId: string
  title: string
  icon: typeof Brain
  children: ReactNode
}) {
  return (
    <section
      data-testid={testId}
      aria-label={title}
      className="flex flex-col gap-3 rounded-xl border border-border bg-card/40 p-4"
    >
      <h2 className="flex items-center gap-2 text-sm font-semibold tracking-tight text-foreground">
        <Icon aria-hidden="true" className="size-4 text-muted-foreground" />
        {title}
      </h2>
      <div className="grid grid-cols-2 gap-3">{children}</div>
    </section>
  )
}

/** Average tasks closed per iteration across loop features (iteration efficiency). */
function iterationEfficiency(loop: LoopResponse | undefined): number | null {
  const features = loop?.features ?? []
  const rows = features.filter(
    (f) => typeof f.iteration === 'number' && (f.iteration ?? 0) > 0,
  )
  if (rows.length === 0) return null
  const sum = rows.reduce(
    (acc, f) => acc + f.tasks_done / (f.iteration as number),
    0,
  )
  return sum / rows.length
}

export function OverviewPanel({ panel, query }: PanelProps<LoopResponse>) {
  const loopQ = query
  const outcomesQ = useOutcomes()
  const kbQ = useKb()
  const qualityQ = useQuality()
  const loopHealthQ = useLoopHealth()
  const alertsQ = useAlerts()

  // Progressive disclosure ("speed as design"): gate the panel's skeleton on
  // ONLY the bound primary query (loop). The other five pillars read undefined
  // data gracefully (tiles fall back to '—'/0) and fill in as each query
  // resolves, so a slow derivation (e.g. /api/loop-health, /api/alerts over a
  // large KB) never blanks the whole Overview waiting for the slowest of six.
  const status = loopQ.isLoading ? 'loading' : 'ready'

  const loop = loopQ.data
  const outcomes = outcomesQ.data
  const kb = kbQ.data
  const quality = qualityQ.data

  // --- Agent / Loop health ---
  const active = loop?.active ?? null
  const totalRuns = outcomes?.features.length ?? 0
  const completed = outcomes?.summary.completed ?? 0
  const successRate = totalRuns > 0 ? completed / totalRuns : 0
  const eff = iterationEfficiency(loop)

  // --- Loop-health (Task 30) — runaway detection rolled up to a badge ---
  const loopHealth = loopHealthQ.data
  const loopHealthStatus = loopHealth?.status ?? 'ok'
  const loopHealthSummary =
    loopHealth?.summary ?? { ok: 0, at_risk: 0, critical: 0 }
  const flaggedRuns =
    (loopHealthSummary.at_risk ?? 0) + (loopHealthSummary.critical ?? 0)
  const loopHealthLabel =
    loopHealthStatus === 'ok'
      ? 'Healthy'
      : loopHealthStatus === 'at_risk'
        ? 'At risk'
        : 'Critical'

  // --- Open alerts (Task 31) — symptom-based count surfaced as a badge/tile ---
  const alerts = alertsQ.data
  const openAlerts = alerts?.open_count ?? alerts?.alerts.length ?? 0
  const alertsStatus = alerts?.status ?? 'ok'
  const openAlertsSemantic =
    openAlerts === 0
      ? 'success'
      : alertSeverityToSemantic(alertsStatus === 'ok' ? 'info' : alertsStatus)

  // --- Memory health ---
  const kbEntries = kb?.entry_count ?? 0
  const latestReliability = quality?.latest?.reliability ?? null
  const kbList = kb?.entries ?? []
  const verified = kbList.filter(
    (e) => (e as Record<string, unknown>).last_verified != null,
  ).length
  const freshness = kbList.length > 0 ? verified / kbList.length : null

  const successStatus: SemanticStatus =
    totalRuns === 0
      ? 'neutral'
      : successRate >= 0.8
        ? 'success'
        : successRate >= 0.5
          ? 'warning'
          : 'danger'
  const reliabilityStatus: SemanticStatus =
    latestReliability == null
      ? 'neutral'
      : latestReliability >= 80
        ? 'success'
        : latestReliability >= 60
          ? 'warning'
          : 'danger'

  return (
    <GridCell span={12}>
      {/* Always-present status marker so the registry wiring is observable. */}
      <span
        data-testid={`panel-status-${panel.id}`}
        className="sr-only"
        data-status={status}
      >
        {status}
      </span>

      {status === 'loading' ? (
        <div
          data-testid="overview-loading"
          className="h-24 animate-pulse rounded-xl border border-border bg-muted/30"
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <PillarGroup
            testId="overview-loop-health"
            title="Agent / Loop health"
            icon={Repeat}
          >
            <TileLink to="/loops/live" label="Active loop">
              <StatTile
                label="Active loop"
                value={active ?? 'Idle'}
                status={active ? 'success' : 'neutral'}
                hint={active ? 'run in flight' : 'no active run'}
              />
            </TileLink>
            <TileLink to="/loops/outcomes" label="Run success rate">
              <StatTile
                label="Run success rate"
                value={totalRuns > 0 ? percent(successRate, 0) : '—'}
                status={successStatus}
                hint={`${completed}/${totalRuns} completed`}
              />
            </TileLink>
            <TileLink to="/loops/analytics" label="Iteration efficiency">
              <StatTile
                label="Iteration efficiency"
                value={eff != null ? eff.toFixed(1) : '—'}
                status="neutral"
                hint="tasks closed / iter"
              />
            </TileLink>
            <TileLink to="/loops/health" label="Loop health">
              <StatTile
                label="Loop health"
                value={loopHealthLabel}
                status={loopHealthStatusToSemantic(loopHealthStatus)}
                hint={
                  flaggedRuns > 0
                    ? `${flaggedRuns} run${flaggedRuns === 1 ? '' : 's'} flagged`
                    : 'no runaway signals'
                }
              />
            </TileLink>
            <TileLink to="/alerts" label="Open alerts">
              <StatTile
                label="Open alerts"
                value={
                  <span
                    data-testid="overview-open-alerts"
                    data-alert-count={openAlerts}
                  >
                    {openAlerts}
                  </span>
                }
                status={openAlertsSemantic}
                hint={openAlerts === 0 ? 'all clear' : 'need attention'}
              />
            </TileLink>
          </PillarGroup>

          <PillarGroup
            testId="overview-memory-health"
            title="Knowledge Base / Memory health"
            icon={Brain}
          >
            <TileLink to="/memory/kb" label="KB entries">
              <StatTile
                label="KB entries"
                value={kbEntries}
                status="neutral"
                hint="indexed knowledge"
              />
            </TileLink>
            <TileLink to="/health/quality" label="Recall quality">
              <StatTile
                label="Recall quality"
                value={
                  latestReliability != null
                    ? `${latestReliability.toFixed(0)}%`
                    : '—'
                }
                status={reliabilityStatus}
                hint="latest reliability"
              />
            </TileLink>
            <TileLink to="/memory/kb" label="KB freshness">
              <StatTile
                label="KB freshness"
                value={freshness != null ? percent(freshness, 0) : '—'}
                status="neutral"
                hint="entries verified"
              />
            </TileLink>
            <TileLink to="/memory/scaling" label="Retrieval scaling">
              <StatTile
                label="Categories"
                value={kb?.category_count ?? 0}
                status="neutral"
                hint="taxonomy buckets"
              />
            </TileLink>
          </PillarGroup>
        </div>
      )}
    </GridCell>
  )
}
