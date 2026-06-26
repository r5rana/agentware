import { LineTrend } from '@/components/charts'
import { StatTile } from '@/components/ui'
import type { ScalingResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { percent } from '@/panels/format'

/**
 * Retrieval-scaling panel (Task 21) — Recall@k vs corpus size: does retrieval
 * quality hold as the knowledge base grows? Plots measured points ordered by
 * corpus size and reports the fitted slope (recall per entry).
 */
export function ScalingPanel({ panel, query }: PanelProps<ScalingResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Recall@k vs corpus size — does memory scale?"
      query={query}
      span={6}
      isEmpty={(d) => d.measured === 0 && d.points.length === 0}
    >
      {(data) => {
        const measured = data.points
          .filter(
            (p) => p.corpus_size != null && p.recall_at_k != null,
          )
          .sort((a, b) => (a.corpus_size ?? 0) - (b.corpus_size ?? 0))
        const slope = data.slope
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3">
              <StatTile
                label="Measured points"
                value={data.measured}
                status="neutral"
                hint={`${data.count} ledger rows`}
              />
              <StatTile
                label="Slope"
                value={slope == null ? '—' : slope.toFixed(4)}
                status={
                  slope == null
                    ? 'neutral'
                    : slope >= 0
                      ? 'success'
                      : 'danger'
                }
                hint="recall per entry"
              />
            </div>
            {measured.length > 0 ? (
              <LineTrend
                categories={measured.map((p) => String(p.corpus_size))}
                series={[
                  {
                    name: 'Recall@k',
                    data: measured.map((p) => p.recall_at_k ?? 0),
                    status: 'success',
                  },
                ]}
                valueFormatter={(v) => percent(v, 0)}
                ariaLabel="Recall vs corpus size"
              />
            ) : null}
          </div>
        )
      }}
    </PanelFrame>
  )
}
