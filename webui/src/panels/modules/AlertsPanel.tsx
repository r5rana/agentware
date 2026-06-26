import { Link } from 'react-router-dom'

import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import { cn } from '@/lib/utils'
import type { SemanticStatus } from '@/lib/design-tokens'
import type {
  Alert,
  AlertSeverity,
  AlertsResponse,
} from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'

/**
 * ALERTS surface (Task 31) — symptom-based, severity-ranked alerts per SRE best
 * practice. Bound to `/api/alerts`, it rolls up reliability/nDCG regression,
 * retrieval scaling-slope, cost spikes, stuck-loop/runaway signals,
 * stale/conflicting KB, and unpromoted LEARNED/DECISION markers at finish into a
 * single ranked list. Each alert DEEP-LINKS to the panel that explains it, and
 * the headline tiles show the open-alert count by severity. Purely prop-driven +
 * idle-resilient (reads persisted derivations; no active run required).
 */

const SEVERITY_LABEL: Record<AlertSeverity, string> = {
  critical: 'Critical',
  warning: 'Warning',
  info: 'Info',
}

/**
 * Alert severity → the semantic color it carries (color = MEANING). `info` maps
 * to the neutral hue since the palette reserves color for actionable states
 * (success/warning/danger) on a neutral canvas (Task 15).
 */
export function alertSeverityToSemantic(severity: AlertSeverity): SemanticStatus {
  if (severity === 'critical') return 'danger'
  if (severity === 'warning') return 'warning'
  return 'neutral'
}

/** A severity pill (Critical / Warning / Info) with a semantic dot. */
export function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  const semantic = alertSeverityToSemantic(severity)
  return (
    <span
      data-testid={`alert-severity-${severity}`}
      data-severity={severity}
      className={cn(
        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-2xs font-medium',
        semantic === 'danger' && 'border-danger/40 text-danger',
        semantic === 'warning' && 'border-warning/40 text-warning',
        semantic === 'neutral' && 'border-border text-muted-foreground',
      )}
    >
      <span
        className={cn(
          'inline-block h-1.5 w-1.5 rounded-full',
          semantic === 'danger' && 'bg-danger',
          semantic === 'warning' && 'bg-warning',
          semantic === 'neutral' && 'bg-muted-foreground',
        )}
      />
      {SEVERITY_LABEL[severity]}
    </span>
  )
}

export function AlertsPanel({ panel, query }: PanelProps<AlertsResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Symptom-based, severity-ranked alerts — regression, cost, runaway loops, KB drift"
      query={query}
      span={12}
      isEmpty={(d) => d.alerts.length === 0}
      emptyTitle="All clear"
      emptyDescription="No open alerts — reliability, cost, loops, and the KB are within bounds."
    >
      {(data) => {
        const summary = data.summary ?? { critical: 0, warning: 0, info: 0 }
        const columns: DataTableColumn<Alert>[] = [
          {
            key: 'severity',
            header: 'Severity',
            cell: (a) => <SeverityBadge severity={a.severity} />,
          },
          {
            key: 'title',
            header: 'Alert',
            cell: (a) => (
              <span className="font-medium text-card-foreground">{a.title}</span>
            ),
          },
          {
            key: 'detail',
            header: 'Detail',
            cell: (a) => (
              <span className="text-2xs text-muted-foreground">
                {a.detail || '—'}
                {a.feature ? (
                  <span className="ml-1 text-muted-foreground/70">
                    · {a.feature}
                  </span>
                ) : null}
              </span>
            ),
          },
          {
            key: 'deep_link',
            header: '',
            cell: (a) =>
              a.deep_link ? (
                <Link
                  to={a.deep_link}
                  data-testid={`alert-link-${a.id}`}
                  className="text-2xs font-medium text-primary underline-offset-2 hover:underline"
                >
                  View →
                </Link>
              ) : (
                <span className="text-2xs text-muted-foreground">—</span>
              ),
          },
        ]
        return (
          <div className="flex flex-col gap-4" data-testid="alerts-body">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              <StatTile
                label="Open alerts"
                value={data.open_count ?? data.alerts.length}
                status={
                  (data.open_count ?? data.alerts.length) > 0
                    ? alertSeverityToSemantic(
                        data.status === 'ok' ? 'info' : data.status,
                      )
                    : 'success'
                }
                hint="needs attention"
              />
              <StatTile
                label="Critical"
                value={summary.critical}
                status={summary.critical > 0 ? 'danger' : 'neutral'}
                hint="alerts"
              />
              <StatTile
                label="Warning"
                value={summary.warning}
                status={summary.warning > 0 ? 'warning' : 'neutral'}
                hint="alerts"
              />
              <StatTile
                label="Info"
                value={summary.info}
                status="neutral"
                hint="alerts"
              />
            </div>

            <DataTable
              columns={columns}
              rows={data.alerts}
              rowKey={(a) => a.id}
              empty="No open alerts"
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
