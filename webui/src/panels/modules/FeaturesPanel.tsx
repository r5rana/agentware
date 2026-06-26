import { BarSeries } from '@/components/charts'
import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import type { FeaturesResponse, KbEntry } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'

/**
 * Features panel (Task 21) — the FEATURES.md surface: knowledge entries grouped
 * by category. Counts + a per-category bar + a flattened, browsable table.
 */
type FeatureRow = KbEntry & { _category: string }

const columns: DataTableColumn<FeatureRow>[] = [
  {
    key: 'title',
    header: 'Entry',
    cell: (e) => <span className="font-medium">{e.title ?? e.id ?? '—'}</span>,
    sortValue: (e) => e.title ?? e.id ?? '',
  },
  {
    key: 'category',
    header: 'Category',
    cell: (e) => <span className="text-muted-foreground">{e._category}</span>,
    sortValue: (e) => e._category,
  },
  {
    key: 'summary',
    header: 'Summary',
    cell: (e) => (
      <span className="text-2xs text-muted-foreground">{e.summary ?? '—'}</span>
    ),
  },
]

export function FeaturesPanel({ panel, query }: PanelProps<FeaturesResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Knowledge entries grouped by category"
      query={query}
      span={12}
      isEmpty={(d) => d.entry_count === 0}
    >
      {(data) => {
        const categoryNames = Object.keys(data.categories)
        const rows: FeatureRow[] = categoryNames.flatMap((c) =>
          data.categories[c].map((e) => ({ ...e, _category: c })),
        )
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3">
              <StatTile
                label="Entries"
                value={data.entry_count}
                status="neutral"
                hint="catalogued features"
              />
              <StatTile
                label="Categories"
                value={data.category_count ?? categoryNames.length}
                status="neutral"
                hint="taxonomy buckets"
              />
            </div>
            {categoryNames.length > 0 ? (
              <BarSeries
                categories={categoryNames}
                series={[
                  {
                    name: 'Entries',
                    data: categoryNames.map(
                      (c) => data.categories[c]?.length ?? 0,
                    ),
                  },
                ]}
                ariaLabel="Entries by category"
                height={180}
              />
            ) : null}
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(e, i) => e.id ?? e.path ?? String(i)}
              caption="Feature catalogue"
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
