import { AlertTriangle } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Button } from './button'
import { cn } from '@/lib/utils'

/**
 * ErrorBoundary primitive (Task 19) — a DESIGNED error state. A React error
 * boundary (class component, the only way to catch render errors) that isolates a
 * crashing panel from the rest of the dashboard: one panel can fail without
 * blanking the whole app. Renders a friendly fallback with the message and a
 * "Try again" reset that re-mounts the subtree.
 */
export interface ErrorBoundaryProps {
  children: ReactNode
  /** Custom fallback; receives the error + a reset handler. */
  fallback?: (error: Error, reset: () => void) => ReactNode
  /** Side-effect hook for logging (no network by default — same-origin only). */
  onError?: (error: Error, info: ErrorInfo) => void
  className?: string
}

interface ErrorBoundaryState {
  error: Error | null
}

export class ErrorBoundary extends Component<
  ErrorBoundaryProps,
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    this.props.onError?.(error, info)
  }

  reset = (): void => {
    this.setState({ error: null })
  }

  render(): ReactNode {
    const { error } = this.state
    if (error) {
      if (this.props.fallback) return this.props.fallback(error, this.reset)
      return (
        <div
          role="alert"
          data-testid="error-boundary"
          className={cn(
            'flex flex-col items-center justify-center gap-2 rounded-lg border border-danger/40 bg-card px-6 py-10 text-center',
            this.props.className,
          )}
        >
          <AlertTriangle aria-hidden="true" className="size-6 text-danger" />
          <div className="text-sm font-medium text-card-foreground">
            Something went wrong
          </div>
          <p className="max-w-sm text-2xs text-muted-foreground">
            {error.message || 'An unexpected error occurred while rendering this panel.'}
          </p>
          <Button
            variant="outline"
            size="sm"
            className="mt-2"
            onClick={this.reset}
          >
            Try again
          </Button>
        </div>
      )
    }
    return this.props.children
  }
}
