import { useMemo, useState } from 'react'
import { ClipboardList, Hammer, Search } from 'lucide-react'

import { BarSeries } from '@/components/charts'
import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import { cn } from '@/lib/utils'
import type {
  AgentActivity,
  AgentSession,
  AgentsResponse,
} from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { compactNumber, duration, percent, shortDate, usd } from '@/panels/format'

/**
 * PLAN_AW / WORK_AW pillar panels (the two headline agent views).
 *
 * Each renders ONE agent's activity from the live `/api/agents` payload: an
 * ACTIVE marker (the planner/worker currently running, if any), KPI tiles over
 * the agent's full history, per-day + per-feature breakdowns, and a fully
 * filterable / searchable / sortable session explorer. Both views share the same
 * presentational core (`AgentView`) so PLAN_AW and WORK_AW are visually and
 * behaviourally identical — only the data slice + framing differ.
 */

/** A pulsing "active now" / "idle" pill (color = meaning). */
function ActiveBadge({ active, noun }: { active: boolean; noun: string }) {
  return (
    <span
      data-testid="agent-active-badge"
      data-active={active}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium',
        active
          ? 'border-success/40 text-success'
          : 'border-border text-muted-foreground',
      )}
    >
      <span
        className={cn(
          'inline-block h-1.5 w-1.5 rounded-full',
          active ? 'animate-pulse bg-success' : 'bg-muted-foreground/50',
        )}
      />
      {active ? `Active ${noun}` : `No active ${noun}`}
    </span>
  )
}

/** Best-effort "Nm ago" relative time (browser-side; falls back to the date). */
function relTime(ts: string | null | undefined): string {
  if (!ts) return '—'
  const then = Date.parse(ts)
  if (Number.isNaN(then)) return shortDate(ts)
  const secs = Math.max(0, (Date.now() - then) / 1000)
  if (secs < 90) return 'just now'
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`
  if (secs < 86400) return `${Math.round(secs / 3600)}h ago`
  return `${Math.round(secs / 86400)}d ago`
}

/** A run-status pill: active (live) / complete (done) / ended (neutral) /
 *  incomplete (planner produced no plan, or a run stopped mid-way). */
function StatusBadge({ status }: { status: string | null | undefined }) {
  const s = status ?? 'ended'
  const tone =
    s === 'active'
      ? 'border-success/40 text-success'
      : s === 'complete'
        ? 'border-success/30 text-success'
        : s === 'incomplete'
          ? 'border-warning/40 text-warning'
          : 'border-border text-muted-foreground'
  const label =
    s === 'active'
      ? '● live'
      : s === 'complete'
        ? '✓ complete'
        : s === 'incomplete'
          ? '⚠ incomplete'
          : 'ended'
  return (
    <span
      data-status={s}
      className={cn(
        'inline-flex rounded-md border px-1.5 py-0.5 text-2xs font-medium',
        tone,
      )}
    >
      {label}
    </span>
  )
}

/** A "provisional"/"pending" attribution hint (the feature is inferred, not
 *  action-confirmed) so the operator knows the name may backfill. */
function ConfidenceHint({ confidence }: { confidence: string | null | undefined }) {
  if (confidence === 'high' || !confidence) return null
  return (
    <span
      className="ml-1.5 text-2xs text-muted-foreground"
      title={
        confidence === 'pending'
          ? 'feature not yet determinable — backfills once the run acts on one'
          : 'feature inferred, not action-confirmed'
      }
    >
      {confidence === 'pending' ? '· unattributed' : '· provisional'}
    </span>
  )
}

/* -------------------------------------------------------------------------- */
/* Session explorer: filter (by feature) + search (free text) + sort           */
/* -------------------------------------------------------------------------- */

function matchesQuery(s: AgentSession, q: string): boolean {
  if (!q) return true
  const hay = [
    s.name,
    s.feature,
    s.session_id,
    s.stage,
    s.status,
    s.date,
    ...(s.models ?? []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase()
  return hay.includes(q.toLowerCase())
}

/** A session's display scope for the filter: its feature, else its run name. */
function scopeOf(s: AgentSession): string {
  return s.feature ?? s.name ?? '(unattributed)'
}

function SessionExplorer({ sessions }: { sessions: AgentSession[] }) {
  const [q, setQ] = useState('')
  const [feature, setFeature] = useState<string>('all')

  const features = useMemo(
    () => Array.from(new Set(sessions.map(scopeOf))).sort(),
    [sessions],
  )

  const filtered = useMemo(
    () =>
      sessions.filter(
        (s) =>
          (feature === 'all' || scopeOf(s) === feature) && matchesQuery(s, q),
      ),
    [sessions, q, feature],
  )

  // Live aggregate over the CURRENT filter (so the numbers always describe what
  // the customer is looking at, not the unfiltered whole).
  const agg = useMemo(
    () =>
      filtered.reduce(
        (a, s) => {
          a.tokens += s.total_tokens ?? 0
          a.cost += s.cost_usd ?? 0
          a.turns += s.turns ?? 0
          return a
        },
        { tokens: 0, cost: 0, turns: 0 },
      ),
    [filtered],
  )

  const columns: DataTableColumn<AgentSession>[] = [
    {
      key: 'start',
      header: 'Started',
      cell: (r) => (
        <span title={r.start ?? ''}>{relTime(r.start ?? r.date)}</span>
      ),
      sortValue: (r) => r.start ?? '',
      align: 'left',
    },
    {
      key: 'name',
      header: 'Run',
      cell: (r) => (
        <span className="font-medium text-card-foreground">
          {r.name ?? r.feature ?? '—'}
          <ConfidenceHint confidence={r.confidence} />
        </span>
      ),
      sortValue: (r) => r.name ?? r.feature ?? '',
    },
    {
      key: 'status',
      header: 'Status',
      cell: (r) => <StatusBadge status={r.status} />,
      sortValue: (r) => r.status ?? '',
    },
    {
      key: 'turns',
      header: 'Turns',
      cell: (r) => compactNumber(r.turns ?? 0),
      sortValue: (r) => r.turns ?? 0,
      align: 'right',
    },
    {
      key: 'tools',
      header: 'Tools',
      cell: (r) => compactNumber(r.tool_calls ?? 0),
      sortValue: (r) => r.tool_calls ?? 0,
      align: 'right',
    },
    {
      key: 'tokens',
      header: 'Tokens',
      cell: (r) => compactNumber(r.total_tokens ?? 0),
      sortValue: (r) => r.total_tokens ?? 0,
      align: 'right',
    },
    {
      key: 'cost',
      header: 'Cost',
      cell: (r) => usd(r.cost_usd ?? 0),
      sortValue: (r) => r.cost_usd ?? 0,
      align: 'right',
    },
    {
      key: 'duration',
      header: 'Duration',
      cell: (r) => duration(r.duration_seconds ?? 0),
      sortValue: (r) => r.duration_seconds ?? 0,
      align: 'right',
    },
  ]

  return (
    <div className="flex flex-col gap-2" data-testid="session-explorer">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <label className="relative flex items-center">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-2 size-3.5 text-muted-foreground"
            />
            <input
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search sessions…"
              data-testid="session-search"
              className="h-8 w-48 rounded-md border border-border bg-background pl-7 pr-2 text-xs text-card-foreground outline-none focus:border-ring"
            />
          </label>
          <select
            value={feature}
            onChange={(e) => setFeature(e.target.value)}
            data-testid="session-feature-filter"
            className="h-8 rounded-md border border-border bg-background px-2 text-xs text-card-foreground outline-none focus:border-ring"
          >
            <option value="all">All features ({features.length})</option>
            {features.map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </div>
        <div
          className="text-2xs text-muted-foreground tabular-metric"
          data-testid="session-filter-summary"
        >
          {filtered.length} of {sessions.length} · {compactNumber(agg.tokens)}{' '}
          tokens · {usd(agg.cost)}
        </div>
      </div>
      <DataTable
        columns={columns}
        rows={filtered}
        rowKey={(r, i) => r.session_id ?? `${i}`}
        caption="Agent sessions (newest first; click a column to sort)"
        empty="No sessions match the current filter."
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* The shared agent view (used by both PLAN_AW and WORK_AW)                    */
/* -------------------------------------------------------------------------- */

function topEntries(
  rec: Record<string, { sessions: number; total_tokens: number; cost_usd: number }>,
  by: 'total_tokens' | 'sessions',
  n: number,
) {
  return Object.entries(rec)
    .sort((a, b) => b[1][by] - a[1][by])
    .slice(0, n)
}

function AgentView({
  data,
  noun,
}: {
  data: AgentActivity
  noun: string
}) {
  const agg = data.aggregate
  const active = data.active_session

  // Per-day activity (chronological).
  const days = Object.keys(data.by_day).sort()
  const daySessions = days.map((d) => data.by_day[d]?.sessions ?? 0)

  // Top features by token spend (where this agent spent its effort).
  const topFeat = topEntries(data.by_feature, 'total_tokens', 8)

  return (
    <div className="flex flex-col gap-4" data-testid="agent-view">
      {/* KPI strip over the agent's full history */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label="Sessions"
          value={compactNumber(data.session_count)}
          status="neutral"
          hint={`${data.features.length} features`}
        />
        <StatTile
          label="Total tokens"
          value={compactNumber(agg.total_tokens ?? 0)}
          status="neutral"
          hint={`${percent(agg.cache_read_ratio ?? 0)} cache-read`}
        />
        <StatTile
          label="Total cost"
          value={usd(agg.cost_usd ?? 0)}
          status="neutral"
          hint={`${compactNumber(agg.turns ?? 0)} turns`}
        />
        <StatTile
          label={`Active ${noun}`}
          value={data.active ? '● live' : 'idle'}
          status={data.active ? 'success' : 'neutral'}
          hint={
            data.active && active
              ? `${active.name ?? active.feature ?? '—'} · ${relTime(active.start ?? active.date)}`
              : 'no run in the last 12 min'
          }
        />
      </div>

      {/* Attribution + incomplete callout — honest about what's known */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-2xs text-muted-foreground">
        <span data-testid="agent-attribution">
          {data.attributed_count} of {data.session_count} attributed to a feature
        </span>
        {data.incomplete_count > 0 ? (
          <span className="text-warning" data-testid="agent-incomplete">
            ⚠ {data.incomplete_count} incomplete (started, no plan produced)
          </span>
        ) : null}
      </div>

      {/* The active run, surfaced (active state the user explicitly asked for) */}
      {active ? (
        <div
          data-testid="agent-active-card"
          className={cn(
            'rounded-lg border p-3',
            data.active ? 'border-success/40 bg-success/5' : 'border-border',
          )}
        >
          <div className="mb-1.5 flex items-center justify-between">
            <h3 className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
              {data.active ? `Active ${noun}` : `Most recent ${noun}`}
            </h3>
            <StatusBadge status={active.status} />
          </div>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs md:grid-cols-4">
            <div>
              <div className="text-2xs uppercase text-muted-foreground">Run</div>
              <div className="font-medium text-card-foreground">
                {active.name ?? active.feature ?? '—'}
                <ConfidenceHint confidence={active.confidence} />
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase text-muted-foreground">Started</div>
              <div className="tabular-metric">{relTime(active.start)}</div>
            </div>
            <div>
              <div className="text-2xs uppercase text-muted-foreground">Tokens</div>
              <div className="tabular-metric">
                {compactNumber(active.total_tokens ?? 0)}
              </div>
            </div>
            <div>
              <div className="text-2xs uppercase text-muted-foreground">Cost</div>
              <div className="tabular-metric">{usd(active.cost_usd ?? 0)}</div>
            </div>
          </div>
        </div>
      ) : null}

      {/* Activity over time + where effort went */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {days.length > 0 ? (
          <div>
            <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
              Activity by day
            </h3>
            <BarSeries
              categories={days.map((d) => d.slice(5))}
              series={[
                { name: 'Sessions', data: daySessions, status: 'neutral' },
              ]}
              ariaLabel={`${noun} sessions per day`}
              valueFormatter={(v) => `${Math.round(v)}`}
            />
          </div>
        ) : null}
        {topFeat.length > 0 ? (
          <div>
            <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
              Top features by tokens
            </h3>
            <BarSeries
              horizontal
              categories={topFeat.map(([f]) => f)}
              series={[
                {
                  name: 'Tokens',
                  data: topFeat.map(([, v]) => v.total_tokens),
                  status: 'neutral',
                },
              ]}
              ariaLabel={`${noun} token spend by feature`}
              valueFormatter={(v) => compactNumber(v)}
            />
          </div>
        ) : null}
      </div>

      {/* The full, filterable session history */}
      <SessionExplorer sessions={data.sessions} />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* The two registered pillar panels                                            */
/* -------------------------------------------------------------------------- */

/** Plan task-completion badge. */
function PlanStatusBadge({ status }: { status: string | null | undefined }) {
  const s = status ?? 'open'
  const tone =
    s === 'complete'
      ? 'border-success/40 text-success'
      : s === 'in_progress'
        ? 'border-warning/40 text-warning'
        : 'border-border text-muted-foreground'
  const label =
    s === 'complete' ? '✓ complete' : s === 'in_progress' ? '◐ in progress' : 'open'
  return (
    <span
      className={cn(
        'inline-flex rounded-md border px-1.5 py-0.5 text-2xs font-medium',
        tone,
      )}
    >
      {label}
    </span>
  )
}

/** A tiny done/total progress bar. */
function TaskProgress({ done, total }: { done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-success"
          style={{ width: `${pct}%` }}
          aria-hidden="true"
        />
      </div>
      <span className="tabular-metric text-2xs text-muted-foreground">
        {done}/{total}
      </span>
    </div>
  )
}

/**
 * The PLAN_AW view: the planner's OUTPUT — every plan AUTHORED (a full Write of a
 * work/<f>/plan.md), attributed by that action regardless of which session wrote
 * it. This is the honest planner view for an operator who plans INLINE while
 * building (so "pure planner sessions" barely exist, but the PLANS do). Each plan
 * shows its feature, when authored, size + completion, and status — searchable,
 * filterable by status, sortable.
 */
function PlansView({ data }: { data: AgentsResponse['plan'] }) {
  const plans = data.plans ?? []
  const [q, setQ] = useState('')
  const [statusF, setStatusF] = useState('all')

  const filtered = useMemo(
    () =>
      plans.filter(
        (p) =>
          (statusF === 'all' || p.status === statusF) &&
          (!q || (p.feature ?? '').toLowerCase().includes(q.toLowerCase())),
      ),
    [plans, q, statusF],
  )

  const tasksPlanned = plans.reduce((a, p) => a + (p.tasks_total ?? 0), 0)
  const tasksDone = plans.reduce((a, p) => a + (p.tasks_done ?? 0), 0)
  const completePlans = plans.filter((p) => p.status === 'complete').length
  const featureCount = new Set(plans.map((p) => p.feature)).size

  const columns: DataTableColumn<(typeof plans)[number]>[] = [
    {
      key: 'feature',
      header: 'Plan',
      cell: (p) => (
        <span className="font-medium text-card-foreground">{p.feature}</span>
      ),
      sortValue: (p) => p.feature,
    },
    {
      key: 'authored',
      header: 'Authored',
      cell: (p) => <span title={p.authored ?? ''}>{relTime(p.authored)}</span>,
      sortValue: (p) => p.authored ?? '',
    },
    {
      key: 'tasks',
      header: 'Tasks done',
      cell: (p) => (
        <TaskProgress done={p.tasks_done ?? 0} total={p.tasks_total ?? 0} />
      ),
      sortValue: (p) =>
        p.tasks_total ? (p.tasks_done ?? 0) / p.tasks_total : -1,
    },
    {
      key: 'status',
      header: 'Status',
      cell: (p) => <PlanStatusBadge status={p.status} />,
      sortValue: (p) => p.status ?? '',
    },
  ]

  return (
    <div className="flex flex-col gap-4" data-testid="plans-view">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label="Plans authored"
          value={compactNumber(plans.length)}
          status="neutral"
          hint={`${featureCount} features`}
        />
        <StatTile
          label="Tasks planned"
          value={compactNumber(tasksPlanned)}
          status="neutral"
          hint={`${tasksDone} done`}
        />
        <StatTile
          label="Completed plans"
          value={`${completePlans}/${plans.length}`}
          status={completePlans === plans.length && plans.length ? 'success' : 'neutral'}
          hint="all tasks ✓"
        />
        <StatTile
          label="Task completion"
          value={percent(tasksPlanned ? tasksDone / tasksPlanned : 0, 0)}
          status="neutral"
          hint="across all plans"
        />
      </div>

      <div className="text-2xs text-muted-foreground">
        Plans are attributed by the <em>authoring action</em> (a full Write of a
        plan.md) — independent of the session that wrote it. The build effort lives
        in WORK_AW.
      </div>

      <div className="flex flex-col gap-2" data-testid="plans-explorer">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative flex items-center">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-2 size-3.5 text-muted-foreground"
              />
              <input
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search plans…"
                data-testid="plans-search"
                className="h-8 w-48 rounded-md border border-border bg-background pl-7 pr-2 text-xs text-card-foreground outline-none focus:border-ring"
              />
            </label>
            <select
              value={statusF}
              onChange={(e) => setStatusF(e.target.value)}
              data-testid="plans-status-filter"
              className="h-8 rounded-md border border-border bg-background px-2 text-xs text-card-foreground outline-none focus:border-ring"
            >
              <option value="all">All status</option>
              <option value="complete">Complete</option>
              <option value="in_progress">In progress</option>
              <option value="open">Open</option>
            </select>
          </div>
          <div className="text-2xs text-muted-foreground tabular-metric">
            {filtered.length} of {plans.length} plans
          </div>
        </div>
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(p, i) => p.feature ?? `${i}`}
          caption="Plans authored (newest first; click a column to sort)"
          empty="No plans match the current filter."
        />
      </div>
    </div>
  )
}

export function PlannerPanel({ panel, query }: PanelProps<AgentsResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="PLAN_AW — the planner's output: every plan authored, its size & completion"
      query={query}
      span={12}
      isEmpty={(d) => (d.plan?.plans?.length ?? 0) === 0}
      emptyTitle="No plans authored yet"
      emptyDescription="Run /agentware-plan (or the loop's PLAN phase) to author a plan.md."
    >
      {(data) => <PlansView data={data.plan} />}
    </PanelFrame>
  )
}

export function WorkerPanel({ panel, query }: PanelProps<AgentsResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="WORK_AW — the worker agent: active run + all previous worker metrics"
      query={query}
      span={12}
      headerRight={
        <ActiveBadge active={query.data?.work?.active ?? false} noun="worker" />
      }
      isEmpty={(d) => (d.work?.session_count ?? 0) === 0}
      emptyTitle="No worker sessions yet"
      emptyDescription="Run ./agentware.sh <feature> (or an execution session) to populate worker metrics."
    >
      {(data) => <AgentView data={data.work} noun="worker" />}
    </PanelFrame>
  )
}

/** Planner icon (nav) + worker icon are re-exported for the registry. */
export const PlannerIcon = ClipboardList
export const WorkerIcon = Hammer
