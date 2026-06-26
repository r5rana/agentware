import { useMemo } from 'react'
import { useTheme } from '@/theme/ThemeProvider'
import { EChart } from './EChart'
import type { EChartsOption } from './echarts-core'
import { statusColor } from './echarts-theme'
import { totalPoints, type CategoryChartProps } from './types'

/**
 * LineTrend (Task 20) — a smooth multi-series line for time/quality/cost trends
 * (retrieval quality, success rate, scaling, cost-by-day). Themed via the shared
 * token-bound EChart wrapper; circle symbols on every point keep the line readable
 * at a glance and make each datum a discrete, inspectable SVG mark.
 */
export function LineTrend({
  categories,
  series,
  height,
  width,
  className,
  ariaLabel,
  valueFormatter,
  showLegend,
  commitMarkers,
}: CategoryChartProps) {
  const { theme } = useTheme()

  // Only markers whose category is actually on the axis are drawn, so a stray
  // SHA never produces a floating, mis-aligned line.
  const markers = useMemo(
    () => (commitMarkers ?? []).filter((m) => categories.includes(m.category)),
    [commitMarkers, categories],
  )

  const option = useMemo<EChartsOption>(
    () => ({
      legend: { show: showLegend ?? series.length > 1, top: 0 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', boundaryGap: false, data: categories },
      yAxis: {
        type: 'value',
        axisLabel: valueFormatter ? { formatter: valueFormatter } : undefined,
      },
      series: series.map((s, i) => ({
        name: s.name,
        type: 'line',
        smooth: true,
        showSymbol: true,
        symbol: 'circle',
        symbolSize: 6,
        data: s.data,
        ...(s.status ? { itemStyle: { color: statusColor(s.status, theme) } } : {}),
        // Attach the commit markers to the FIRST series only (one set per chart).
        ...(i === 0 && markers.length > 0
          ? {
              markLine: {
                silent: true,
                symbol: 'none',
                lineStyle: { type: 'dashed', opacity: 0.5 },
                label: { formatter: (p: { name?: string }) => p.name ?? '' },
                data: markers.map((m) => ({
                  xAxis: m.category,
                  name: m.label,
                })),
              },
            }
          : {}),
      })),
    }),
    [categories, series, valueFormatter, showLegend, theme, markers],
  )

  return (
    <EChart
      testId="line-trend"
      option={option}
      pointCount={totalPoints(series)}
      markerCount={markers.length}
      height={height}
      width={width}
      className={className}
      ariaLabel={ariaLabel ?? 'Line trend chart'}
    />
  )
}
