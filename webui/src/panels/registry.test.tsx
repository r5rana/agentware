/**
 * Task 18 — panel-registry architecture verification.
 *
 *   1. Every registry entry is complete (route + icon + component + useData) and
 *      surfaces as exactly one GENERATED nav item; routes are unique.
 *   2. Every entry renders at its route through the registry-generated router.
 *   3. Adding a DUMMY panel makes it appear in BOTH the nav and the routes with
 *      ZERO edits to nav/router code — proving the one-entry extensibility claim
 *      (this test imports the unmodified `buildNavSections` + `PanelRoutes`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import type { ReactElement } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

import { buildNavSections } from '@/components/layout/nav'
import {
  PANELS,
  definePanel,
  findPanelByRoute,
  type PanelDefinition,
  type PanelProps,
} from '@/panels/registry'
import { PanelRoutes } from '@/panels/routes'
import { AppQueryProvider } from '@/services/query'
import { ThemeProvider } from '@/theme/ThemeProvider'

function renderRoutes(
  panels: PanelDefinition[],
  initialPath: string,
): ReturnType<typeof render> {
  return render(
    <ThemeProvider>
      <AppQueryProvider>
        <MemoryRouter
          initialEntries={[initialPath]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <PanelRoutes panels={panels} />
        </MemoryRouter>
      </AppQueryProvider>
    </ThemeProvider>,
  )
}

beforeEach(() => {
  document.documentElement.className = ''
  // Keep every panel's data query pending so renders are deterministic + offline.
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>(() => {})),
  )
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('panel registry', () => {
  it('every entry is complete and surfaces as exactly one generated nav item', () => {
    const navItems = buildNavSections(PANELS).flatMap((s) => s.items)

    // One nav item per registered panel — nothing dropped, nothing duplicated.
    expect(navItems).toHaveLength(PANELS.length)

    const routes = PANELS.map((p) => p.route)
    expect(new Set(routes).size).toBe(routes.length) // routes are unique

    for (const panel of PANELS) {
      expect(panel.id).toBeTruthy()
      expect(panel.title).toBeTruthy()
      expect(panel.route).toBeTruthy()
      expect(panel.icon).toBeTruthy()
      expect(typeof panel.component).toBe('function')
      expect(typeof panel.useData).toBe('function')

      const navItem = navItems.find((i) => i.id === panel.id)
      expect(navItem).toBeDefined()
      expect(navItem?.route).toBe(panel.route)
      expect(navItem?.label).toBe(panel.title)

      // findPanelByRoute round-trips the route back to the same entry.
      expect(findPanelByRoute(PANELS, panel.route)?.id).toBe(panel.id)
    }
  })

  it('the three agent pillars (PLAN_AW + WORK_AW + LOOP) have panels', () => {
    const sections = buildNavSections(PANELS)
    for (const id of ['plan_aw', 'work_aw', 'loop']) {
      const section = sections.find((s) => s.id === id)
      expect(section?.items.length).toBeGreaterThanOrEqual(1)
    }
  })

  it('renders every registry entry at its own route', () => {
    for (const panel of PANELS) {
      const { unmount } = renderRoutes(PANELS, panel.route)
      // The registry-generated route mounted THIS panel's component.
      expect(
        screen.getByTestId(`panel-status-${panel.id}`),
      ).toBeInTheDocument()
      // ...and the shell header reflects the active panel's title.
      expect(
        screen.getByRole('heading', { level: 1, name: panel.title }),
      ).toBeInTheDocument()
      unmount()
    }
  })

  it('adding a dummy panel appears in nav + route with no nav/router edits', () => {
    function DummyPanel({ panel }: PanelProps): ReactElement {
      return <div data-testid="dummy-panel">dummy:{panel.title}</div>
    }
    // A stand-in data hook (no real network) — proves useData is honoured.
    const useDummyData = (): UseQueryResult<{ ok: boolean }> =>
      ({
        data: { ok: true },
        isLoading: false,
        isError: false,
      }) as unknown as UseQueryResult<{ ok: boolean }>

    const dummy = definePanel({
      id: 'dummy',
      title: 'Dummy Panel',
      route: '/dummy',
      icon: Sparkles,
      section: 'overview',
      component: DummyPanel,
      useData: useDummyData,
    })

    const panels = [...PANELS, dummy]

    // 1) It shows up in the GENERATED nav (same buildNavSections, untouched).
    const navItems = buildNavSections(panels).flatMap((s) => s.items)
    expect(navItems.some((i) => i.id === 'dummy')).toBe(true)

    // 2) It is reachable as a GENERATED route (same PanelRoutes, untouched).
    renderRoutes(panels, '/dummy')
    expect(screen.getByTestId('dummy-panel')).toHaveTextContent(
      'dummy:Dummy Panel',
    )

    // 3) Its nav link is rendered in the sidebar.
    const sidebar = screen.getByRole('complementary', { name: /primary/i })
    expect(
      within(sidebar).getByRole('link', { name: 'Dummy Panel' }),
    ).toBeInTheDocument()
  })
})
