import { LineTrend } from '@/components/charts'
import { StatTile } from '@/components/ui'
import type { ContextTaxResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { compactNumber, percent } from '@/panels/format'

/**
 * Context-tax panel (Task 21) — the "plans feel slower as the KB grows" signal:
 * avg context re-read per turn + injected MAIN.md footprint, plus context-window
 * utilization % with a truncation-risk flag. Series is plotted by day.
 */
export function ContextTaxPanel({
  panel,
  query,
}: PanelProps<ContextTaxResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Context re-read, injected footprint & window utilization"
      query={query}
      span={12}
      isEmpty={(d) => Object.keys(d.context_tax.by_day ?? {}).length === 0}
    >
      {(data) => {
        const ct = data.context_tax
        const days = Object.keys(ct.by_day ?? {}).sort()
        const byDay = ct.by_day ?? {}
        const windowPct = ct.context_window_pct ?? 0
        const risk = ct.truncation_risk ?? false
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile
                label="Injected"
                value={compactNumber(ct.injected_tokens ?? 0)}
                status="neutral"
                hint="MAIN.md footprint"
              />
              <StatTile
                label="Re-read / turn"
                value={compactNumber(ct.cache_read_per_turn ?? 0)}
                status="neutral"
                hint="avg cache-read tokens"
              />
              <StatTile
                label="Window used"
                value={percent(windowPct)}
                status={
                  windowPct >= (ct.truncation_threshold ?? 0.9)
                    ? 'danger'
                    : windowPct >= 0.6
                      ? 'warning'
                      : 'success'
                }
                hint="peak context-window %"
              />
              <StatTile
                label="Truncation risk"
                value={risk ? 'At risk' : 'Safe'}
                status={risk ? 'danger' : 'success'}
                hint={`threshold ${percent(ct.truncation_threshold ?? 0.9, 0)}`}
              />
            </div>
            {days.length > 0 ? (
              <LineTrend
                categories={days}
                series={[
                  {
                    name: 'Re-read / turn',
                    data: days.map((d) => byDay[d]?.cache_read_per_turn ?? 0),
                  },
                  {
                    name: 'Window %',
                    data: days.map(
                      (d) => (byDay[d]?.context_window_pct ?? 0) * 100,
                    ),
                  },
                ]}
                ariaLabel="Context tax by day"
              />
            ) : null}
          </div>
        )
      }}
    </PanelFrame>
  )
}
