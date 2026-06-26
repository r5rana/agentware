import { Burndown, type BurndownPoint } from '@/components/charts'
import { StatTile } from '@/components/ui'
import { cn } from '@/lib/utils'
import type { LoopEvent, LoopResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { duration, shortDate } from '@/panels/format'

/**
 * Execution / loop panel (Task 21) — the LIVE differentiator. Bound to the only
 * interval-polled hook (`/api/loop`), it shows whether a run is in flight, a
 * tasks-remaining BURNDOWN over iterations, and the most recent phase/transition
 * events. Being purely prop-driven, it re-renders the instant a fresh `/api/loop`
 * payload arrives (the polled live behaviour the Task-21 test asserts).
 */

/** A live "active run" / "idle" pill (color = meaning). */
function LiveBadge({ active }: { active: string | null }) {
  const live = active != null && active !== ''
  return (
    <span
      data-testid="loop-live-badge"
      data-live={live}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium',
        live
          ? 'border-success/40 text-success'
          : 'border-border text-muted-foreground',
      )}
    >
      <span
        className={cn(
          'inline-block h-1.5 w-1.5 rounded-full',
          live ? 'animate-pulse bg-success' : 'bg-muted-foreground/50',
        )}
      />
      {live ? 'Active run' : 'No active run'}
    </span>
  )
}

/** Build a burndown (iteration → tasks_remaining) from the loop's phase events. */
function burndownFromEvents(events: LoopEvent[]): BurndownPoint[] {
  const byIter = new Map<number, number>()
  for (const e of events) {
    if (e.iteration == null || e.tasks_remaining == null) continue
    // Keep the LAST remaining count seen for each iteration.
    byIter.set(e.iteration, e.tasks_remaining)
  }
  return [...byIter.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([iteration, remaining]) => ({ iteration, remaining }))
}

export function LoopPanel({ panel, query }: PanelProps<LoopResponse>) {
  const active = query.data?.active ?? null
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="The 3-phase loop — live state, burndown & recent events"
      query={query}
      span={12}
      headerRight={<LiveBadge active={active} />}
      isEmpty={(d) => d.features.length === 0 && d.recent_events.length === 0}
      emptyTitle="No loop runs recorded"
      emptyDescription="Run ./agentware.sh <feature> to populate the loop telemetry."
    >
      {(data) => {
        const burndown = burndownFromEvents(data.recent_events)
        const latest = data.features[0]
        const phaseEvents = data.recent_events.filter((e) => e.phase != null)
        return (
          <div className="flex flex-col gap-4" data-testid="loop-panel-body">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile
                label="Active"
                value={active ?? '—'}
                status={active ? 'success' : 'neutral'}
                hint={active ? 'run in flight' : 'idle (history below)'}
              />
              <StatTile
                label="Iteration"
                value={latest?.iteration ?? '—'}
                status="neutral"
                hint={
                  latest?.outcome?.iterations_used != null
                    ? `${latest.outcome.iterations_used} used`
                    : 'current iteration'
                }
              />
              <StatTile
                label="Tasks"
                value={
                  latest ? `${latest.tasks_done}/${latest.tasks_total}` : '—'
                }
                status={
                  latest && latest.tasks_open === 0 ? 'success' : 'warning'
                }
                hint={latest ? `${latest.tasks_open} open` : 'done / total'}
              />
              <StatTile
                label="Self-heal"
                value={latest?.outcome?.self_heal_count ?? 0}
                status="neutral"
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
                  ariaLabel="Tasks remaining burndown across iterations"
                />
              </div>
            ) : null}

            <ul
              data-testid="loop-recent-events"
              className="flex flex-col divide-y divide-border rounded-lg border border-border"
            >
              {phaseEvents.slice(-6).map((e, i) => (
                <li
                  key={`${e.ts}-${i}`}
                  className="flex items-center justify-between gap-3 px-3 py-2 text-2xs"
                >
                  <span className="font-medium text-card-foreground">
                    it{e.iteration} · {e.phase}
                  </span>
                  <span className="text-muted-foreground">
                    {e.tasks_remaining != null
                      ? `${e.tasks_remaining} left`
                      : ''}
                    {e.phase_wall_s != null
                      ? ` · ${duration(e.phase_wall_s)}`
                      : ''}
                  </span>
                  <span className="text-muted-foreground">
                    {shortDate(e.ts)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )
      }}
    </PanelFrame>
  )
}
