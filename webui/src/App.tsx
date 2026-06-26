import { BrowserRouter } from 'react-router-dom'

import { PanelRoutes } from '@/panels/routes'
import { AppQueryProvider } from '@/services/query'
import { ThemeProvider } from '@/theme/ThemeProvider'

/**
 * App root (Task 15 shell → Task 18 registry-driven routing).
 *
 * Wraps the dark-by-default `ThemeProvider` and the TanStack Query provider (one
 * shared cache; the live loop poll survives route changes) around a `BrowserRouter`
 * whose entire route tree + sidebar nav are GENERATED from the panel registry
 * (`PanelRoutes` → `PANELS`). Adding a panel is one registry entry — no edits here.
 * The bespoke panels + IA sections + Overview landing arrive in Tasks 19–33.
 */
export function App() {
  return (
    <ThemeProvider>
      <AppQueryProvider>
        <BrowserRouter
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <PanelRoutes />
        </BrowserRouter>
      </AppQueryProvider>
    </ThemeProvider>
  )
}

export default App
