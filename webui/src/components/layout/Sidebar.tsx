import { Link, useLocation } from 'react-router-dom'
import { X } from 'lucide-react'

import { buildNavSections, type NavSection } from '@/components/layout/nav'
import { PANELS } from '@/panels/registry'
import { ThemeToggle } from '@/theme/ThemeToggle'
import { cn } from '@/lib/utils'

/**
 * The canonical left sidebar (Task 15 → registry-driven in Task 18): a fixed
 * 256px (within the 240–280px design band) sectioned nav over the neutral
 * `--sidebar` canvas with a crisp 1px right border. The sections + items are
 * GENERATED from the panel registry (`buildNavSections(PANELS)`), so adding a
 * panel adds its nav entry with no edits here. Sections still headline the two
 * pillars (Loops, Knowledge Base / Memory). Active item is derived from the
 * router location unless an explicit `activeRoute` override is passed.
 *
 * Responsive (Task 25 pristine pass): static inline at `md+`, but below `md`
 * (mobile) it becomes an off-canvas drawer that slides in over a backdrop — so
 * the 256px nav never steals width from a 390px viewport and clips content.
 * `open`/`onClose` drive the mobile drawer; on desktop they are inert.
 */
export function Sidebar({
  sections = buildNavSections(PANELS),
  activeRoute,
  open = false,
  onClose,
}: {
  sections?: NavSection[]
  activeRoute?: string
  open?: boolean
  onClose?: () => void
}) {
  const location = useLocation()
  const currentRoute = activeRoute ?? location.pathname

  return (
    <aside
      aria-label="Primary"
      className={cn(
        'fixed inset-y-0 left-0 z-50 flex w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground transition-transform duration-200 md:static md:z-auto md:translate-x-0',
        open ? 'translate-x-0' : '-translate-x-full',
      )}
    >
      <div className="flex h-14 items-center gap-2 border-b border-sidebar-border px-5">
        <span
          aria-hidden="true"
          className="inline-block h-2.5 w-2.5 rounded-full bg-success"
        />
        <span className="text-sm font-semibold tracking-tight">agentware</span>
        <span className="text-2xs text-muted-foreground">observability</span>
        <button
          type="button"
          aria-label="Close navigation"
          onClick={onClose}
          className="ml-auto rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
        >
          <X aria-hidden="true" className="size-4" />
        </button>
      </div>

      <nav aria-label="Sections" className="flex-1 overflow-y-auto px-3 py-4">
        {sections.map((section) => {
          const Icon = section.icon
          return (
            <div key={section.id} className="mb-5">
              <div className="mb-1 flex items-center gap-2 px-2 text-2xs font-medium uppercase tracking-wider text-muted-foreground">
                <Icon aria-hidden="true" className="size-3.5" />
                <span>{section.label}</span>
              </div>
              <ul>
                {section.items.map((item) => {
                  const active = item.route === currentRoute
                  return (
                    <li key={item.id}>
                      <Link
                        to={item.route}
                        onClick={onClose}
                        aria-current={active ? 'page' : undefined}
                        className={cn(
                          'block rounded-md px-3 py-1.5 text-sm transition-colors duration-75',
                          active
                            ? 'bg-muted font-medium text-foreground'
                            : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                        )}
                      >
                        {item.label}
                      </Link>
                    </li>
                  )
                })}
              </ul>
            </div>
          )
        })}
      </nav>

      <div className="flex items-center justify-between border-t border-sidebar-border px-4 py-3">
        <span className="text-2xs text-muted-foreground">v0.1.0</span>
        <ThemeToggle />
      </div>
    </aside>
  )
}
