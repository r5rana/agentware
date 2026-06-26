import { useState } from 'react'
import { Check, Download, Link2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import {
  buildShareUrl,
  csvFromData,
  downloadFile,
  toJson,
} from '@/lib/export'

/**
 * Per-panel actions toolbar (Task 27) — CSV/JSON export + a copyable deep-link.
 *
 * Rendered in every `PanelFrame` header once data is ready, so EXTENSIBILITY
 * holds: a new panel inherits export + deep-links with zero extra code. Export
 * serializes the panel's validated payload (`csvFromData` flattens the most
 * table-like view); the deep-link copies a URL that restores the current route +
 * filter/drill state (the route path already encodes drill state; `params` layer
 * in any in-panel filter state).
 */
export interface PanelActionsProps {
  /** Filename base (no extension) — usually the panel id. */
  filenameBase: string
  /** The validated panel payload to export. */
  data: unknown
  /** Override the CSV rows (else the payload is flattened generically). */
  csvRows?: () => Record<string, unknown>[]
  /** In-panel filter state to encode into the deep-link as query params. */
  shareParams?: Record<string, string | number | boolean | undefined | null>
}

const ACTION_BTN =
  'inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-2xs font-medium text-muted-foreground outline-none transition-colors duration-75 hover:border-foreground/40 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring'

export function PanelActions({
  filenameBase,
  data,
  csvRows,
  shareParams,
}: PanelActionsProps) {
  const [copied, setCopied] = useState(false)

  function exportJson() {
    downloadFile(`${filenameBase}.json`, toJson(data), 'application/json')
  }

  function exportCsv() {
    const csv = csvRows ? csvFromDataRows(csvRows()) : csvFromData(data)
    downloadFile(`${filenameBase}.csv`, csv, 'text/csv')
  }

  async function copyLink() {
    // Read the LIVE URL at click-time (BrowserRouter keeps it in sync), so the
    // toolbar needs no Router context and the link always restores the current
    // route + filter/drill state.
    const loc =
      typeof window !== 'undefined'
        ? window.location
        : { origin: '', pathname: '/', search: '' }
    const url = buildShareUrl(
      { origin: loc.origin, pathname: loc.pathname, search: loc.search },
      shareParams,
    )
    try {
      await navigator.clipboard?.writeText(url)
    } catch {
      /* clipboard unavailable (insecure context / no permission) — non-fatal */
    }
    setCopied(true)
    window.setTimeout?.(() => setCopied(false), 1500)
  }

  return (
    <div
      role="group"
      aria-label="Panel actions"
      data-testid={`panel-actions-${filenameBase}`}
      className="flex items-center gap-1"
    >
      <button
        type="button"
        aria-label="Export JSON"
        title="Export JSON"
        onClick={exportJson}
        className={ACTION_BTN}
      >
        <Download aria-hidden="true" className="size-3" />
        JSON
      </button>
      <button
        type="button"
        aria-label="Export CSV"
        title="Export CSV"
        onClick={exportCsv}
        className={ACTION_BTN}
      >
        <Download aria-hidden="true" className="size-3" />
        CSV
      </button>
      <button
        type="button"
        aria-label="Copy deep link"
        title="Copy a deep-link that restores this view"
        onClick={copyLink}
        className={cn(ACTION_BTN, copied && 'text-success')}
      >
        {copied ? (
          <Check aria-hidden="true" className="size-3" />
        ) : (
          <Link2 aria-hidden="true" className="size-3" />
        )}
        {copied ? 'Copied' : 'Link'}
      </button>
    </div>
  )
}

/** Serialize explicit rows to CSV (re-uses the shared serializer). */
function csvFromDataRows(rows: Record<string, unknown>[]): string {
  return csvFromData(rows)
}
