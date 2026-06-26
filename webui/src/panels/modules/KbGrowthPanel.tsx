import { ChevronRight } from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'

import { BarSeries } from '@/components/charts'
import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import type { KbEntry, KbResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import {
  KB_LEARNINGS_PATH,
  KB_PROJECTS_PATH,
  kbLearningDetailPath,
} from '@/panels/drilldown/paths'
import type { PanelProps } from '@/panels/registry'

/**
 * KB growth panel (Task 21) — the MEMORY pillar's headline. Entry / category /
 * tag counts, the per-category distribution, and a browsable table of the
 * knowledge entries.
 *
 * Task 22 makes this the ENTRY POINT of the aggregate → list → detail drill-down:
 * the "Browse learnings / projects" links open the list views, and clicking an
 * entry row opens that learning's detail (`/memory/kb/learnings/<id>`).
 */
const columns: DataTableColumn<KbEntry>[] = [
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
    key: 'tags',
    header: 'Tags',
    cell: (e) => (
      <span className="text-2xs text-muted-foreground">
        {(e.tags ?? []).join(', ') || '—'}
      </span>
    ),
  },
]

export function KbGrowthPanel({ panel, query }: PanelProps<KbResponse>) {
  const navigate = useNavigate()
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Knowledge-base size, categories & entries"
      query={query}
      span={12}
      isEmpty={(d) => d.entry_count === 0}
    >
      {(data) => {
        const categoryNames = Object.keys(data.categories)
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-3 gap-3">
              <StatTile
                label="Entries"
                value={data.entry_count}
                status="neutral"
                hint="indexed knowledge"
              />
              <StatTile
                label="Categories"
                value={data.category_count ?? categoryNames.length}
                status="neutral"
                hint="taxonomy buckets"
              />
              <StatTile
                label="Tags"
                value={data.tag_count ?? 0}
                status="neutral"
                hint="distinct tags"
              />
            </div>
            <nav
              aria-label="Knowledge-base drill-downs"
              className="flex flex-wrap gap-2"
            >
              <Link
                to={KB_LEARNINGS_PATH}
                className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground outline-none transition-colors duration-75 hover:border-foreground/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              >
                Browse learnings
                <ChevronRight aria-hidden="true" className="size-3" />
              </Link>
              <Link
                to={KB_PROJECTS_PATH}
                className="inline-flex items-center gap-1 rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground outline-none transition-colors duration-75 hover:border-foreground/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
              >
                Browse projects
                <ChevronRight aria-hidden="true" className="size-3" />
              </Link>
            </nav>
            {categoryNames.length > 0 ? (
              <BarSeries
                categories={categoryNames}
                series={[
                  {
                    name: 'Entries',
                    data: categoryNames.map((c) => data.categories[c] ?? 0),
                  },
                ]}
                ariaLabel="Entries by category"
                height={180}
              />
            ) : null}
            <DataTable
              columns={columns}
              rows={data.entries}
              rowKey={(e, i) => e.id ?? e.path ?? String(i)}
              onRowClick={(e) => {
                if (e.id) navigate(kbLearningDetailPath(e.id))
              }}
              caption="Knowledge-base entries"
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
