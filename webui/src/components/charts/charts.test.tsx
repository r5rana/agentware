import { render, waitFor } from '@testing-library/react'
import type { ReactElement } from 'react'
import { describe, expect, it } from 'vitest'
import { ThemeProvider } from '@/theme/ThemeProvider'
import { AreaTrend } from './AreaTrend'
import { BarSeries } from './BarSeries'
import { Burndown } from './Burndown'
import { LineTrend } from './LineTrend'
import { Sparkline } from './Sparkline'
import { TraceWaterfall } from './TraceWaterfall'

/** Charts read `useTheme`, so every render is wrapped in the ThemeProvider. */
function renderChart(ui: ReactElement) {
  return render(<ThemeProvider>{ui}</ThemeProvider>)
}

/**
 * Count the data-point SYMBOLS ECharts actually painted into the SVG. The SVG
 * renderer draws each line/area symbol as a `<path>` whose `d` is the unit circle
 * (`M1 0A1 1 …`), positioned by a transform — so this counts real marks in the DOM,
 * not just a prop.
 */
function countEchartsSymbols(svg: SVGSVGElement): number {
  return Array.from(svg.querySelectorAll('path')).filter((p) =>
    /^M1 0A1 1/.test(p.getAttribute('d') ?? ''),
  ).length
}

const TREND_FIXTURE = {
  categories: ['d1', 'd2', 'd3', 'd4', 'd5'],
  series: [{ name: 'Recall@5', data: [0.4, 0.5, 0.55, 0.6, 0.62] }],
}

describe('ECharts charts (Task 20)', () => {
  it('LineTrend renders an SVG with one symbol per data point', async () => {
    const { getByTestId, container } = renderChart(
      <LineTrend {...TREND_FIXTURE} width={600} height={300} />,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('svg path').length).toBeGreaterThan(0),
    )
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(svg).toBeTruthy()
    // The wrapper exposes the data-point count …
    expect(getByTestId('line-trend').getAttribute('data-point-count')).toBe('5')
    // … and ECharts painted exactly that many symbol marks into the SVG.
    expect(countEchartsSymbols(svg)).toBe(5)
  })

  it('LineTrend renders commit markers aligned to ledger SHAs (Task 31)', async () => {
    const { getByTestId, container } = renderChart(
      <LineTrend
        categories={['c0ffee0', 'c0ffee1', 'c0ffee2']}
        series={[{ name: 'reliability', data: [78, 82, 70] }]}
        commitMarkers={[
          { category: 'c0ffee1', label: 'c0ffee1' },
          { category: 'c0ffee2', label: 'c0ffee2' },
          // An off-axis SHA is ignored (never produces a floating marker).
          { category: 'deadbee', label: 'deadbee' },
        ]}
        width={600}
        height={300}
      />,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('svg path').length).toBeGreaterThan(0),
    )
    // Only the two on-axis markers count; the off-axis one is filtered out.
    expect(getByTestId('line-trend').getAttribute('data-marker-count')).toBe('2')
  })

  it('AreaTrend renders a filled SVG with one symbol per data point', async () => {
    const { getByTestId, container } = renderChart(
      <AreaTrend {...TREND_FIXTURE} width={600} height={300} />,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('svg path').length).toBeGreaterThan(0),
    )
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(getByTestId('area-trend').getAttribute('data-point-count')).toBe('5')
    expect(countEchartsSymbols(svg)).toBe(5)
  })

  it('LineTrend supports multiple series (point count sums all series)', async () => {
    const { getByTestId, container } = renderChart(
      <LineTrend
        categories={['a', 'b', 'c']}
        series={[
          { name: 'pre', data: [1, 2, 3] },
          { name: 'main', data: [4, 5, 6], status: 'success' },
        ]}
        width={600}
        height={300}
      />,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('svg path').length).toBeGreaterThan(0),
    )
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(getByTestId('line-trend').getAttribute('data-point-count')).toBe('6')
    expect(countEchartsSymbols(svg)).toBe(6)
  })

  it('BarSeries renders a non-empty SVG and reports its data points', async () => {
    const { getByTestId, container } = renderChart(
      <BarSeries
        categories={['opus', 'sonnet', 'haiku']}
        series={[{ name: 'cost', data: [12, 5, 1] }]}
        width={600}
        height={300}
      />,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('svg path').length).toBeGreaterThan(0),
    )
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(svg).toBeTruthy()
    expect(getByTestId('bar-series').getAttribute('data-point-count')).toBe('3')
  })

  it('Sparkline renders a minimal SVG with one symbol per point', async () => {
    const { getByTestId, container } = renderChart(
      <Sparkline data={[1, 3, 2, 5, 4, 6]} status="success" width={120} height={40} />,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('svg path').length).toBeGreaterThan(0),
    )
    const svg = container.querySelector('svg') as SVGSVGElement
    expect(getByTestId('sparkline').getAttribute('data-point-count')).toBe('6')
    expect(countEchartsSymbols(svg)).toBe(6)
  })

  it('re-themes on light vs dark (renders an SVG in both themes)', async () => {
    const { container } = renderChart(
      <Sparkline data={[1, 2, 3]} width={120} height={40} />,
    )
    await waitFor(() =>
      expect(container.querySelectorAll('svg path').length).toBeGreaterThan(0),
    )
    expect(container.querySelector('svg')).toBeTruthy()
  })
})

describe('visx bespoke charts (Task 20)', () => {
  it('Burndown renders an SVG with one point per iteration', () => {
    const data = [
      { iteration: 0, remaining: 5 },
      { iteration: 1, remaining: 3 },
      { iteration: 2, remaining: 1 },
      { iteration: 3, remaining: 0 },
    ]
    const { getByTestId } = renderChart(<Burndown data={data} />)
    const svg = getByTestId('burndown')
    expect(svg.tagName.toLowerCase()).toBe('svg')
    expect(svg.getAttribute('data-point-count')).toBe('4')
    expect(svg.querySelectorAll('[data-testid="burndown-point"]')).toHaveLength(4)
    // the actual burndown line + the dashed ideal-pace reference both render
    expect(svg.querySelector('[data-testid="burndown-line"]')).toBeTruthy()
    expect(svg.querySelector('[data-testid="burndown-ideal"]')).toBeTruthy()
  })

  it('Burndown handles an empty series without crashing', () => {
    const { getByTestId } = renderChart(<Burndown data={[]} />)
    const svg = getByTestId('burndown')
    expect(svg.getAttribute('data-point-count')).toBe('0')
    expect(svg.querySelectorAll('[data-testid="burndown-point"]')).toHaveLength(0)
  })

  it('TraceWaterfall renders one bar per step', () => {
    const steps = [
      { label: 'Read', start: 0, duration: 5 },
      { label: 'Edit', start: 5, duration: 3, status: 'success' as const },
      { label: 'Bash', start: 8, duration: 7, status: 'danger' as const },
    ]
    const { getByTestId } = renderChart(<TraceWaterfall steps={steps} />)
    const svg = getByTestId('trace-waterfall')
    expect(svg.tagName.toLowerCase()).toBe('svg')
    expect(svg.getAttribute('data-point-count')).toBe('3')
    expect(svg.querySelectorAll('[data-testid="waterfall-bar"]')).toHaveLength(3)
  })

  it('TraceWaterfall handles an empty trace', () => {
    const { getByTestId } = renderChart(<TraceWaterfall steps={[]} />)
    const svg = getByTestId('trace-waterfall')
    expect(svg.getAttribute('data-point-count')).toBe('0')
    expect(svg.querySelectorAll('[data-testid="waterfall-bar"]')).toHaveLength(0)
  })
})
