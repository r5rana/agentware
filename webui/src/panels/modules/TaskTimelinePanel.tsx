import type { ReactNode } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

import { DataTable, StatTile, type DataTableColumn } from '@/components/ui'
import type { TaskTransition, TasksResponse } from '@/services/api/contract'
import { PanelFrame } from '@/panels/PanelFrame'
import { percent, shortDate } from '@/panels/format'

/**
 * Per-task timeline panel (Task 21) — a feature's task transitions over the loop:
 * each ⬜→🟡→✅ flip with iteration + timestamp (an ⬜→✅ jump labelled approx).
 * Plan progress (done/total) leads as tiles. This is a DRILL-DOWN view: it takes
 * a `feature` + its bound `useTasks(feature)` query rather than a registry hook,
 * so Task 22 mounts it under `/api/tasks/<feature>` with zero changes here.
 */
const columns: DataTableColumn<TaskTransition>[] = [
  {
    key: 'iteration',
    header: 'Iter',
    align: 'right',
    cell: (t) => t.iteration ?? '—',
    sortValue: (t) => t.iteration ?? 0,
  },
  {
    key: 'task',
    header: 'Task',
    cell: (t) => <span className="font-medium">{t.task ?? '—'}</span>,
    sortValue: (t) => t.task ?? '',
  },
  {
    key: 'transition',
    header: 'Transition',
    cell: (t) => (
      <span className="text-muted-foreground">
        {t.from ?? '?'} → {t.to ?? '?'}
        {t.approx ? ' (approx)' : ''}
      </span>
    ),
  },
  {
    key: 'ts',
    header: 'When',
    cell: (t) => (
      <span className="text-2xs text-muted-foreground">{shortDate(t.ts)}</span>
    ),
    sortValue: (t) => t.ts ?? '',
  },
]

export interface TaskTimelinePanelProps {
  feature: string
  query: UseQueryResult<TasksResponse>
  /** Optional header-right slot (e.g. a Task-22 drill-down back link). */
  headerRight?: ReactNode
}

export function TaskTimelinePanel({
  feature,
  query,
  headerRight,
}: TaskTimelinePanelProps) {
  return (
    <PanelFrame
      id={`tasks-${feature}`}
      title={`Task timeline · ${feature}`}
      description="Per-task transitions across loop iterations"
      query={query}
      span={12}
      headerRight={headerRight}
      isEmpty={(d) => d.transitions.length === 0}
    >
      {(data) => {
        const plan = data.plan
        const done = plan?.done ?? 0
        const total = plan?.total ?? 0
        const completion = total ? done / total : 0
        return (
          <div className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
              <StatTile
                label="Completion"
                value={percent(completion, 0)}
                status={completion >= 1 ? 'success' : 'warning'}
                hint={`${done}/${total} tasks`}
              />
              <StatTile
                label="Open"
                value={plan?.open ?? 0}
                status={(plan?.open ?? 0) === 0 ? 'success' : 'warning'}
                hint="remaining tasks"
              />
              <StatTile
                label="Transitions"
                value={data.transition_count}
                status="neutral"
                hint="recorded flips"
              />
            </div>
            <DataTable
              columns={columns}
              rows={data.transitions}
              rowKey={(t, i) => `${t.task}-${t.to}-${i}`}
              caption="Task transitions"
            />
          </div>
        )
      }}
    </PanelFrame>
  )
}
