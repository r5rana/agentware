import { StatTile } from '@/components/ui'
import type { AuthoringResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelProps } from '@/panels/registry'
import { compactNumber, duration } from '@/panels/format'

/**
 * Plan-authoring panel (Task 21) — the attributed cost of WRITING plans (the
 * step before the loop runs): wall-time + tokens spent in plan-stage sessions,
 * plus the contributing session ids.
 */
export function AuthoringPanel({ panel, query }: PanelProps<AuthoringResponse>) {
  return (
    <PanelFrame
      id={panel.id}
      title={panel.title}
      description="Attributed plan-authoring time & tokens"
      query={query}
      span={6}
      isEmpty={(d) => (d.authoring.session_count ?? 0) === 0}
    >
      {(data) => {
        const a = data.authoring
        const sessions = a.sessions ?? []
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-3 gap-3">
              <StatTile
                label="Wall time"
                value={duration(a.wall_s ?? 0)}
                status="neutral"
                hint="time authoring plans"
              />
              <StatTile
                label="Tokens"
                value={compactNumber(a.tokens ?? 0)}
                status="neutral"
                hint="authoring tokens"
              />
              <StatTile
                label="Sessions"
                value={a.session_count ?? 0}
                status="neutral"
                hint="plan-stage sessions"
              />
            </div>
            {sessions.length > 0 ? (
              <ul className="flex flex-col divide-y divide-border rounded-lg border border-border">
                {sessions.map((s) => (
                  <li
                    key={s}
                    className="px-3 py-2 text-2xs text-muted-foreground"
                  >
                    {s}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        )
      }}
    </PanelFrame>
  )
}
