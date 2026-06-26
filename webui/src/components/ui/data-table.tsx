import { ChevronDown, ChevronsUpDown, ChevronUp } from 'lucide-react'
import {
  useMemo,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import { cn } from '@/lib/utils'
import { EmptyState } from './empty-state'

/**
 * DataTable primitive (Task 19) — a generic, prop-driven, SORTABLE table with
 * optional DRILL-DOWN rows (the aggregate → list → detail navigation in Task 22).
 * Columns declare an optional `sortValue` to become sortable; clicking the header
 * cycles asc → desc. When `onRowClick` is set, rows become keyboard-accessible
 * buttons (Enter/Space) so a count → its list → an item detail is reachable
 * without a mouse. Empty rows render the designed EmptyState.
 */
export interface DataTableColumn<T> {
  key: string
  header: ReactNode
  cell: (row: T) => ReactNode
  /** Provide to make the column sortable; returns the comparable value. */
  sortValue?: (row: T) => string | number
  align?: 'left' | 'right' | 'center'
  className?: string
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[]
  rows: T[]
  rowKey: (row: T, index: number) => string
  /** When set, each row is a drill-down trigger (button semantics + a11y). */
  onRowClick?: (row: T) => void
  empty?: ReactNode
  caption?: string
  className?: string
}

type SortState = { key: string; dir: 'asc' | 'desc' } | null

const ALIGN: Record<NonNullable<DataTableColumn<unknown>['align']>, string> = {
  left: 'text-left',
  right: 'text-right',
  center: 'text-center',
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  empty,
  caption,
  className,
}: DataTableProps<T>) {
  const [sort, setSort] = useState<SortState>(null)

  const sortedRows = useMemo(() => {
    if (!sort) return rows
    const col = columns.find((c) => c.key === sort.key)
    if (!col?.sortValue) return rows
    const get = col.sortValue
    const factor = sort.dir === 'asc' ? 1 : -1
    return [...rows].sort((a, b) => {
      const av = get(a)
      const bv = get(b)
      if (av < bv) return -1 * factor
      if (av > bv) return 1 * factor
      return 0
    })
  }, [rows, sort, columns])

  function toggleSort(col: DataTableColumn<T>): void {
    if (!col.sortValue) return
    setSort((prev) =>
      prev?.key === col.key
        ? { key: col.key, dir: prev.dir === 'asc' ? 'desc' : 'asc' }
        : { key: col.key, dir: 'asc' },
    )
  }

  if (rows.length === 0) {
    return <>{empty ?? <EmptyState title="No rows to show" />}</>
  }

  return (
    <div className={cn('overflow-x-auto rounded-lg border border-border', className)}>
      <table className="w-full border-collapse text-sm">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-border bg-muted/40">
            {columns.map((col) => {
              const active = sort?.key === col.key
              const sortable = Boolean(col.sortValue)
              const Icon = !active
                ? ChevronsUpDown
                : sort?.dir === 'asc'
                  ? ChevronUp
                  : ChevronDown
              return (
                <th
                  key={col.key}
                  scope="col"
                  aria-sort={
                    active
                      ? sort?.dir === 'asc'
                        ? 'ascending'
                        : 'descending'
                      : sortable
                        ? 'none'
                        : undefined
                  }
                  className={cn(
                    'px-3 py-2 text-2xs font-medium uppercase tracking-wider text-muted-foreground',
                    ALIGN[col.align ?? 'left'],
                  )}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(col)}
                      className={cn(
                        'inline-flex items-center gap-1 rounded outline-none transition-colors duration-75 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring',
                        col.align === 'right' && 'flex-row-reverse',
                      )}
                    >
                      {col.header}
                      <Icon
                        aria-hidden="true"
                        className={cn('size-3', active ? 'opacity-100' : 'opacity-40')}
                      />
                    </button>
                  ) : (
                    col.header
                  )}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {sortedRows.map((row, i) => {
            const interactive = Boolean(onRowClick)
            return (
              <tr
                key={rowKey(row, i)}
                data-testid="data-table-row"
                {...(interactive
                  ? {
                      role: 'button',
                      tabIndex: 0,
                      'aria-label': 'View details',
                      onClick: () => onRowClick?.(row),
                      onKeyDown: (e: ReactKeyboardEvent) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onRowClick?.(row)
                        }
                      },
                    }
                  : {})}
                className={cn(
                  'border-b border-border last:border-0 transition-colors duration-75',
                  interactive &&
                    'cursor-pointer outline-none hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring',
                )}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={cn(
                      'px-3 py-2 text-card-foreground',
                      col.align === 'right' && 'tabular-nums',
                      ALIGN[col.align ?? 'left'],
                      col.className,
                    )}
                  >
                    {col.cell(row) as ReactNode}
                  </td>
                ))}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
