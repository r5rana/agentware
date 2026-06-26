import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { render, screen, fireEvent, within } from '@testing-library/react'
import axe from 'axe-core'
import { App } from '@/App'
import {
  STATUS_TOKEN,
  SEMANTIC_COLOR_VARS,
  readCssVar,
} from '@/lib/design-tokens'

// Task 15 — design-system + app-shell verification. Mounts the real shell
// (sectioned sidebar + KPI strip + 12-col grid) under the ThemeProvider and
// asserts nav + theme toggle render, the tokens expose semantic colors, and axe
// reports 0 CRITICAL violations.

describe('design system app shell', () => {
  beforeEach(() => {
    document.documentElement.className = ''
    window.localStorage.clear()
    // The Task-18 registry mounts real panels whose hooks fetch on render; keep
    // the queries pending so the shell renders deterministically with no network.
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    )
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the sectioned sidebar with the three agent pillars', () => {
    render(<App />)
    const sidebar = screen.getByRole('complementary', { name: /primary/i })
    expect(sidebar).toBeInTheDocument()
    // The IA leads with the three pillars: PLAN_AW, WORK_AW, LOOP.
    expect(within(sidebar).getByText('PLAN_AW · Planner')).toBeInTheDocument()
    expect(within(sidebar).getByText('WORK_AW · Worker')).toBeInTheDocument()
    expect(within(sidebar).getByText('LOOP · Runs')).toBeInTheDocument()
    // A nav item from a pillar is reachable.
    expect(
      within(sidebar).getByRole('link', { name: 'Loop analytics' }),
    ).toBeInTheDocument()
  })

  it('renders the KPI metric strip and the 12-column content grid', () => {
    render(<App />)
    expect(
      screen.getByRole('region', { name: /key metrics/i }),
    ).toBeInTheDocument()
    const grid = screen.getByTestId('content-grid')
    expect(grid).toBeInTheDocument()
    expect(grid.className).toContain('grid-cols-12')
  })

  it('renders a theme toggle that flips the dark class on <html>', () => {
    render(<App />)
    // Dark-mode-by-default: the provider applies `.dark` on mount.
    expect(document.documentElement).toHaveClass('dark')
    const toggle = screen.getByTestId('theme-toggle')
    expect(toggle).toBeInTheDocument()
    fireEvent.click(toggle)
    expect(document.documentElement).not.toHaveClass('dark')
    fireEvent.click(toggle)
    expect(document.documentElement).toHaveClass('dark')
  })

  it('exposes semantic status colors as design tokens (meaning, not decoration)', () => {
    // The typed token registry maps every health state to a single hue.
    for (const status of ['success', 'warning', 'danger'] as const) {
      expect(STATUS_TOKEN[status].bg).toBe(`bg-${status}`)
      expect(STATUS_TOKEN[status].cssVar).toContain(status)
    }
    expect(SEMANTIC_COLOR_VARS).toEqual(['--success', '--warning', '--danger'])

    // readCssVar resolves a CSS custom property off an element.
    const el = document.createElement('div')
    el.style.setProperty('--success', '#22c55e')
    document.body.appendChild(el)
    expect(readCssVar('--success', el)).toBe('#22c55e')
    el.remove()
  })

  it('has no critical accessibility violations (axe-core)', async () => {
    const { container } = render(<App />)
    const results = await axe.run(container, {
      // jsdom has no layout engine, so color-contrast cannot be evaluated here
      // (covered by the Playwright pass in Task 25); assert the structural rules.
      rules: { 'color-contrast': { enabled: false } },
    })
    const critical = results.violations.filter((v) => v.impact === 'critical')
    expect(critical).toEqual([])
  })
})
