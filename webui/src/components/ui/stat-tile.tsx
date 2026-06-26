import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import type { SemanticStatus } from '@/lib/design-tokens'
import { STATUS_TOKEN } from '@/lib/design-tokens'
import { cn } from '@/lib/utils'
import { Card } from './card'
import { TrendBadge, type TrendBadgeProps } from './trend-badge'

/**
 * StatTile primitive (Task 19) — the canonical north-star metric card used across
 * the Overview + every panel header. A big tabular number with strong numeric
 * hierarchy, a label, an optional hint, a single semantic status dot (color =
 * meaning), an optional trend badge, and an optional leading icon. Prop-driven so
 * the KPI strip and panels render identically.
 */
export interface StatTileProps {
  label: string
  value: ReactNode
  hint?: string
  status?: SemanticStatus
  trend?: TrendBadgeProps
  icon?: LucideIcon
  className?: string
}

export function StatTile({
  label,
  value,
  hint,
  status = 'neutral',
  trend,
  icon: Icon,
  className,
}: StatTileProps) {
  const token = STATUS_TOKEN[status]
  return (
    <Card
      data-testid="stat-tile"
      data-status={status}
      className={cn('p-4', className)}
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
          {Icon ? <Icon aria-hidden="true" className="size-3.5" /> : null}
          {label}
        </span>
        <span
          aria-hidden="true"
          data-testid="stat-tile-status-dot"
          className={cn('inline-block h-1.5 w-1.5 rounded-full', token.bg)}
        />
      </div>
      <div className="mt-2 flex items-end justify-between gap-2">
        <div className="tabular-metric text-2xl font-semibold text-card-foreground">
          {value}
        </div>
        {trend ? <TrendBadge {...trend} /> : null}
      </div>
      {hint ? <div className="mt-1 text-2xs text-muted-foreground">{hint}</div> : null}
    </Card>
  )
}
