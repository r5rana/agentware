import { Inbox, type LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * EmptyState primitive (Task 19) — a DESIGNED empty surface (not an afterthought):
 * a muted icon, a title, a short description, and an optional action. Used by the
 * DataTable, idle panels, and any "no data yet" state so the dashboard reads as
 * intentional even with zero rows (the idle-resilient requirement).
 */
export interface EmptyStateProps {
  title?: string
  description?: string
  icon?: LucideIcon
  action?: ReactNode
  className?: string
}

export function EmptyState({
  title = 'Nothing here yet',
  description,
  icon: Icon = Inbox,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      role="status"
      data-testid="empty-state"
      className={cn(
        'flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border bg-card/50 px-6 py-10 text-center',
        className,
      )}
    >
      <Icon
        aria-hidden="true"
        className="size-6 text-muted-foreground/60"
      />
      <div className="text-sm font-medium text-card-foreground">{title}</div>
      {description ? (
        <p className="max-w-sm text-2xs text-muted-foreground">{description}</p>
      ) : null}
      {action ? <div className="mt-2">{action}</div> : null}
    </div>
  )
}
