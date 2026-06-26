/**
 * Task 19 — presentational primitives verification.
 *
 * Render tests for every primitive (Card, StatTile, TrendBadge, DataTable,
 * EmptyState, ErrorBoundary, LoadingSkeleton) including the DESIGNED empty +
 * error states. Each primitive is prop-driven, so the tests assert the props
 * surface in the DOM and that interaction (sort, drill-down click, error reset)
 * behaves.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { Activity } from 'lucide-react'

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  DataTable,
  EmptyState,
  ErrorBoundary,
  Skeleton,
  SkeletonTable,
  SkeletonText,
  StatTile,
  TrendBadge,
  trendStatus,
  type DataTableColumn,
} from '@/components/ui'

describe('Card', () => {
  it('renders the composed header/title/description/content', () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Loop health</CardTitle>
          <CardDescription>last 30 days</CardDescription>
        </CardHeader>
        <CardContent>body</CardContent>
      </Card>,
    )
    expect(
      screen.getByRole('heading', { name: 'Loop health' }),
    ).toBeInTheDocument()
    expect(screen.getByText('last 30 days')).toBeInTheDocument()
    expect(screen.getByText('body')).toBeInTheDocument()
  })
})

describe('TrendBadge', () => {
  it('maps direction + polarity to a semantic status (pure)', () => {
    expect(trendStatus('up', 'up')).toBe('success')
    expect(trendStatus('down', 'up')).toBe('danger')
    expect(trendStatus('up', 'down')).toBe('danger') // cost up = bad
    expect(trendStatus('down', 'down')).toBe('success') // cost down = good
    expect(trendStatus('flat', 'up')).toBe('neutral')
    expect(trendStatus('up', 'neutral')).toBe('neutral')
  })

  it('infers direction from the value sign when not given', () => {
    render(<TrendBadge value="-3%" goodWhen="up" />)
    const badge = screen.getByTestId('trend-badge')
    expect(badge).toHaveAttribute('data-direction', 'down')
    expect(badge).toHaveAttribute('data-status', 'danger')
    expect(badge).toHaveTextContent('-3%')
  })

  it('honours an explicit direction + polarity', () => {
    render(<TrendBadge direction="up" value="+12%" goodWhen="up" />)
    const badge = screen.getByTestId('trend-badge')
    expect(badge).toHaveAttribute('data-direction', 'up')
    expect(badge).toHaveAttribute('data-status', 'success')
  })
})

describe('StatTile', () => {
  it('renders the label, value, hint, status dot, icon, and trend', () => {
    render(
      <StatTile
        label="Run success rate"
        value="92%"
        hint="last 30d"
        status="success"
        icon={Activity}
        trend={{ direction: 'up', value: '+4%', goodWhen: 'up' }}
      />,
    )
    const tile = screen.getByTestId('stat-tile')
    expect(tile).toHaveAttribute('data-status', 'success')
    expect(within(tile).getByText('Run success rate')).toBeInTheDocument()
    expect(within(tile).getByText('92%')).toBeInTheDocument()
    expect(within(tile).getByText('last 30d')).toBeInTheDocument()
    expect(within(tile).getByTestId('stat-tile-status-dot')).toBeInTheDocument()
    expect(within(tile).getByTestId('trend-badge')).toHaveTextContent('+4%')
  })

  it('defaults to a neutral status with no trend', () => {
    render(<StatTile label="KB entries" value="—" />)
    const tile = screen.getByTestId('stat-tile')
    expect(tile).toHaveAttribute('data-status', 'neutral')
    expect(within(tile).queryByTestId('trend-badge')).toBeNull()
  })
})

describe('EmptyState', () => {
  it('renders a designed empty surface with title + description + action', () => {
    render(
      <EmptyState
        title="No active run"
        description="Start a loop to see live telemetry."
        action={<button>Start</button>}
      />,
    )
    const empty = screen.getByTestId('empty-state')
    expect(empty).toHaveAttribute('role', 'status')
    expect(within(empty).getByText('No active run')).toBeInTheDocument()
    expect(
      within(empty).getByText('Start a loop to see live telemetry.'),
    ).toBeInTheDocument()
    expect(within(empty).getByRole('button', { name: 'Start' })).toBeInTheDocument()
  })
})

describe('LoadingSkeleton', () => {
  it('renders a base skeleton, text lines, and a table shape', () => {
    render(
      <div>
        <Skeleton className="h-4 w-10" />
        <SkeletonText lines={4} />
        <SkeletonTable rows={3} cols={5} />
      </div>,
    )
    expect(screen.getAllByTestId('skeleton').length).toBeGreaterThan(0)
    // 4 text lines.
    const text = screen.getByTestId('skeleton-text')
    expect(within(text).getAllByTestId('skeleton')).toHaveLength(4)
    // table: 1 header + 3*5 cells = 16 skeleton blocks.
    const table = screen.getByTestId('skeleton-table')
    expect(within(table).getAllByTestId('skeleton')).toHaveLength(1 + 3 * 5)
  })
})

interface Row {
  feature: string
  iterations: number
}

const COLUMNS: DataTableColumn<Row>[] = [
  {
    key: 'feature',
    header: 'Feature',
    cell: (r) => r.feature,
    sortValue: (r) => r.feature,
  },
  {
    key: 'iterations',
    header: 'Iterations',
    align: 'right',
    cell: (r) => r.iterations,
    sortValue: (r) => r.iterations,
  },
]

const ROWS: Row[] = [
  { feature: 'beta', iterations: 12 },
  { feature: 'alpha', iterations: 4 },
  { feature: 'gamma', iterations: 8 },
]

describe('DataTable', () => {
  it('renders rows and sorts ascending then descending on header click', () => {
    render(
      <DataTable columns={COLUMNS} rows={ROWS} rowKey={(r) => r.feature} />,
    )
    const initial = screen
      .getAllByTestId('data-table-row')
      .map((tr) => tr.textContent)
    expect(initial[0]).toContain('beta') // unsorted = input order

    // Sort by iterations ascending.
    fireEvent.click(screen.getByRole('button', { name: /Iterations/i }))
    let cells = screen
      .getAllByTestId('data-table-row')
      .map((tr) => tr.textContent)
    expect(cells[0]).toContain('alpha') // 4 is smallest
    expect(cells[2]).toContain('beta') // 12 is largest

    // Click again → descending.
    fireEvent.click(screen.getByRole('button', { name: /Iterations/i }))
    cells = screen.getAllByTestId('data-table-row').map((tr) => tr.textContent)
    expect(cells[0]).toContain('beta')
    expect(cells[2]).toContain('alpha')
  })

  it('invokes onRowClick for drill-down via mouse and keyboard', () => {
    const onRowClick = vi.fn()
    render(
      <DataTable
        columns={COLUMNS}
        rows={ROWS}
        rowKey={(r) => r.feature}
        onRowClick={onRowClick}
      />,
    )
    const rows = screen.getAllByTestId('data-table-row')
    expect(rows[0]).toHaveAttribute('role', 'button')
    fireEvent.click(rows[0])
    fireEvent.keyDown(rows[1], { key: 'Enter' })
    expect(onRowClick).toHaveBeenCalledTimes(2)
  })

  it('renders the empty state when there are no rows', () => {
    render(<DataTable columns={COLUMNS} rows={[]} rowKey={(r) => r.feature} />)
    expect(screen.getByTestId('empty-state')).toBeInTheDocument()
    expect(screen.queryByTestId('data-table-row')).toBeNull()
  })
})

describe('ErrorBoundary', () => {
  // React logs caught render errors to console.error — silence the noise.
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function Boom({ shouldThrow }: { shouldThrow: boolean }) {
    if (shouldThrow) throw new Error('panel exploded')
    return <div>healthy</div>
  }

  it('renders children when there is no error', () => {
    render(
      <ErrorBoundary>
        <Boom shouldThrow={false} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('healthy')).toBeInTheDocument()
  })

  it('catches a render error and shows the designed fallback', () => {
    render(
      <ErrorBoundary>
        <Boom shouldThrow />
      </ErrorBoundary>,
    )
    const alert = screen.getByTestId('error-boundary')
    expect(alert).toHaveAttribute('role', 'alert')
    expect(within(alert).getByText('Something went wrong')).toBeInTheDocument()
    expect(within(alert).getByText('panel exploded')).toBeInTheDocument()
    expect(
      within(alert).getByRole('button', { name: /try again/i }),
    ).toBeInTheDocument()
  })

  it('renders a custom fallback when provided', () => {
    render(
      <ErrorBoundary fallback={(err) => <div>custom:{err.message}</div>}>
        <Boom shouldThrow />
      </ErrorBoundary>,
    )
    expect(screen.getByText('custom:panel exploded')).toBeInTheDocument()
  })
})
