import { LineTrend } from '@/components/charts'
import type { CommitMarker } from '@/components/charts/types'
import { StatTile } from '@/components/ui'
import type { LedgerRow, QualityResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { percent, shortSha } from '@/panels/format'
import { useAlerts } from '@/services/query'

/**
 * Retrieval-quality trend panel (Task 21). Plots the eval ledger's reliability +
 * Recall@k / nDCG / MRR across runs, with the latest reliability as a north-star
 * tile and the run-over-run delta as a trend badge (up = good for quality).
 *
 * Commit markers (Task 31): the `/api/alerts` ledger SHAs are drawn as vertical
 * markers on the trend so a metric shift can be correlated with the commit.
 */
function metric(row: LedgerRow, key: string): number {
  const v = row.metrics?.[key]
  return typeof v === 'number' ? v : 0
}

export function QualityPanel({ panel, query }: PanelProps<QualityResponse>) {
  const alertsQ = useAlerts()
  const commitMarkers: CommitMarker[] = (alertsQ.data?.commit_markers ?? [])
    .map((m) => {
      const label = shortSha(m.commit) || (m.run ?? '')
      return { category: label, label }
    })
    .filter((m) => m.label !== '')
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Eval ledger — reliability & retrieval metrics over time"
      query={query}
      span={12}
      isEmpty={(d) => d.series.length === 0}
    >
      {(data) => {
        const rows = data.series
        const categories = rows.map((r) => shortSha(r.commit) || (r.run ?? ''))
        const latest = data.latest ?? rows[rows.length - 1]
        const prev = rows.length > 1 ? rows[rows.length - 2] : undefined
        const latestRel = latest?.reliability ?? 0
        const delta =
          prev?.reliability != null && latest?.reliability != null
            ? latest.reliability - prev.reliability
            : 0
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile
                label="Reliability"
                value={`${latestRel.toFixed(0)}%`}
                status={
                  latestRel >= 80
                    ? 'success'
                    : latestRel >= 60
                      ? 'warning'
                      : 'danger'
                }
                trend={{
                  value: `${delta >= 0 ? '+' : ''}${delta.toFixed(0)}`,
                  goodWhen: 'up',
                }}
                hint="latest eval"
              />
              <StatTile
                label="Recall@k"
                value={percent(metric(latest ?? rows[0], 'recall_at_k'))}
                status="neutral"
                hint="latest"
              />
              <StatTile
                label="nDCG@k"
                value={percent(metric(latest ?? rows[0], 'ndcg_at_k'))}
                status="neutral"
                hint="latest"
              />
              <StatTile
                label="MRR"
                value={percent(metric(latest ?? rows[0], 'mrr'))}
                status="neutral"
                hint="latest"
              />
            </div>
            <LineTrend
              categories={categories}
              series={[
                {
                  name: 'Recall@k',
                  data: rows.map((r) => metric(r, 'recall_at_k')),
                },
                {
                  name: 'nDCG@k',
                  data: rows.map((r) => metric(r, 'ndcg_at_k')),
                },
                { name: 'MRR', data: rows.map((r) => metric(r, 'mrr')) },
              ]}
              valueFormatter={(v) => v.toFixed(2)}
              ariaLabel="Retrieval quality trend"
              commitMarkers={commitMarkers}
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
