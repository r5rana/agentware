import type { SemanticStatus } from '@/lib/design-tokens'

/** One named series of numeric values aligned to a shared category axis. */
export interface ChartSeries {
  name: string
  data: number[]
  /** Optional semantic hue (color = meaning); defaults to the palette order. */
  status?: SemanticStatus
}

/**
 * A commit / deployment marker placed on a trend chart's category axis (Task 31)
 * so a metric shift can be correlated with the commit (ledger SHA) that caused it.
 * `category` MUST match one of the chart's `categories` values (e.g. the short
 * SHA); `label` is the short text shown at the marker (typically the SHA).
 */
export interface CommitMarker {
  category: string | number
  label: string
}

/** Props common to the category-axis charts (LineTrend / AreaTrend / BarSeries). */
export interface CategoryChartProps {
  categories: (string | number)[]
  series: ChartSeries[]
  height?: number
  width?: number
  className?: string
  ariaLabel?: string
  /** Format a value for the axis/tooltip (e.g. `$`, `%`). */
  valueFormatter?: (value: number) => string
  showLegend?: boolean
  /**
   * Vertical commit/deployment markers aligned to ledger SHAs (Task 31). Each is
   * drawn as a labelled `markLine` at its `category` on the x-axis.
   */
  commitMarkers?: CommitMarker[]
}

/** Total data points across every series — what `data-point-count` reports. */
export function totalPoints(series: ChartSeries[]): number {
  return series.reduce((sum, s) => sum + s.data.length, 0)
}
