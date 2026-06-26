import { useMemo } from 'react'
import { Group } from '@visx/group'
import { scaleBand, scaleLinear } from '@visx/scale'
import { cn } from '@/lib/utils'
import { useTheme } from '@/theme/ThemeProvider'
import type { SemanticStatus } from '@/lib/design-tokens'
import { statusColor, token } from './echarts-theme'

/**
 * Trace WATERFALL (Task 20) — a step-level run timeline (the Task-29 trace
 * explorer's core viz): each tool call / step is a horizontal bar positioned by its
 * start offset and sized by its duration, ordered top-to-bottom. Bespoke visx (MIT)
 * because a generic Gantt/bar chart can't express start-offset + status + ordering
 * cleanly. Responsive via `viewBox`; deterministic SVG for tests.
 */
export interface TraceStep {
  label: string
  /** Start offset (e.g. ms/s) from the run origin. */
  start: number
  /** Duration in the same unit. */
  duration: number
  status?: SemanticStatus
}

export interface TraceWaterfallProps {
  steps: TraceStep[]
  height?: number
  className?: string
  ariaLabel?: string
}

const VB_WIDTH = 600
const MARGIN = { top: 8, right: 16, bottom: 20, left: 120 }
const ROW_H = 22

export function TraceWaterfall({
  steps,
  height,
  className,
  ariaLabel,
}: TraceWaterfallProps) {
  const { theme } = useTheme()
  const resolvedHeight = height ?? MARGIN.top + MARGIN.bottom + steps.length * ROW_H
  const innerW = VB_WIDTH - MARGIN.left - MARGIN.right
  const innerH = resolvedHeight - MARGIN.top - MARGIN.bottom

  const labelColor = token('--muted-foreground', theme)
  const accent = token('--ring', theme)

  const { xScale, yScale } = useMemo(() => {
    const maxEnd = Math.max(1, ...steps.map((s) => s.start + s.duration))
    const x = scaleLinear({ domain: [0, maxEnd], range: [0, innerW] })
    const y = scaleBand({
      domain: steps.map((_, i) => i),
      range: [0, innerH],
      padding: 0.25,
    })
    return { xScale: x, yScale: y }
  }, [steps, innerW, innerH])

  const barHeight = yScale.bandwidth()

  return (
    <svg
      data-testid="trace-waterfall"
      data-point-count={steps.length}
      role="img"
      aria-label={ariaLabel ?? 'Trace waterfall chart'}
      viewBox={`0 0 ${VB_WIDTH} ${resolvedHeight}`}
      width="100%"
      height={resolvedHeight}
      preserveAspectRatio="xMidYMid meet"
      className={cn('overflow-visible', className)}
    >
      <Group left={MARGIN.left} top={MARGIN.top}>
        {steps.map((step, i) => {
          const y = yScale(i) ?? 0
          const fill = step.status ? statusColor(step.status, theme) : accent
          return (
            <Group key={i}>
              <text
                x={-8}
                y={y + barHeight / 2}
                textAnchor="end"
                dominantBaseline="middle"
                fontSize={11}
                fill={labelColor}
              >
                {step.label}
              </text>
              <rect
                data-testid="waterfall-bar"
                x={xScale(step.start)}
                y={y}
                width={Math.max(1, xScale(step.start + step.duration) - xScale(step.start))}
                height={barHeight}
                rx={3}
                fill={fill}
                fillOpacity={0.85}
              />
            </Group>
          )
        })}
      </Group>
    </svg>
  )
}
