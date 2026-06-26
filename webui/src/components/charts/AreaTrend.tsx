import { useMemo } from 'react'
import { useTheme } from '@/theme/ThemeProvider'
import { EChart } from './EChart'
import type { EChartsOption } from './echarts-core'
import { statusColor } from './echarts-theme'
import { totalPoints, type CategoryChartProps } from './types'

/**
 * AreaTrend (Task 20) — a filled line for cumulative/volume trends (KB growth,
 * token burn, context-tax over time) where the area under the curve communicates
 * magnitude. A low-opacity gradient fill keeps the Vercel/Linear restraint.
 */
export function AreaTrend({
  categories,
  series,
  height,
  width,
  className,
  ariaLabel,
  valueFormatter,
  showLegend,
}: CategoryChartProps) {
  const { theme } = useTheme()

  const option = useMemo<EChartsOption>(
    () => ({
      legend: { show: showLegend ?? series.length > 1, top: 0 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', boundaryGap: false, data: categories },
      yAxis: {
        type: 'value',
        axisLabel: valueFormatter ? { formatter: valueFormatter } : undefined,
      },
      series: series.map((s) => {
        const color = s.status ? statusColor(s.status, theme) : undefined
        return {
          name: s.name,
          type: 'line',
          smooth: true,
          showSymbol: true,
          symbol: 'circle',
          symbolSize: 5,
          data: s.data,
          areaStyle: { opacity: 0.12 },
          ...(color ? { itemStyle: { color }, lineStyle: { color } } : {}),
        }
      }),
    }),
    [categories, series, valueFormatter, showLegend, theme],
  )

  return (
    <EChart
      testId="area-trend"
      option={option}
      pointCount={totalPoints(series)}
      height={height}
      width={width}
      className={className}
      ariaLabel={ariaLabel ?? 'Area trend chart'}
    />
  )
}
