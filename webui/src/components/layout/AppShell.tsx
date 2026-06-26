import { useState, type ReactNode } from 'react'
import { Menu } from 'lucide-react'
import { Sidebar } from '@/components/layout/Sidebar'
import { KpiStrip, type Kpi } from '@/components/layout/KpiStrip'
import { ContentGrid } from '@/components/layout/ContentGrid'
import type { NavSection } from '@/components/layout/nav'

/**
 * The canonical app shell (Task 15): a 256px sectioned sidebar + a top strip of
 * 4–6 KPI cards + a 12-column content grid below — the layout system Stripe /
 * Linear / Vercel use, on a neutral canvas with the subtle Vercel-grid texture.
 */
export function AppShell({
  children,
  title = 'Overview',
  subtitle,
  sections,
  activeRoute,
  kpis,
}: {
  children?: ReactNode
  title?: string
  subtitle?: string
  sections?: NavSection[]
  activeRoute?: string
  kpis?: Kpi[]
}) {
  const [navOpen, setNavOpen] = useState(false)

  return (
    <div className="flex h-screen overflow-hidden bg-background text-foreground">
      <Sidebar
        sections={sections}
        activeRoute={activeRoute}
        open={navOpen}
        onClose={() => setNavOpen(false)}
      />

      {/* Mobile-only backdrop behind the off-canvas drawer. */}
      {navOpen ? (
        <div
          aria-hidden="true"
          onClick={() => setNavOpen(false)}
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
        />
      ) : null}

      <div className="bg-grid flex flex-1 flex-col overflow-y-auto">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4 md:px-6">
          <button
            type="button"
            aria-label="Open navigation"
            aria-expanded={navOpen}
            onClick={() => setNavOpen(true)}
            className="rounded-md p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
          >
            <Menu aria-hidden="true" className="size-5" />
          </button>
          <div>
            <h1 className="text-base font-semibold tracking-tight">{title}</h1>
            {subtitle ? (
              <p className="text-2xs text-muted-foreground">{subtitle}</p>
            ) : null}
          </div>
        </header>

        <main className="flex flex-1 flex-col gap-6 p-6">
          <KpiStrip kpis={kpis} />
          <ContentGrid>{children}</ContentGrid>
        </main>
      </div>
    </div>
  )
}
