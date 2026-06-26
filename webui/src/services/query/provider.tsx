/**
 * App-wide TanStack Query provider (Task 17).
 *
 * Owns ONE QueryClient for the SPA's lifetime (created lazily via `useState` so
 * a re-render never throws away the cache). Wrap the app shell with this so every
 * panel hook shares one cache and the live loop poll keeps running across route
 * changes.
 */
import { useState, type ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createQueryClient } from './queryClient'

export interface AppQueryProviderProps {
  children: ReactNode
  /** Inject a client in tests; defaults to the app's tuned client. */
  client?: QueryClient
}

export function AppQueryProvider({ children, client }: AppQueryProviderProps) {
  const [queryClient] = useState(() => client ?? createQueryClient())
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
}
