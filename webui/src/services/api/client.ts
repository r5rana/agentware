/**
 * Typed, validated, same-origin API client (Task 16).
 *
 * Every dashboard request goes through {@link apiFetch}, which:
 *   1. confines the request to the SAME ORIGIN the SPA was served from (the
 *      stdlib sidecar serves both `webui/dist/` and `/api/*` from one localhost
 *      origin) — any absolute/protocol-relative/cross-origin route is rejected
 *      BEFORE a network call is made (defence-in-depth alongside the CSP
 *      `connect-src 'self'`);
 *   2. parses the JSON body through the endpoint's zod schema from
 *      `contract.ts`, so a malformed payload is REJECTED at the boundary
 *      (`ApiError` with the zod issues) rather than flowing untyped into the UI;
 *   3. returns fully-typed data inferred from the schema.
 *
 * The typed surface is generated from `API_CONTRACT` / `API_PARAM_CONTRACT` so a
 * new endpoint is one contract entry — no hand-written method drift. The inferred
 * response types are re-exported here so callers import everything from one place.
 */
import { z } from 'zod'
import {
  API_CONTRACT,
  API_PARAM_CONTRACT,
  type ApiContractKey,
  type ApiParamContractKey,
} from './contract'

export * from './contract'

/* -------------------------------------------------------------------------- */
/* Errors                                                                     */
/* -------------------------------------------------------------------------- */

/** The kind of failure, so callers (and the ErrorBoundary) can branch. */
export type ApiErrorKind = 'network' | 'http' | 'parse' | 'origin' | 'validation'

/**
 * Single error type for every client failure. `kind` distinguishes a blocked
 * cross-origin route, a transport failure, a non-2xx status, a non-JSON body,
 * and a schema-validation failure (the malformed-payload rejection).
 */
export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly route: string
  readonly status?: number
  readonly issues?: z.ZodIssue[]

  constructor(
    kind: ApiErrorKind,
    route: string,
    message: string,
    opts: { status?: number; issues?: z.ZodIssue[]; cause?: unknown } = {},
  ) {
    super(message, { cause: opts.cause })
    this.name = 'ApiError'
    this.kind = kind
    this.route = route
    this.status = opts.status
    this.issues = opts.issues
  }
}

/* -------------------------------------------------------------------------- */
/* Same-origin guard                                                          */
/* -------------------------------------------------------------------------- */

/** The origin the SPA was served from; '' under non-browser (test) contexts. */
function currentOrigin(): string {
  if (typeof window !== 'undefined' && window.location?.origin) {
    return window.location.origin
  }
  // Deterministic base for node/test contexts so relative routes still resolve.
  return 'http://localhost'
}

/**
 * Resolve `route` against the current origin and REJECT anything that escapes
 * it (absolute cross-origin URLs, protocol-relative `//host`, or a route that
 * does not target the `/api/` namespace). Returns the safe, resolved URL string.
 */
export function resolveSameOrigin(route: string): string {
  const origin = currentOrigin()
  let url: URL
  try {
    url = new URL(route, origin + '/')
  } catch (cause) {
    throw new ApiError('origin', route, `Invalid request route: ${route}`, { cause })
  }
  if (url.origin !== origin) {
    throw new ApiError('origin', route, `Cross-origin request blocked: ${url.origin} !== ${origin}`)
  }
  if (!url.pathname.startsWith('/api/')) {
    throw new ApiError('origin', route, `Route must target /api/* but got: ${url.pathname}`)
  }
  return url.toString()
}

/* -------------------------------------------------------------------------- */
/* Core fetch                                                                 */
/* -------------------------------------------------------------------------- */

export interface ApiFetchOptions {
  /** Forwarded to fetch (e.g. an AbortController signal for query cancellation). */
  signal?: AbortSignal
}

/**
 * Fetch `route` (same-origin only) and validate the JSON body against `schema`.
 * Throws {@link ApiError} on a blocked origin, transport failure, non-2xx
 * status, non-JSON body, or schema mismatch. Resolves to the typed, parsed data.
 */
export async function apiFetch<S extends z.ZodTypeAny>(
  route: string,
  schema: S,
  options: ApiFetchOptions = {},
): Promise<z.infer<S>> {
  const url = resolveSameOrigin(route)

  let res: Response
  try {
    res = await fetch(url, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      // Never attach credentials/cookies to a localhost observability call.
      credentials: 'omit',
      signal: options.signal,
    })
  } catch (cause) {
    throw new ApiError('network', route, `Request failed: ${route}`, { cause })
  }

  if (!res.ok) {
    throw new ApiError('http', route, `HTTP ${res.status} for ${route}`, { status: res.status })
  }

  let body: unknown
  try {
    body = await res.json()
  } catch (cause) {
    throw new ApiError('parse', route, `Non-JSON response for ${route}`, {
      status: res.status,
      cause,
    })
  }

  const parsed = schema.safeParse(body)
  if (!parsed.success) {
    throw new ApiError('validation', route, `Malformed payload for ${route}`, {
      status: res.status,
      issues: parsed.error.issues,
      cause: parsed.error,
    })
  }
  return parsed.data
}

/* -------------------------------------------------------------------------- */
/* Typed endpoint surface (generated from the contract)                       */
/* -------------------------------------------------------------------------- */

/** Typed fetcher for a static endpoint, validated by its contract schema. */
export type StaticFetcher<K extends ApiContractKey> = (
  options?: ApiFetchOptions,
) => Promise<z.infer<(typeof API_CONTRACT)[K]['schema']>>

/** Typed fetcher for a parameterized drill-down endpoint. */
export type ParamFetcher<K extends ApiParamContractKey> = (
  param: string,
  options?: ApiFetchOptions,
) => Promise<z.infer<(typeof API_PARAM_CONTRACT)[K]['schema']>>

type ApiClient = { [K in ApiContractKey]: StaticFetcher<K> } & {
  [K in ApiParamContractKey]: ParamFetcher<K>
}

function buildClient(): ApiClient {
  const client = {} as Record<string, unknown>

  for (const key of Object.keys(API_CONTRACT) as ApiContractKey[]) {
    const entry = API_CONTRACT[key]
    client[key] = (options?: ApiFetchOptions) => apiFetch(entry.route, entry.schema, options)
  }
  for (const key of Object.keys(API_PARAM_CONTRACT) as ApiParamContractKey[]) {
    const entry = API_PARAM_CONTRACT[key]
    client[key] = (param: string, options?: ApiFetchOptions) =>
      apiFetch(entry.path(param), entry.schema, options)
  }
  return client as ApiClient
}

/**
 * The typed API client. Each method validates its response against the contract
 * and returns inferred-typed data:
 *
 *   const loop = await api.loop()                  // LoopResponse
 *   const detail = await api.kbLearningDetail(id)  // KbLearningDetailResponse
 */
export const api: ApiClient = buildClient()
