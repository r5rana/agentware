import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * The 12-column content grid (Task 15) — the substrate every panel lays out on.
 * Stripe/Linear/Vercel use exactly this: a top KPI strip over a 12-col grid that
 * scales from 5 to 50 panels. Children set their own `col-span-*`.
 */
export function ContentGrid({
  children,
  className,
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <div
      data-testid="content-grid"
      className={cn('grid grid-cols-12 gap-4', className)}
    >
      {children}
    </div>
  )
}

/** A grid cell with a default 12-col span on mobile collapsing to the given span. */
export function GridCell({
  children,
  span = 12,
  className,
}: {
  children: ReactNode
  span?: 3 | 4 | 6 | 8 | 12
  className?: string
}) {
  const spanClass: Record<number, string> = {
    3: 'col-span-12 md:col-span-6 xl:col-span-3',
    4: 'col-span-12 md:col-span-6 xl:col-span-4',
    6: 'col-span-12 lg:col-span-6',
    8: 'col-span-12 lg:col-span-8',
    12: 'col-span-12',
  }
  return <div className={cn(spanClass[span], className)}>{children}</div>
}
