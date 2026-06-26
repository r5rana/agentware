import { Burndown, BarSeries, type BurndownPoint } from '@/components/charts'
import { StatTile } from '@/components/ui'
import { cn } from '@/lib/utils'
import type {
  LoopAnalyticsFeature,
  LoopAnalyticsResponse,
} from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { compactNumber, duration, percent } from '@/panels/format'

/**
 * LOOP ANALYTICS panel (Task 28) — the first-class view of agentware's
 * differentiator, the 3-phase pre/main/post loop. Bound to `/api/loop-analytics`,
 * it renders, for the most-recent run: the tasks-remaining BURNDOWN, the per-phase
 * (pre/main/post) wall-time + token breakdown, iteration efficiency / max-iter
 * utilization / self-heal, the pre & post hook gate outcomes, and the loop
 * throughput (features completed per day) across all runs. Purely prop-driven and
 * idle-resilient (reads persisted emission, no active run required).
 */

const PHASES = ['pre', 'main', 'post'] as const

/** Pick the run to feature: the one with the most emitted events (richest). */
function pickFeature(
  features: LoopAnalyticsFeature[],
): LoopAnalyticsFeature | undefined {
  if (features.length === 0) return undefined
  return [...features].sort((a, b) => b.event_count - a.event_count)[0]
}

/** A small pre/post gate pill (color = meaning). */
function GateBadge({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span
      data-testid={`gate-${label}`}
      data-ok={ok}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium',
        ok
          ? 'border-success/40 text-success'
          : 'border-danger/40 text-danger',
      )}
    >
      <span
        className={cn(
          'inline-block h-1.5 w-1.5 rounded-full',
          ok ? 'bg-success' : 'bg-danger',
        )}
      />
      {label}-hook {ok ? 'passed' : 'failed'}
    </span>
  )
}

export function LoopAnalyticsPanel({
  panel,
  query,
}: PanelProps<LoopAnalyticsResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="The 3-phase loop — burndown, phase breakdown, gates & throughput"
      query={query}
      span={12}
      isEmpty={(d) => d.features.length === 0}
      emptyTitle="No loop runs recorded"
      emptyDescription="Run ./agentware.sh <feature> to populate loop analytics."
    >
      {(data) => {
        const feature = pickFeature(data.features)
        const burndown: BurndownPoint[] = (feature?.burndown ?? [])
          .filter((b) => b.tasks_remaining != null)
          .map((b) => ({
            iteration: b.iteration,
            remaining: b.tasks_remaining as number,
          }))
        const wall = feature?.phase_wall_s ?? {}
        const tokens = feature?.phase_tokens ?? {}
        const throughputDays = Object.keys(data.throughput?.by_day ?? {}).sort()
        const gatePre = (feature?.gates?.pre ?? []).every((g) => g.ok !== false)
        const gatePost = (feature?.gates?.post ?? []).every(
          (g) => g.ok !== false,
        )
        const hasPre = (feature?.gates?.pre ?? []).length > 0
        const hasPost = (feature?.gates?.post ?? []).length > 0
        return (
          <div className="flex flex-col gap-4" data-testid="loop-analytics-body">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-2xs text-muted-foreground">
                {feature?.feature ?? '—'}
              </span>
              <div className="flex items-center gap-2">
                {hasPre ? <GateBadge label="pre" ok={gatePre} /> : null}
                {hasPost ? <GateBadge label="post" ok={gatePost} /> : null}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile
                label="Iterations"
                value={feature?.iterations_to_completion ?? '—'}
                status="neutral"
                hint={
                  feature?.max_iterations
                    ? `of ${feature.max_iterations} max`
                    : 'to completion'
                }
              />
              <StatTile
                label="Efficiency"
                value={
                  feature?.iteration_efficiency != null
                    ? feature.iteration_efficiency.toFixed(2)
                    : '—'
                }
                status="neutral"
                hint="tasks closed / iteration"
              />
              <StatTile
                label="Max-iter used"
                value={
                  feature?.max_iteration_utilization != null
                    ? percent(feature.max_iteration_utilization, 0)
                    : '—'
                }
                status={
                  (feature?.max_iteration_utilization ?? 0) >= 0.9
                    ? 'danger'
                    : (feature?.max_iteration_utilization ?? 0) >= 0.7
                      ? 'warning'
                      : 'success'
                }
                hint="cap headroom"
              />
              <StatTile
                label="Self-heal"
                value={feature?.self_heal_count ?? 0}
                status={
                  (feature?.self_heal_count ?? 0) > 0 ? 'warning' : 'success'
                }
                hint="re-engagements"
              />
            </div>

            {burndown.length > 0 ? (
              <div>
                <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  Tasks-remaining burndown
                </h3>
                <Burndown
                  data={burndown}
                  ariaLabel="Tasks remaining burndown across loop iterations"
                />
              </div>
            ) : null}

            <div className="grid gap-4 lg:grid-cols-2">
              <div data-testid="phase-breakdown">
                <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  Per-phase wall-time &amp; tokens
                </h3>
                <BarSeries
                  categories={PHASES as unknown as string[]}
                  series={[
                    {
                      name: 'Wall (s)',
                      data: PHASES.map((p) => Math.round(wall[p] ?? 0)),
                    },
                    {
                      name: 'Tokens',
                      data: PHASES.map((p) => tokens[p] ?? 0),
                    },
                  ]}
                  showLegend
                  height={200}
                  ariaLabel="Per-phase wall-time and token breakdown"
                  valueFormatter={(v) => compactNumber(v)}
                />
              </div>

              <div data-testid="throughput">
                <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  Loop throughput (features completed / day)
                </h3>
                {throughputDays.length > 0 ? (
                  <BarSeries
                    categories={throughputDays}
                    series={[
                      {
                        name: 'Completed',
                        status: 'success',
                        data: throughputDays.map(
                          (d) => data.throughput?.by_day?.[d] ?? 0,
                        ),
                      },
                    ]}
                    height={200}
                    ariaLabel="Features completed per day"
                  />
                ) : (
                  <p className="text-2xs text-muted-foreground">
                    No completed runs yet.
                  </p>
                )}
              </div>
            </div>

            {feature?.latency_s != null ? (
              <p className="text-2xs text-muted-foreground">
                Promise/.done latency:{' '}
                <span className="text-card-foreground">
                  {duration(feature.latency_s)}
                </span>
              </p>
            ) : null}
          </div>
        )
      }}
    </PanelFrame>
  )
}
