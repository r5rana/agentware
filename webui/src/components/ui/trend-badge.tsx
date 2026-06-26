import { ArrowDown, ArrowRight, ArrowUp } from 'lucide-react'
import type { SemanticStatus } from '@/lib/design-tokens'
import { STATUS_TOKEN } from '@/lib/design-tokens'
import { cn } from '@/lib/utils'

/**
 * TrendBadge primitive (Task 19) — a compact delta pill showing direction (up /
 * down / flat) with semantic color. Direction alone is NOT meaning: for cost,
 * "up" is bad; for recall, "up" is good. So `goodWhen` maps the direction to a
 * semantic status, keeping color = meaning consistent across the system.
 */
export type TrendDirection = 'up' | 'down' | 'flat'

/** Which direction is "good" for this metric (drives the semantic color). */
export type TrendPolarity = 'up' | 'down' | 'neutral'

export interface TrendBadgeProps {
  /** Direction of the change. If omitted, derived from `value`'s sign. */
  direction?: TrendDirection
  /** The formatted delta label, e.g. "+12%", "-3", "0". */
  value: string
  /** Which direction counts as healthy. Default: up is good. */
  goodWhen?: TrendPolarity
  className?: string
}

const ICON: Record<TrendDirection, typeof ArrowUp> = {
  up: ArrowUp,
  down: ArrowDown,
  flat: ArrowRight,
}

/** Map a direction + polarity to the semantic status that colors the badge. */
export function trendStatus(
  direction: TrendDirection,
  goodWhen: TrendPolarity,
): SemanticStatus {
  if (direction === 'flat' || goodWhen === 'neutral') return 'neutral'
  const good = direction === goodWhen
  return good ? 'success' : 'danger'
}

export function TrendBadge({
  direction,
  value,
  goodWhen = 'up',
  className,
}: TrendBadgeProps) {
  const dir: TrendDirection =
    direction ??
    (value.trimStart().startsWith('-')
      ? 'down'
      : /[1-9]/.test(value)
        ? 'up'
        : 'flat')
  const status = trendStatus(dir, goodWhen)
  const token = STATUS_TOKEN[status]
  const Icon = ICON[dir]
  return (
    <span
      data-testid="trend-badge"
      data-direction={dir}
      data-status={status}
      className={cn(
        'inline-flex items-center gap-0.5 rounded-md px-1.5 py-0.5 text-2xs font-medium tabular-nums',
        'bg-muted',
        token.fg,
        className,
      )}
    >
      <Icon aria-hidden="true" className="size-3" />
      {value}
    </span>
  )
}
