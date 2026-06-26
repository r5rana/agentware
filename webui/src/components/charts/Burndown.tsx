import { useMemo } from 'react'
import { Group } from '@visx/group'
import { scaleLinear } from '@visx/scale'
import { AreaClosed, LinePath } from '@visx/shape'
import { curveMonotoneX } from '@visx/curve'
import { cn } from '@/lib/utils'
import { useTheme } from '@/theme/ThemeProvider'
import { statusColor, token } from './echarts-theme'

/**
 * Loop BURNDOWN (Task 20) — the signature loop visualisation: tasks-remaining
 * across iterations. Built with visx (MIT) rather than ECharts because it is a
 * bespoke, opinionated viz (an ideal-pace reference line + the actual burndown)
 * where hand-drawn SVG is cleaner than bending a generic chart. Renders a logical
 * coordinate box scaled responsively via `viewBox` (no ResizeObserver needed), so
 * it is fully deterministic under test.
 */
export interface BurndownPoint {
  iteration: number
  remaining: number
}

export interface BurndownProps {
  data: BurndownPoint[]
  /** Show the dashed ideal-pace reference (linear burn to zero). */
  showIdeal?: boolean
  height?: number
  className?: string
  ariaLabel?: string
}

const VB_WIDTH = 600
const MARGIN = { top: 12, right: 16, bottom: 24, left: 32 }

export function Burndown({
  data,
  showIdeal = true,
  height = 240,
  className,
  ariaLabel,
}: BurndownProps) {
  const { theme } = useTheme()
  const innerW = VB_WIDTH - MARGIN.left - MARGIN.right
  const innerH = height - MARGIN.top - MARGIN.bottom

  const accent = token('--ring', theme)
  const gridColor = token('--border', theme)

  const { xScale, yScale, idealData } = useMemo(() => {
    const maxIter = Math.max(1, ...data.map((d) => d.iteration))
    const maxRemaining = Math.max(1, ...data.map((d) => d.remaining))
    const x = scaleLinear({ domain: [0, maxIter], range: [0, innerW] })
    const y = scaleLinear({ domain: [0, maxRemaining], range: [innerH, 0] })
    const start = data.length ? data[0] : { iteration: 0, remaining: 0 }
    const ideal: BurndownPoint[] = [
      { iteration: start.iteration, remaining: start.remaining },
      { iteration: maxIter, remaining: 0 },
    ]
    return { xScale: x, yScale: y, idealData: ideal }
  }, [data, innerW, innerH])

  return (
    <svg
      data-testid="burndown"
      data-point-count={data.length}
      role="img"
      aria-label={ariaLabel ?? 'Loop burndown chart'}
      viewBox={`0 0 ${VB_WIDTH} ${height}`}
      width="100%"
      height={height}
      preserveAspectRatio="xMidYMid meet"
      className={cn('overflow-visible', className)}
    >
      <Group left={MARGIN.left} top={MARGIN.top}>
        {/* baseline */}
        <line
          x1={0}
          x2={innerW}
          y1={innerH}
          y2={innerH}
          stroke={gridColor}
          strokeWidth={1}
        />
        {showIdeal && data.length > 0 && (
          <LinePath
            data-testid="burndown-ideal"
            data={idealData}
            x={(d) => xScale(d.iteration)}
            y={(d) => yScale(d.remaining)}
            stroke={token('--muted-foreground', theme)}
            strokeWidth={1}
            strokeDasharray="4 4"
          />
        )}
        <AreaClosed
          data={data}
          x={(d) => xScale(d.iteration)}
          y={(d) => yScale(d.remaining)}
          yScale={yScale}
          curve={curveMonotoneX}
          fill={accent}
          fillOpacity={0.12}
        />
        <LinePath
          data-testid="burndown-line"
          data={data}
          x={(d) => xScale(d.iteration)}
          y={(d) => yScale(d.remaining)}
          curve={curveMonotoneX}
          stroke={accent}
          strokeWidth={2}
        />
        {data.map((d, i) => (
          <circle
            key={i}
            data-testid="burndown-point"
            cx={xScale(d.iteration)}
            cy={yScale(d.remaining)}
            r={3}
            fill={d.remaining === 0 ? statusColor('success', theme) : accent}
          />
        ))}
      </Group>
    </svg>
  )
}
