import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import type { HealthCheck, HealthResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'

/**
 * Health panel (Task 21) — the `agentware audit` golden-signal surface. A
 * north-star tile (passing checks / total) plus a sortable table of every check
 * with its semantic status + details. Color = meaning (green pass / red fail).
 */
const columns: DataTableColumn<HealthCheck>[] = [
  {
    key: 'name',
    header: 'Check',
    cell: (c) => <span className="font-medium">{c.name}</span>,
    sortValue: (c) => c.name,
  },
  {
    key: 'status',
    header: 'Status',
    align: 'center',
    cell: (c) => (
      <span
        data-status={c.ok ? 'success' : 'danger'}
        className={c.ok ? 'text-success' : 'text-danger'}
      >
        {c.ok ? 'pass' : 'fail'}
      </span>
    ),
    sortValue: (c) => (c.ok ? 1 : 0),
  },
  {
    key: 'details',
    header: 'Details',
    cell: (c) => (
      <span className="text-2xs text-muted-foreground">
        {c.details.length ? c.details.join('; ') : '—'}
      </span>
    ),
  },
]

export function HealthPanel({ panel, query }: PanelProps<HealthResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="agentware audit — knowledge-base health checks"
      query={query}
      span={12}
      isEmpty={(d) => d.checks.length === 0}
    >
      {(data) => {
        const total = data.checks.length
        const passing = data.checks.filter((c) => c.ok).length
        const allOk = passing === total
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <StatTile
                label="Health"
                value={allOk ? 'Healthy' : 'Degraded'}
                status={allOk ? 'success' : 'danger'}
                hint={`${passing}/${total} checks passing`}
              />
              <StatTile
                label="Checks"
                value={total}
                status="neutral"
                hint="audit checks run"
              />
              <StatTile
                label="Failing"
                value={total - passing}
                status={total - passing === 0 ? 'success' : 'danger'}
                hint="needs attention"
              />
            </div>
            <DataTable
              columns={columns}
              rows={data.checks}
              rowKey={(c) => c.name}
              caption="Knowledge-base health checks"
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
