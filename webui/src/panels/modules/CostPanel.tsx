import { BarSeries, LineTrend } from '@/components/charts'
import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import type { CostResponse, SessionRow } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { compactNumber, percent, usd } from '@/panels/format'

/**
 * Cost & infra panel (Task 21). Total spend + tokens + cache-read ratio as
 * north-star tiles, cost attribution by MODEL (bar) and by DAY (line, with the
 * cost-anomaly flag from the backend), and a per-session table.
 */
const columns: DataTableColumn<SessionRow>[] = [
  {
    key: 'session',
    header: 'Session',
    cell: (s) => <span className="font-medium">{s.session_id}</span>,
    sortValue: (s) => s.session_id,
  },
  {
    key: 'stage',
    header: 'Stage',
    cell: (s) => (
      <span className="text-muted-foreground">{s.stage ?? '—'}</span>
    ),
    sortValue: (s) => s.stage ?? '',
  },
  {
    key: 'tokens',
    header: 'Tokens',
    align: 'right',
    cell: (s) => compactNumber(s.total_tokens ?? 0),
    sortValue: (s) => s.total_tokens ?? 0,
  },
  {
    key: 'cost',
    header: 'Cost',
    align: 'right',
    cell: (s) => usd(s.cost_usd ?? 0),
    sortValue: (s) => s.cost_usd ?? 0,
  },
]

export function CostPanel({ panel, query }: PanelProps<CostResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Spend attribution by model, day & session"
      query={query}
      span={12}
      isEmpty={(d) => d.session_count === 0 && d.sessions.length === 0}
    >
      {(data) => {
        const agg = data.aggregate
        const byModel = agg.by_model ?? {}
        const byDay = agg.by_day ?? {}
        const modelNames = Object.keys(byModel)
        const days = Object.keys(byDay).sort()
        const anomalies = new Set(agg.cost_anomaly_dates ?? [])
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile
                label="Total cost"
                value={usd(agg.cost_usd ?? 0)}
                status="neutral"
                hint={`${data.session_count} sessions`}
              />
              <StatTile
                label="Tokens"
                value={compactNumber(agg.total_tokens ?? 0)}
                status="neutral"
                hint="all sessions"
              />
              <StatTile
                label="Cache-read"
                value={percent(agg.cache_read_ratio ?? 0)}
                status="neutral"
                hint="of input tokens"
              />
              <StatTile
                label="Cost anomalies"
                value={anomalies.size}
                status={anomalies.size ? 'warning' : 'success'}
                hint="day spikes flagged"
              />
            </div>
            {modelNames.length > 0 ? (
              <div>
                <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  Cost by model
                </h3>
                <BarSeries
                  categories={modelNames.map((m) => m.replace(/^claude-/, ''))}
                  series={[
                    {
                      name: 'USD',
                      data: modelNames.map((m) => byModel[m]?.cost_usd ?? 0),
                    },
                  ]}
                  valueFormatter={(v) => usd(v)}
                  ariaLabel="Cost by model"
                  height={180}
                />
              </div>
            ) : null}
            {days.length > 0 ? (
              <div>
                <h3 className="mb-1 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                  Cost by day
                </h3>
                <LineTrend
                  categories={days}
                  series={[
                    {
                      name: 'USD',
                      data: days.map((d) => byDay[d]?.cost_usd ?? 0),
                      status: anomalies.size ? 'warning' : 'success',
                    },
                  ]}
                  valueFormatter={(v) => usd(v)}
                  ariaLabel="Cost by day"
                  height={180}
                />
              </div>
            ) : null}
            <DataTable
              columns={columns}
              rows={data.sessions}
              rowKey={(s) => s.session_id}
              caption="Per-session cost"
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
