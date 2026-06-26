/**
 * ECharts core registration (Task 20) — tree-shaken Apache ECharts (Apache-2.0,
 * free for commercial use; Highcharts is deliberately NOT used as it is not free
 * for this use). We import from `echarts/core` and register ONLY the chart types,
 * components, and the SVG renderer the dashboard actually uses, so the bundle stays
 * small. The SVG renderer (not canvas) is mandatory: it renders crisp, themeable,
 * DOM-inspectable vectors — which also lets the render tests assert real data marks
 * in the produced `<svg>`.
 */
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
} from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
// ECharts 6 moved `grid.containLabel` behind an opt-in legacy feature; register it
// so labels stay inside the box (the themed grid uses containLabel) without the
// deprecation warning.
import { LegacyGridContainLabel } from 'echarts/features'

echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  MarkLineComponent,
  LegacyGridContainLabel,
  SVGRenderer,
])

export { echarts }
export type EChartsOption = echarts.EChartsCoreOption
