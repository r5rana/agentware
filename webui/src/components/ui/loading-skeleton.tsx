import type { HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * LoadingSkeleton primitive (Task 19) — a DESIGNED loading state. A single
 * pulsing muted block (the base) plus `SkeletonText` (stacked lines) and
 * `SkeletonTable` (header + rows) for the common shapes. The pulse uses Tailwind's
 * `animate-pulse` so it stays GPU-cheap and respects reduced-motion via the base
 * utility. Every panel shows these while its query is pending.
 */
export function Skeleton({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-testid="skeleton"
      aria-hidden="true"
      className={cn('animate-pulse rounded-md bg-muted', className)}
      {...props}
    />
  )
}

/** A stack of skeleton text lines; the last line is shorter for realism. */
export function SkeletonText({
  lines = 3,
  className,
}: {
  lines?: number
  className?: string
}) {
  return (
    <div
      role="status"
      aria-label="Loading"
      data-testid="skeleton-text"
      className={cn('flex flex-col gap-2', className)}
    >
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn('h-3', i === lines - 1 ? 'w-2/3' : 'w-full')}
        />
      ))}
    </div>
  )
}

/** A table-shaped skeleton: a header row over `rows` body rows of `cols` cells. */
export function SkeletonTable({
  rows = 5,
  cols = 4,
  className,
}: {
  rows?: number
  cols?: number
  className?: string
}) {
  return (
    <div
      role="status"
      aria-label="Loading"
      data-testid="skeleton-table"
      className={cn('flex flex-col gap-2', className)}
    >
      <Skeleton className="h-8 w-full" />
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="grid gap-2" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0,1fr))` }}>
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className="h-4" />
          ))}
        </div>
      ))}
    </div>
  )
}
