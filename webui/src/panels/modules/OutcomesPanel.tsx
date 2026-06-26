import { useNavigate } from 'react-router-dom'

import { BarSeries } from '@/components/charts'
import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import type { OutcomeRow, OutcomesResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import { taskTimelinePath } from '@/panels/drilldown/paths'
import type { PanelProps } from '@/panels/registry'
import { percent } from '@/panels/format'

/**
 * Run-outcome / success-rate panel (Task 21). Reads the terminal outcomes per
 * feature (completed / hit_max_iterations / post_hook_failure / pre_hook_abort)
 * and surfaces a success rate, an outcome-mix bar, and a per-feature table with
 * iterations-used + self-heal counts.
 */
const columns: DataTableColumn<OutcomeRow>[] = [
  {
    key: 'feature',
    header: 'Feature',
    cell: (r) => <span className="font-medium">{r.feature}</span>,
    sortValue: (r) => r.feature,
  },
  {
    key: 'outcome',
    header: 'Outcome',
    cell: (r) => (
      <span
        data-status={r.outcome === 'completed' ? 'success' : 'warning'}
        className={
          r.outcome === 'completed' ? 'text-success' : 'text-warning'
        }
      >
        {r.outcome}
      </span>
    ),
    sortValue: (r) => r.outcome,
  },
  {
    key: 'iterations',
    header: 'Iters',
    align: 'right',
    cell: (r) => r.iterations_used ?? '—',
    sortValue: (r) => r.iterations_used ?? 0,
  },
  {
    key: 'selfheal',
    header: 'Self-heal',
    align: 'right',
    cell: (r) => r.self_heal_count ?? 0,
    sortValue: (r) => r.self_heal_count ?? 0,
  },
]

export function OutcomesPanel({ panel, query }: PanelProps<OutcomesResponse>) {
  const navigate = useNavigate()
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Terminal run outcomes & success rate"
      query={query}
      span={12}
      isEmpty={(d) => d.features.length === 0}
    >
      {(data) => {
        const total = data.features.length
        const completed = data.summary.completed ?? 0
        const rate = total ? completed / total : 0
        const outcomeKeys = Object.keys(data.summary)
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <StatTile
                label="Success rate"
                value={percent(rate, 0)}
                status={
                  rate >= 0.8 ? 'success' : rate >= 0.5 ? 'warning' : 'danger'
                }
                hint={`${completed}/${total} completed`}
              />
              <StatTile
                label="Runs"
                value={total}
                status="neutral"
                hint="features with an outcome"
              />
              <StatTile
                label="Outcome types"
                value={outcomeKeys.length}
                status="neutral"
                hint="distinct terminal states"
              />
            </div>
            {outcomeKeys.length > 0 ? (
              <BarSeries
                categories={outcomeKeys}
                series={[
                  {
                    name: 'Runs',
                    data: outcomeKeys.map((k) => data.summary[k] ?? 0),
                  },
                ]}
                ariaLabel="Outcome mix"
                height={180}
              />
            ) : null}
            <DataTable
              columns={columns}
              rows={data.features}
              rowKey={(r) => r.feature}
              onRowClick={(r) => navigate(taskTimelinePath(r.feature))}
              caption="Per-feature run outcomes"
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
