/**
 * Registry-generated routing (Task 18).
 *
 * `PanelRoutes` builds the entire React Router tree FROM the `PANELS` registry: a
 * layout route renders the app shell (sidebar + KPI strip + grid) and each panel
 * entry becomes a child route. Adding a panel to the registry therefore adds its
 * route here with ZERO edits to this file. `PanelHost` calls a panel's `useData`
 * hook and hands the typed, schema-validated query to its component.
 *
 * Alongside the registry-generated panel routes, the Task-22 DRILL-DOWN routes
 * (`DRILLDOWN_ROUTES`: aggregate → list → detail for KB learnings/projects/tags
 * and the per-feature task timeline) are mounted under the SAME layout, so the
 * shell + nav persist as the user drills in. Both tables are data-driven, so a
 * new panel or a new drill-down is one entry — never an edit to this file.
 */
import { Navigate, Route, Routes } from 'react-router-dom'

import { DashboardLayout } from '@/components/layout/DashboardLayout'
import { DRILLDOWN_ROUTES } from '@/panels/drilldown'
import { PANELS, type PanelDefinition } from '@/panels/registry'

/** Calls a panel's bound hook and renders its component with the typed query. */
export function PanelHost({ panel }: { panel: PanelDefinition }) {
  const query = panel.useData()
  const Component = panel.component
  return <Component panel={panel} query={query} />
}

/** The full router, generated from the panel registry. */
export function PanelRoutes({
  panels = PANELS,
}: {
  panels?: PanelDefinition[]
}) {
  return (
    <Routes>
      <Route element={<DashboardLayout panels={panels} />}>
        {panels.map((panel) => (
          <Route
            key={panel.id}
            path={panel.route}
            element={<PanelHost panel={panel} />}
          />
        ))}
        {/* Task-22 drill-down routes (aggregate → list → detail). */}
        {DRILLDOWN_ROUTES.map((r) => (
          <Route key={r.path} path={r.path} element={r.element} />
        ))}
        {/* Unknown routes fall back to the Overview landing. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
