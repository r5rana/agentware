/**
 * The dashboard's TanStack Query client (Task 17).
 *
 * Defaults are tuned for a read-only observability SPA talking to a localhost
 * sidecar: don't refetch on window focus (the data is persisted + interval-polled
 * where it matters), and DON'T retry deterministic client-side rejections —
 * a blocked cross-origin route or a schema-validation failure will never succeed
 * on a retry, so retrying only delays the error reaching the ErrorBoundary.
 */
import { QueryClient } from '@tanstack/react-query'
import { ApiError } from '@/services/api/client'
import { DEFAULT_STALE_MS } from './keys'

/** Failures that are deterministic — retrying them is pointless. */
function isNonRetryable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (
    error.kind === 'origin' ||
    error.kind === 'validation' ||
    error.kind === 'parse'
  ) {
    return true
  }
  // 4xx is a client/contract error; only retry transient 5xx + network.
  if (error.kind === 'http' && error.status != null && error.status < 500) {
    return true
  }
  return false
}

/** Build a fresh QueryClient (a factory so tests get an isolated cache). */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: DEFAULT_STALE_MS,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          if (isNonRetryable(error)) return false
          return failureCount < 2
        },
      },
    },
  })
}
