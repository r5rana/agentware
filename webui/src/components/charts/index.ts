/**
 * Barrel for the chart components (Task 20). Panels import from `@/components/charts`
 * so the themed ECharts family + the bespoke visx viz are one consistent surface.
 *
 * - ECharts (Apache-2.0): LineTrend, AreaTrend, BarSeries, Sparkline — themed via
 *   the token-bound EChart wrapper (SVG renderer, re-themes on light/dark toggle).
 * - visx (MIT): Burndown, TraceWaterfall — bespoke hand-drawn SVG where a generic
 *   chart is awkward.
 */
export { EChart, type EChartProps } from './EChart'
export { LineTrend } from './LineTrend'
export { AreaTrend } from './AreaTrend'
export { BarSeries } from './BarSeries'
export { Sparkline, type SparklineProps } from './Sparkline'
export { Burndown, type BurndownProps, type BurndownPoint } from './Burndown'
export {
  TraceWaterfall,
  type TraceWaterfallProps,
  type TraceStep,
} from './TraceWaterfall'
export {
  type ChartSeries,
  type CategoryChartProps,
  totalPoints,
} from './types'
export {
  buildEchartsTheme,
  chartPalette,
  statusColor,
  token,
} from './echarts-theme'
