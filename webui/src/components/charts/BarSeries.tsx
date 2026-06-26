import { useMemo } from 'react'
import { useTheme } from '@/theme/ThemeProvider'
import { EChart } from './EChart'
import type { EChartsOption } from './echarts-core'
import { statusColor } from './echarts-theme'
import { totalPoints, type CategoryChartProps } from './types'

/**
 * BarSeries (Task 20) — grouped/single bars for categorical comparisons
 * (cost-by-model, per-phase token split, failure-tier counts). Rounded top caps +
 * the token-bound palette keep it consistent with the line family.
 */
export function BarSeries({
  categories,
  series,
  height,
  width,
  className,
  ariaLabel,
  valueFormatter,
  showLegend,
  horizontal = false,
}: CategoryChartProps & { horizontal?: boolean }) {
  const { theme } = useTheme()

  const option = useMemo<EChartsOption>(() => {
    const categoryAxis = { type: 'category' as const, data: categories }
    const valueAxis = {
      type: 'value' as const,
      axisLabel: valueFormatter ? { formatter: valueFormatter } : undefined,
    }
    return {
      legend: { show: showLegend ?? series.length > 1, top: 0 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: horizontal ? valueAxis : categoryAxis,
      yAxis: horizontal ? categoryAxis : valueAxis,
      series: series.map((s) => ({
        name: s.name,
        type: 'bar',
        data: s.data,
        barMaxWidth: 28,
        itemStyle: {
          borderRadius: horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
          ...(s.status ? { color: statusColor(s.status, theme) } : {}),
        },
      })),
    }
  }, [categories, series, valueFormatter, showLegend, horizontal, theme])

  return (
    <EChart
      testId="bar-series"
      option={option}
      pointCount={totalPoints(series)}
      height={height}
      width={width}
      className={className}
      ariaLabel={ariaLabel ?? 'Bar chart'}
    />
  )
}
