/**
 * Barrel for the presentational primitives (Task 19). Panels import from
 * `@/components/ui` so the primitive set stays a single, consistent surface.
 */
export { Button, buttonVariants, type ButtonProps } from './button'
export {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from './card'
export { StatTile, type StatTileProps } from './stat-tile'
export {
  TrendBadge,
  trendStatus,
  type TrendBadgeProps,
  type TrendDirection,
  type TrendPolarity,
} from './trend-badge'
export {
  DataTable,
  type DataTableColumn,
  type DataTableProps,
} from './data-table'
export { EmptyState, type EmptyStateProps } from './empty-state'
export { ErrorBoundary, type ErrorBoundaryProps } from './error-boundary'
export {
  Skeleton,
  SkeletonText,
  SkeletonTable,
} from './loading-skeleton'
