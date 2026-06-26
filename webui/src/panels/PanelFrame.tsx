import { AlertTriangle, Unplug } from 'lucide-react'
import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

import { GridCell } from '@/components/layout/ContentGrid'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  EmptyState,
  SkeletonText,
} from '@/components/ui'
import { PanelActions } from '@/panels/PanelActions'

/**
 * Shared panel scaffold (Task 21) — the one surface every registry panel renders
 * through. It centralises the four designed states (loading / error / empty /
 * ready) so each bespoke panel only declares HOW to read its data, never the
 * boilerplate around it:
 *
 *  - **loading** → a `SkeletonText` placeholder (designed, not a spinner).
 *  - **error**   → a designed `EmptyState` (the query rejected at the boundary).
 *  - **not-available** → the read-only backend reports the knowledge dir is
 *    unconfigured (`available:false`); shown as a friendly, non-alarming empty.
 *  - **empty**   → data parsed fine but the panel has nothing to show (idle).
 *  - **ready**   → `children(data)` renders the real panel body.
 *
 * Every state ALWAYS emits a `panel-status-<id>` element so the registry test
 * (which keeps queries pending) and the panel tests can assert the wiring.
 * `PanelFrame` takes `id`/`title` (not the whole `PanelDefinition`) so the same
 * frame backs both registry panels AND the Task-22 drill-down views.
 */
export type PanelStatus = 'loading' | 'error' | 'unavailable' | 'empty' | 'ready'

export interface PanelFrameProps<T> {
  /** Stable id — used for the `panel-status-<id>` test handle. */
  id: string
  /** Panel header title. */
  title: string
  /** Optional sub-header description. */
  description?: string
  /** The bound query (only the state fields are read). */
  query: Pick<UseQueryResult<T>, 'data' | 'isLoading' | 'isError'>
  /** Grid span (12-col substrate). Defaults to a half-width 6. */
  span?: 3 | 4 | 6 | 8 | 12
  /** Optional header-right slot (live badge, back-link, etc.). */
  headerRight?: ReactNode
  /** Return true when `data` is structurally present but logically empty. */
  isEmpty?: (data: T) => boolean
  emptyTitle?: string
  emptyDescription?: string
  /**
   * Per-panel CSV/JSON export + deep-link toolbar (Task 27). Rendered in the
   * header once data is ready, so every panel inherits export + deep-links with
   * zero extra code. Set `exportable={false}` to opt a panel out.
   */
  exportable?: boolean
  /** Export filename base (no extension). Defaults to the panel `id`. */
  exportName?: string
  /** Override the exported CSV rows (else the payload is flattened generically). */
  csvRows?: (data: T) => Record<string, unknown>[]
  /** In-panel filter state encoded into the deep-link as query params. */
  shareParams?: Record<string, string | number | boolean | undefined | null>
  /** Render the body from validated, non-empty data. */
  children: (data: T) => ReactNode
}

/** Detect the read-only backend's "knowledge dir unconfigured" envelope. */
function isUnavailable(data: unknown): boolean {
  return (
    typeof data === 'object' &&
    data !== null &&
    'available' in data &&
    (data as { available?: unknown }).available === false
  )
}

export function PanelFrame<T>({
  id,
  title,
  description,
  query,
  span = 6,
  headerRight,
  isEmpty,
  emptyTitle,
  emptyDescription,
  exportable = true,
  exportName,
  csvRows,
  shareParams,
  children,
}: PanelFrameProps<T>) {
  const { data, isLoading, isError } = query

  const status: PanelStatus = isLoading
    ? 'loading'
    : isError
      ? 'error'
      : data == null
        ? 'empty'
        : isUnavailable(data)
          ? 'unavailable'
          : isEmpty?.(data)
            ? 'empty'
            : 'ready'

  return (
    <GridCell span={span}>
      <Card
        data-testid={`panel-${id}`}
        aria-label={title}
        className="flex h-full flex-col"
      >
        <CardHeader className="flex-row items-start justify-between gap-2">
          <div className="flex flex-col gap-1">
            <CardTitle>{title}</CardTitle>
            {description ? (
              <CardDescription>{description}</CardDescription>
            ) : null}
          </div>
          {headerRight || (exportable && status === 'ready') ? (
            <div className="flex shrink-0 items-center gap-2">
              {headerRight}
              {exportable && status === 'ready' ? (
                <PanelActions
                  filenameBase={exportName ?? id}
                  data={data}
                  csvRows={csvRows ? () => csvRows(data as T) : undefined}
                  shareParams={shareParams}
                />
              ) : null}
            </div>
          ) : null}
        </CardHeader>
        {/* Always-present status marker (sr-only) so the wiring is observable. */}
        <span
          data-testid={`panel-status-${id}`}
          className="sr-only"
          data-status={status}
        >
          {status}
        </span>
        <CardContent className="flex-1">
          {status === 'loading' ? (
            <SkeletonText lines={4} />
          ) : status === 'error' ? (
            <EmptyState
              icon={AlertTriangle}
              title="Couldn't load this panel"
              description="The dashboard API returned an error or a malformed payload."
            />
          ) : status === 'unavailable' ? (
            <EmptyState
              icon={Unplug}
              title="Knowledge base not configured"
              description="Run agentware onboarding to populate this dashboard."
            />
          ) : status === 'empty' ? (
            <EmptyState
              title={emptyTitle ?? 'Nothing to show yet'}
              description={
                emptyDescription ??
                'This panel will populate once agentware records data.'
              }
            />
          ) : (
            children(data as T)
          )}
        </CardContent>
      </Card>
    </GridCell>
  )
}
