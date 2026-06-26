import { GridCell } from '@/components/layout/ContentGrid'
import type { PanelProps } from '@/panels/registry'

/**
 * Generic panel scaffold (Task 18).
 *
 * The panel-registry needs a concrete `component` for every entry so the router
 * and nav can be GENERATED before the bespoke panels exist. This placeholder
 * renders a designed card that reflects its bound `useData` query state
 * (loading / error / ready) so the registry wiring is observable end-to-end; the
 * real presentational panels replace it per-entry in Tasks 19–33 by swapping the
 * single `component` field — no router/nav edits required.
 */
export function PlaceholderPanel({ panel, query }: PanelProps) {
  const status = query.isLoading
    ? 'loading'
    : query.isError
      ? 'error'
      : 'ready'
  return (
    <GridCell span={6}>
      <section
        aria-label={panel.title}
        className="flex h-48 flex-col rounded-lg border border-border bg-card p-5"
      >
        <h2 className="text-sm font-medium text-card-foreground">
          {panel.title}
        </h2>
        <p className="mt-1 text-2xs text-muted-foreground">
          Panel scaffold — full UI lands in Tasks 19–33.
        </p>
        <div
          data-testid={`panel-status-${panel.id}`}
          className="flex flex-1 items-center justify-center text-2xs text-muted-foreground"
        >
          data: {status}
        </div>
      </section>
    </GridCell>
  )
}
