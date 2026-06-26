/**
 * TanStack Query data layer (Task 17) — public surface.
 *
 * Panels import their typed query hook from here; `App` mounts `AppQueryProvider`.
 */
export * from './hooks'
export * from './keys'
export { createQueryClient } from './queryClient'
export { AppQueryProvider, type AppQueryProviderProps } from './provider'
