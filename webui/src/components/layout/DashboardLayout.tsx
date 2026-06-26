/**
 * Dashboard layout route (Task 18).
 *
 * Renders the canonical app shell once and nests the active panel via `<Outlet>`,
 * so the sidebar + KPI strip persist across route changes (and the live-loop poll
 * keeps running). The sidebar sections and the header title are DERIVED from the
 * panel registry — nothing here is hand-maintained per panel.
 */
import { Outlet, useLocation } from 'react-router-dom'

import { AppShell } from '@/components/layout/AppShell'
import { buildNavSections } from '@/components/layout/nav'
import { PANELS, findPanelByRoute, type PanelDefinition } from '@/panels/registry'

export function DashboardLayout({
  panels = PANELS,
}: {
  panels?: PanelDefinition[]
}) {
  const location = useLocation()
  const active = findPanelByRoute(panels, location.pathname)
  return (
    <AppShell
      title={active?.title ?? 'Dashboard'}
      subtitle="agentware — both a memory system and a looping agent"
      sections={buildNavSections(panels)}
      activeRoute={location.pathname}
    >
      <Outlet />
    </AppShell>
  )
}
