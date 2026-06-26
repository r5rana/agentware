import { motion } from 'framer-motion'
import type { SemanticStatus } from '@/lib/design-tokens'
import { STATUS_TOKEN } from '@/lib/design-tokens'
import { cn } from '@/lib/utils'

/**
 * A KPI tile for the top metric strip (Task 15). The full StatTile primitive +
 * trend badges arrive in Task 19; this is the shell-level north-star card: a big
 * tabular metric, a label, and a single semantic status dot (color = meaning).
 */
export interface Kpi {
  id: string
  label: string
  value: string
  hint?: string
  status?: SemanticStatus
}

/** Placeholder north-star tiles so the shell is glanceable before the data layer. */
export const DEFAULT_KPIS: Kpi[] = [
  { id: 'loop', label: 'Active loop', value: 'idle', hint: 'no active run', status: 'neutral' },
  { id: 'success', label: 'Run success rate', value: '—', hint: 'last 30d', status: 'neutral' },
  { id: 'recall', label: 'Recall@10', value: '—', hint: 'latest eval', status: 'neutral' },
  { id: 'kb', label: 'KB entries', value: '—', hint: 'indexed', status: 'neutral' },
  { id: 'cost', label: 'Cost (30d)', value: '—', hint: 'all features', status: 'neutral' },
  { id: 'alerts', label: 'Open alerts', value: '—', hint: 'severity-ranked', status: 'neutral' },
]

export function KpiStrip({ kpis = DEFAULT_KPIS }: { kpis?: Kpi[] }) {
  return (
    <section
      aria-label="Key metrics"
      className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6"
    >
      {kpis.map((kpi, i) => {
        const status = STATUS_TOKEN[kpi.status ?? 'neutral']
        return (
          <motion.div
            key={kpi.id}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.18, delay: i * 0.02 }}
            className="rounded-lg border border-border bg-card p-4"
          >
            <div className="flex items-center justify-between">
              <span className="text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                {kpi.label}
              </span>
              <span
                aria-hidden="true"
                className={cn('inline-block h-1.5 w-1.5 rounded-full', status.bg)}
              />
            </div>
            <div className="tabular-metric mt-2 text-2xl font-semibold text-card-foreground">
              {kpi.value}
            </div>
            {kpi.hint ? (
              <div className="mt-1 text-2xs text-muted-foreground">{kpi.hint}</div>
            ) : null}
          </motion.div>
        )
      })}
    </section>
  )
}
