/**
 * Drill-down navigation (Task 22) — aggregate → list → detail.
 *
 * The registry-driven router (`PanelRoutes`) mounts the AGGREGATE panels (e.g.
 * the KB-growth panel at `/memory/kb`, run outcomes at `/loops/outcomes`). This
 * module adds the deeper LIST and DETAIL routes those panels link into, all
 * reusing the shared `DataTable` primitive + the Task-16/17 zod-validated
 * drill-down hooks (`useKbLearnings`, `useKbLearningDetail`, `useKbTag`,
 * `useKbProjects`, `useTasks`). Every view renders through `PanelFrame`, so the
 * loading / error / unavailable / empty / ready states (and the
 * `panel-status-<id>` test marker) come for free.
 *
 * `DRILLDOWN_ROUTES` is a data-driven table the router maps over, so adding a
 * drill-down is one entry — mirroring the panel-registry extensibility model.
 */
import type { ReactElement } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import type { UseQueryResult } from '@tanstack/react-query'

import { DataTable, type DataTableColumn } from '@/components/ui'
import { PanelFrame } from '@/panels/PanelFrame'
import { TaskTimelinePanel } from '@/panels/modules'
import type { KbEntry } from '@/services/api/contract'
import {
  useKbLearningDetail,
  useKbLearnings,
  useKbProjects,
  useKbTag,
  useTasks,
} from '@/services/query'
import {
  KB_LEARNINGS_PATH,
  kbLearningDetailPath,
  kbTagPath,
} from './paths'

export * from './paths'

/* -------------------------------------------------------------------------- */
/* Shared bits                                                                 */
/* -------------------------------------------------------------------------- */

/** A small "← back" link rendered in a drill-down view's header-right slot. */
function BackLink({ to, label }: { to: string; label: string }): ReactElement {
  return (
    <Link
      to={to}
      className="inline-flex items-center gap-1 rounded text-2xs font-medium text-muted-foreground outline-none transition-colors duration-75 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
    >
      <ArrowLeft aria-hidden="true" className="size-3" />
      {label}
    </Link>
  )
}

/** Columns for a list of KB entries (rows drill into a learning's detail). */
const ENTRY_COLUMNS: DataTableColumn<KbEntry>[] = [
  {
    key: 'title',
    header: 'Entry',
    cell: (e) => <span className="font-medium">{e.title ?? e.id ?? '—'}</span>,
    sortValue: (e) => e.title ?? e.id ?? '',
  },
  {
    key: 'category',
    header: 'Category',
    cell: (e) => (
      <span className="text-muted-foreground">{e.category ?? '—'}</span>
    ),
    sortValue: (e) => e.category ?? '',
  },
  {
    key: 'summary',
    header: 'Summary',
    cell: (e) => (
      <span className="text-2xs text-muted-foreground">{e.summary ?? '—'}</span>
    ),
  },
]

/**
 * Generic "list of KB entries" drill-down view. The learnings list, the
 * projects list, and the by-tag list all share the `{ entries: KbEntry[] }`
 * shape, so they reuse this one body; a row click drills into the entry's
 * detail when it carries an id.
 */
function KbEntryListView<T extends { entries: KbEntry[] }>({
  id,
  title,
  description,
  backTo,
  backLabel,
  emptyTitle,
  query,
}: {
  id: string
  title: string
  description: string
  backTo: string
  backLabel: string
  emptyTitle: string
  query: UseQueryResult<T>
}): ReactElement {
  const navigate = useNavigate()
  return (
    <PanelFrame
      id={id}
      title={title}
      description={description}
      span={12}
      query={query}
      headerRight={<BackLink to={backTo} label={backLabel} />}
      isEmpty={(d) => d.entries.length === 0}
      emptyTitle={emptyTitle}
    >
      {(data) => (
        <DataTable
          columns={ENTRY_COLUMNS}
          rows={data.entries}
          rowKey={(e, i) => e.id ?? e.path ?? String(i)}
          onRowClick={(e) => {
            if (e.id) navigate(kbLearningDetailPath(e.id))
          }}
          caption={title}
        />
      )}
    </PanelFrame>
  )
}

/* -------------------------------------------------------------------------- */
/* List views                                                                  */
/* -------------------------------------------------------------------------- */

/** `/memory/kb/learnings` — the learnings LIST (drills to a learning detail). */
export function KbLearningsView(): ReactElement {
  const query = useKbLearnings()
  return (
    <KbEntryListView
      id="kb-learnings"
      title="Learnings"
      description="Browse every learning entry — open one for its full body"
      backTo="/memory/kb"
      backLabel="Back to KB"
      emptyTitle="No learnings yet"
      query={query}
    />
  )
}

/** `/memory/kb/projects` — the projects LIST. */
export function KbProjectsView(): ReactElement {
  const query = useKbProjects()
  return (
    <KbEntryListView
      id="kb-projects"
      title="Projects"
      description="Browse every project entry"
      backTo="/memory/kb"
      backLabel="Back to KB"
      emptyTitle="No projects yet"
      query={query}
    />
  )
}

/** `/memory/kb/tags/:tag` — knowledge entries carrying a tag. */
export function KbTagView(): ReactElement {
  const { tag } = useParams<{ tag: string }>()
  const query = useKbTag(tag)
  return (
    <KbEntryListView
      id={`kb-tag-${tag ?? 'unknown'}`}
      title={`Tag · ${tag ?? ''}`}
      description="Knowledge entries carrying this tag"
      backTo={KB_LEARNINGS_PATH}
      backLabel="Back to learnings"
      emptyTitle="No entries for this tag"
      query={query}
    />
  )
}

/* -------------------------------------------------------------------------- */
/* Detail views                                                                */
/* -------------------------------------------------------------------------- */

/** `/memory/kb/learnings/:id` — a single learning's full record + body. */
export function KbLearningDetailView(): ReactElement {
  const { id } = useParams<{ id: string }>()
  const query = useKbLearningDetail(id)
  return (
    <PanelFrame
      id={`kb-learning-${id ?? 'unknown'}`}
      title="Learning detail"
      description={id}
      span={12}
      query={query}
      headerRight={<BackLink to={KB_LEARNINGS_PATH} label="Back to learnings" />}
      isEmpty={(d) =>
        d.entry == null && (d.body == null || d.body === '')
      }
      emptyTitle="Learning not found"
    >
      {(data) => {
        const entry = data.entry
        return (
          <article className="flex flex-col gap-4">
            <header className="flex flex-col gap-1">
              <h3 className="text-lg font-semibold text-foreground">
                {entry?.title ?? data.id ?? id}
              </h3>
              {entry?.summary ? (
                <p className="text-sm text-muted-foreground">{entry.summary}</p>
              ) : null}
              {entry?.tags?.length ? (
                <div className="flex flex-wrap gap-2 pt-1">
                  {entry.tags.map((tag) => (
                    <Link
                      key={tag}
                      to={kbTagPath(tag)}
                      className="rounded-full border border-border px-2 py-0.5 text-2xs text-muted-foreground outline-none transition-colors duration-75 hover:border-foreground/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      #{tag}
                    </Link>
                  ))}
                </div>
              ) : null}
            </header>
            {data.body ? (
              <pre className="whitespace-pre-wrap rounded-lg border border-border bg-muted/30 p-4 text-sm leading-relaxed text-card-foreground">
                {data.body}
              </pre>
            ) : (
              <p className="text-sm text-muted-foreground">
                No body recorded for this entry.
              </p>
            )}
          </article>
        )
      }}
    </PanelFrame>
  )
}

/* -------------------------------------------------------------------------- */
/* Per-feature task timeline (reuses the Task-21 panel)                         */
/* -------------------------------------------------------------------------- */

/** `/loops/tasks/:feature` — the per-task transition timeline for a feature. */
export function TaskTimelineView(): ReactElement {
  const { feature = '' } = useParams<{ feature: string }>()
  const query = useTasks(feature)
  return (
    <TaskTimelinePanel
      feature={feature}
      query={query}
      headerRight={<BackLink to="/loops/outcomes" label="Back to outcomes" />}
    />
  )
}

/* -------------------------------------------------------------------------- */
/* Route table — mapped by the registry-driven router (Task 18/22)             */
/* -------------------------------------------------------------------------- */

export interface DrilldownRoute {
  /** Absolute path (may carry React Router `:params`). */
  path: string
  /** The view rendered at that path. */
  element: ReactElement
}

/** Every drill-down route. Adding one is a single entry here. */
export const DRILLDOWN_ROUTES: ReadonlyArray<DrilldownRoute> = [
  { path: KB_LEARNINGS_PATH, element: <KbLearningsView /> },
  { path: '/memory/kb/learnings/:id', element: <KbLearningDetailView /> },
  { path: '/memory/kb/projects', element: <KbProjectsView /> },
  { path: '/memory/kb/tags/:tag', element: <KbTagView /> },
  { path: '/loops/tasks/:feature', element: <TaskTimelineView /> },
]
