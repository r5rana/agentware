import { forwardRef, type HTMLAttributes } from 'react'
import { cn } from '@/lib/utils'

/**
 * Card primitive (Task 19) — the panel surface every dashboard module sits on.
 * shadcn/ui-style owned code: a neutral `bg-card` surface with a crisp 1px border
 * and rounded corners (the "Vercel grid" aesthetic). Compose with the Header /
 * Title / Description / Content / Footer subcomponents for consistent spacing.
 */
export const Card = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        'rounded-lg border border-border bg-card text-card-foreground',
        className,
      )}
      {...props}
    />
  ),
)
Card.displayName = 'Card'

export const CardHeader = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('flex flex-col gap-1 p-4 pb-2', className)}
    {...props}
  />
))
CardHeader.displayName = 'CardHeader'

export const CardTitle = forwardRef<
  HTMLHeadingElement,
  HTMLAttributes<HTMLHeadingElement>
>(({ className, ...props }, ref) => (
  <h3
    ref={ref}
    className={cn(
      'text-sm font-semibold leading-none tracking-tight text-card-foreground',
      className,
    )}
    {...props}
  />
))
CardTitle.displayName = 'CardTitle'

export const CardDescription = forwardRef<
  HTMLParagraphElement,
  HTMLAttributes<HTMLParagraphElement>
>(({ className, ...props }, ref) => (
  <p
    ref={ref}
    className={cn('text-2xs text-muted-foreground', className)}
    {...props}
  />
))
CardDescription.displayName = 'CardDescription'

export const CardContent = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('p-4 pt-2', className)} {...props} />
))
CardContent.displayName = 'CardContent'

export const CardFooter = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('flex items-center p-4 pt-2', className)}
    {...props}
  />
))
CardFooter.displayName = 'CardFooter'
