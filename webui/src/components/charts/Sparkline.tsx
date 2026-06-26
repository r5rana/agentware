import { useMemo } from 'react'
import { useTheme } from '@/theme/ThemeProvider'
import { EChart } from './EChart'
import type { EChartsOption } from './echarts-core'
import { statusColor } from './echarts-theme'
import type { SemanticStatus } from '@/lib/design-tokens'

/**
 * Sparkline (Task 20) — a minimal, axis-less inline trend for StatTiles / table
 * rows. No grid, no labels, no tooltip chrome: just the shape of the recent series
 * so a metric tile carries its own context at a glance (Linear/Mercury pattern).
 */
export interface SparklineProps {
  data: number[]
  status?: SemanticStatus
  /** Render as a filled area instead of a bare line. */
  area?: boolean
  height?: number
  width?: number
  className?: string
  ariaLabel?: string
}

export function Sparkline({
  data,
  status,
  area = true,
  height = 40,
  width,
  className,
  ariaLabel,
}: SparklineProps) {
  const { theme } = useTheme()
  const color = status ? statusColor(status, theme) : undefined

  const option = useMemo<EChartsOption>(
    () => ({
      grid: { left: 1, right: 1, top: 2, bottom: 2 },
      xAxis: {
        type: 'category',
        show: false,
        boundaryGap: false,
        data: data.map((_, i) => i),
      },
      yAxis: { type: 'value', show: true, scale: true, axisLabel: { show: false } },
      tooltip: { show: false },
      series: [
        {
          type: 'line',
          data,
          smooth: true,
          showSymbol: true,
          symbol: 'circle',
          symbolSize: 3,
          lineStyle: { width: 1.5, ...(color ? { color } : {}) },
          ...(color ? { itemStyle: { color } } : {}),
          ...(area ? { areaStyle: { opacity: 0.12, ...(color ? { color } : {}) } } : {}),
        },
      ],
    }),
    [data, area, color],
  )

  return (
    <EChart
      testId="sparkline"
      option={option}
      pointCount={data.length}
      height={height}
      width={width}
      className={className}
      ariaLabel={ariaLabel ?? 'Sparkline'}
    />
  )
}
