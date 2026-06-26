import { useMemo } from 'react'
import ReactEChartsCore from 'echarts-for-react/lib/core'
import { cn } from '@/lib/utils'
import { useTheme } from '@/theme/ThemeProvider'
import { echarts, type EChartsOption } from './echarts-core'
import { buildEchartsTheme } from './echarts-theme'

/**
 * Shared responsive ECharts container (Task 20). Every themed ECharts chart
 * (LineTrend / AreaTrend / BarSeries / Sparkline) renders through this wrapper so
 * they share: the SVG renderer, the token-bound theme (re-themes on light/dark
 * toggle), a responsive box that fills its grid cell, and a stable
 * `data-point-count` the panels + tests read.
 *
 * Sizing: the browser sizes the chart from its flex/grid cell (width 100% + a fixed
 * `height`). jsdom computes no layout, so an EXPLICIT `width` switches the chart to
 * fixed init dimensions — that is the only way ECharts paints marks under test
 * (otherwise clientWidth/Height are 0 and it renders an empty `<svg>`).
 */
export interface EChartProps {
  option: EChartsOption
  /** Total number of data points across all series — exposed as `data-point-count`. */
  pointCount: number
  /**
   * Number of commit/deployment markers drawn on the chart (Task 31) — exposed as
   * `data-marker-count` so panels + tests can assert markers render.
   */
  markerCount?: number
  /** Pixel height of the chart box. */
  height?: number
  /** Explicit pixel width — set in tests for deterministic SVG painting. */
  width?: number
  className?: string
  /** Accessible label for the chart region. */
  ariaLabel?: string
  testId?: string
}

export function EChart({
  option,
  pointCount,
  markerCount,
  height = 280,
  width,
  className,
  ariaLabel,
  testId = 'echart',
}: EChartProps) {
  const { theme } = useTheme()
  const themeObject = useMemo(() => buildEchartsTheme(theme), [theme])

  // Disable animation so the SVG is stable on first paint (deterministic tests +
  // no jank). Merge non-destructively so callers keep full control of the option.
  const mergedOption = useMemo<EChartsOption>(
    () => ({ animation: false, ...option }),
    [option],
  )

  const opts = useMemo(
    () =>
      width != null
        ? { renderer: 'svg' as const, width, height }
        : { renderer: 'svg' as const },
    [width, height],
  )

  return (
    <div
      data-testid={testId}
      data-point-count={pointCount}
      {...(markerCount != null ? { 'data-marker-count': markerCount } : {})}
      role="img"
      aria-label={ariaLabel}
      className={cn('w-full', className)}
      style={{ height }}
    >
      <ReactEChartsCore
        echarts={echarts}
        option={mergedOption}
        // A fresh theme object per toggle re-themes the chart; notMerge clears stale
        // series when the data shape changes.
        theme={themeObject}
        notMerge
        lazyUpdate
        opts={opts}
        style={{ width: width != null ? width : '100%', height }}
      />
    </div>
  )
}
