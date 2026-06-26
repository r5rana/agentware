/**
 * Task 21 — bespoke panel modules verification.
 *
 *   1. Every panel renders its REAL body from its recorded synthetic fixture
 *      (schema-parsed → typed), reaching the `ready` state.
 *   2. The shared `PanelFrame` renders the four designed states
 *      (loading / error / empty / ready), always emitting `panel-status-<id>`.
 *   3. The LIVE loop panel re-renders when a fresh `/api/loop` payload arrives
 *      (the polled live behaviour) — idle → active flips the live badge.
 *   4. The drill-down `TaskTimelinePanel` renders from the tasks fixture.
 */
import { describe, expect, it } from 'vitest'
import {
  render,
  screen,
  within,
  type RenderResult,
} from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { ReactElement } from 'react'
import { QueryClient, type UseQueryResult } from '@tanstack/react-query'

import { AppQueryProvider } from '@/services/query'
import { ThemeProvider } from '@/theme/ThemeProvider'
import type { PanelDefinition } from '@/panels/registry'
import { PanelFrame } from '@/panels/PanelFrame'
import {
  AlertsResponseSchema,
  AuthoringResponseSchema,
  ContextTaxResponseSchema,
  CostResponseSchema,
  EvalsResponseSchema,
  FeaturesResponseSchema,
  HealthResponseSchema,
  KbResponseSchema,
  LoopAnalyticsResponseSchema,
  LoopHealthResponseSchema,
  LoopResponseSchema,
  OutcomesResponseSchema,
  QualityResponseSchema,
  ScalingResponseSchema,
  TasksResponseSchema,
} from '@/services/api/contract'
import {
  AlertsPanel,
  AuthoringPanel,
  ContextTaxPanel,
  CostPanel,
  EvaluationView,
  FeaturesPanel,
  HealthPanel,
  KbGrowthPanel,
  LoopAnalyticsPanel,
  LoopHealthPanel,
  LoopPanel,
  OutcomesPanel,
  QualityPanel,
  ScalingPanel,
  TaskTimelinePanel,
} from '.'

import healthFixture from '@/fixtures/health.json'
import qualityFixture from '@/fixtures/quality.json'
import loopFixture from '@/fixtures/loop.json'
import loopAnalyticsFixture from '@/fixtures/loopAnalytics.json'
import loopHealthFixture from '@/fixtures/loopHealth.json'
import costFixture from '@/fixtures/cost.json'
import authoringFixture from '@/fixtures/authoring.json'
import contextTaxFixture from '@/fixtures/contextTax.json'
import scalingFixture from '@/fixtures/scaling.json'
import outcomesFixture from '@/fixtures/outcomes.json'
import alertsFixture from '@/fixtures/alerts.json'
import kbFixture from '@/fixtures/kb.json'
import featuresFixture from '@/fixtures/features.json'
import tasksFixture from '@/fixtures/tasks.json'
import evalsFixture from '@/fixtures/evals.json'

/** A "succeeded" query stub exposing only the fields PanelFrame reads. */
function qOk<T>(data: T): UseQueryResult<T> {
  return { data, isLoading: false, isError: false } as UseQueryResult<T>
}

/** A typed panel definition stub (only id + title are used by the body). */
function panelStub(id: string, title: string): PanelDefinition {
  return { id, title } as PanelDefinition
}

/**
 * Render with the theme + a router context. Panels that wire Task-22 drill-downs
 * (KbGrowthPanel, OutcomesPanel) call `useNavigate`/`<Link>`, so they need a
 * Router ancestor even in these isolated body tests.
 */
function renderTP(ui: ReactElement): RenderResult {
  // A QueryClient ancestor so panels that compose extra hooks internally (e.g.
  // QualityPanel's `useAlerts` for commit markers) mount without a live fetch.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <ThemeProvider>
      <AppQueryProvider client={client}>
        <MemoryRouter
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          {ui}
        </MemoryRouter>
      </AppQueryProvider>
    </ThemeProvider>,
  )
}

describe('panel modules render their bodies from fixtures', () => {
  it('HealthPanel — passing checks + table', () => {
    const data = HealthResponseSchema.parse(healthFixture)
    renderTP(
      <HealthPanel panel={panelStub('overview', 'Overview')} query={qOk(data)} />,
    )
    expect(
      screen.getByTestId('panel-status-overview'),
    ).toHaveAttribute('data-status', 'ready')
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    expect(screen.getByText('index_validate')).toBeInTheDocument()
  })

  it('LoopHealthPanel — runaway-detection badge + offender', () => {
    const data = LoopHealthResponseSchema.parse(loopHealthFixture)
    renderTP(
      <LoopHealthPanel
        panel={panelStub('loop-health', 'Loop health')}
        query={qOk(data)}
      />,
    )
    expect(
      screen.getByTestId('panel-status-loop-health'),
    ).toHaveAttribute('data-status', 'ready')
    // The critical run's offender (the dup-looping Bash tool) is surfaced.
    const offender = screen.getByTestId('loop-health-offender')
    expect(within(offender).getByText(/Bash/)).toBeInTheDocument()
    // Both fixture runs render their check tables.
    expect(
      screen.getByTestId('loop-health-run-260101-observability-demo'),
    ).toBeInTheDocument()
    expect(
      screen.getByTestId('loop-health-run-260101-stuck-loop-demo'),
    ).toBeInTheDocument()
    // The overall badge is critical (worst run).
    expect(screen.getAllByTestId('health-badge-critical').length).toBeGreaterThan(
      0,
    )
  })

  it('OutcomesPanel — success rate + outcome', () => {
    const data = OutcomesResponseSchema.parse(outcomesFixture)
    renderTP(
      <OutcomesPanel
        panel={panelStub('outcomes', 'Run outcomes')}
        query={qOk(data)}
      />,
    )
    expect(screen.getByText('Success rate')).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('AlertsPanel — severity-ranked alerts with deep-links (Task 31)', () => {
    const data = AlertsResponseSchema.parse(alertsFixture)
    renderTP(
      <AlertsPanel panel={panelStub('alerts', 'Alerts')} query={qOk(data)} />,
    )
    expect(
      screen.getByTestId('panel-status-alerts'),
    ).toHaveAttribute('data-status', 'ready')
    // The body lists each fixture alert by title.
    expect(screen.getByText('Reliability regression')).toBeInTheDocument()
    expect(screen.getByText('Cost spike')).toBeInTheDocument()
    // Critical severity badges render (worst-first ordering).
    expect(
      screen.getAllByTestId('alert-severity-critical').length,
    ).toBeGreaterThan(0)
    // Each alert deep-links to its panel route (here the regression alert).
    const link = screen.getByTestId('alert-link-regression')
    expect(link).toHaveAttribute('href', '/health/quality')
  })

  it('AuthoringPanel — wall time + sessions', () => {
    const data = AuthoringResponseSchema.parse(authoringFixture)
    renderTP(
      <AuthoringPanel
        panel={panelStub('authoring', 'Plan authoring')}
        query={qOk(data)}
      />,
    )
    expect(screen.getByText('Wall time')).toBeInTheDocument()
    expect(screen.getByText('20260101-plan')).toBeInTheDocument()
  })

  it('KbGrowthPanel — entry count + entry row', () => {
    const data = KbResponseSchema.parse(kbFixture)
    renderTP(
      <KbGrowthPanel panel={panelStub('kb', 'KB growth')} query={qOk(data)} />,
    )
    const tile = screen
      .getAllByTestId('stat-tile')
      .find((t) => within(t).queryByText('Entries'))
    expect(tile).toBeDefined()
    expect(within(tile!).getByText('4')).toBeInTheDocument()
    expect(
      screen.getByText('BM25 Deterministic Ranking'),
    ).toBeInTheDocument()
  })

  it('QualityPanel — latest reliability', () => {
    const data = QualityResponseSchema.parse(qualityFixture)
    renderTP(
      <QualityPanel
        panel={panelStub('quality', 'Retrieval quality')}
        query={qOk(data)}
      />,
    )
    expect(screen.getByText('Reliability')).toBeInTheDocument()
    expect(screen.getByText('83%')).toBeInTheDocument()
  })

  it('ScalingPanel — slope from fixture', () => {
    const data = ScalingResponseSchema.parse(scalingFixture)
    renderTP(
      <ScalingPanel
        panel={panelStub('scaling', 'Retrieval scaling')}
        query={qOk(data)}
      />,
    )
    expect(screen.getByText('Slope')).toBeInTheDocument()
    expect(screen.getByText('0.0158')).toBeInTheDocument()
  })

  it('ContextTaxPanel — window utilization', () => {
    const data = ContextTaxResponseSchema.parse(contextTaxFixture)
    renderTP(
      <ContextTaxPanel
        panel={panelStub('context-tax', 'Context tax')}
        query={qOk(data)}
      />,
    )
    expect(screen.getByText('Window used')).toBeInTheDocument()
    expect(screen.getByText('Safe')).toBeInTheDocument()
  })

  it('CostPanel — total cost + session row', () => {
    const data = CostResponseSchema.parse(costFixture)
    renderTP(
      <CostPanel panel={panelStub('cost', 'Cost & infra')} query={qOk(data)} />,
    )
    expect(screen.getByText('Total cost')).toBeInTheDocument()
    expect(screen.getByText('20260101-plan')).toBeInTheDocument()
  })

  it('FeaturesPanel — entry count + catalogue row', () => {
    const data = FeaturesResponseSchema.parse(featuresFixture)
    renderTP(
      <FeaturesPanel
        panel={panelStub('features', 'Features')}
        query={qOk(data)}
      />,
    )
    expect(
      screen.getByText('Python Runtime Conventions'),
    ).toBeInTheDocument()
  })

  it('LoopAnalyticsPanel — burndown + phase breakdown + throughput', () => {
    const data = LoopAnalyticsResponseSchema.parse(loopAnalyticsFixture)
    renderTP(
      <LoopAnalyticsPanel
        panel={panelStub('loop-analytics', 'Loop analytics')}
        query={qOk(data)}
      />,
    )
    expect(
      screen.getByTestId('panel-status-loop-analytics'),
    ).toHaveAttribute('data-status', 'ready')
    // The signature burndown viz renders with one point per main iteration.
    const burndown = screen.getByTestId('burndown')
    expect(burndown).toHaveAttribute('data-point-count', '3')
    // A pre/main/post phase breakdown chart renders.
    expect(screen.getByTestId('phase-breakdown')).toBeInTheDocument()
    // Iteration efficiency tile is shown.
    expect(screen.getByText('Efficiency')).toBeInTheDocument()
    // Throughput series is non-empty (≥1 completed day → a bar chart, not the
    // empty fallback).
    const throughput = screen.getByTestId('throughput')
    expect(within(throughput).queryByText('No completed runs yet.')).toBeNull()
    // Both hook gates render their pass/fail badge.
    expect(screen.getByTestId('gate-pre')).toHaveAttribute('data-ok', 'true')
    expect(screen.getByTestId('gate-post')).toHaveAttribute('data-ok', 'true')
  })

  it('TaskTimelinePanel — completion + transitions (drill-down)', () => {
    const data = TasksResponseSchema.parse(tasksFixture)
    renderTP(
      <TaskTimelinePanel
        feature="260101-observability-demo"
        query={qOk(data)}
      />,
    )
    expect(screen.getByText('Completion')).toBeInTheDocument()
    // 4 transitions in the fixture.
    const tile = screen
      .getAllByTestId('stat-tile')
      .find((t) => within(t).queryByText('Transitions'))
    expect(within(tile!).getByText('4')).toBeInTheDocument()
  })

  it('EvaluationView — eval trend, ACR badge + assessment slot (Task 33)', () => {
    const data = EvalsResponseSchema.parse(evalsFixture)
    renderTP(
      <EvaluationView
        panelId="evaluation"
        panelTitle="Evaluation & quality"
        query={qOk(data)}
        assessmentSlot={
          <div data-testid="evals-assessment">Post-Execution Assessment</div>
        }
      />,
    )
    expect(screen.getByTestId('panel-status-evaluation')).toHaveAttribute(
      'data-status',
      'ready',
    )
    // Reliability north-star tile renders the latest 82.5% → "83%".
    const relTile = screen
      .getAllByTestId('stat-tile')
      .find((t) => within(t).queryByText('Reliability'))
    expect(within(relTile!).getByText('83%')).toBeInTheDocument()
    // Trend: 3 series × 2 runs = 6 data points.
    const trend = screen.getByTestId('evals-trend')
    expect(within(trend).getByTestId('line-trend')).toHaveAttribute(
      'data-point-count',
      '6',
    )
    // ACR-gate decision is split out and badged.
    const acr = screen.getByTestId('evals-acr')
    expect(within(acr).getByTestId('evals-acr-badge')).toHaveTextContent(
      'no-change',
    )
    // The composed assessment slot renders.
    expect(screen.getByTestId('evals-assessment')).toHaveTextContent(
      'Post-Execution Assessment',
    )
  })
})

describe('LoopPanel — LIVE re-render on a fresh /api/loop payload', () => {
  it('flips from idle to active when the polled payload changes', () => {
    const idle = LoopResponseSchema.parse(loopFixture)
    const { rerender } = renderTP(
      <LoopPanel
        panel={panelStub('live-loop', 'Live loop')}
        query={qOk(idle)}
      />,
    )
    // Fixture has active:null → "No active run".
    expect(screen.getByTestId('loop-live-badge')).toHaveAttribute(
      'data-live',
      'false',
    )

    // A NEW /api/loop payload arrives (the interval poll) → active run.
    const active = { ...idle, active: '260101-observability-demo' }
    rerender(
      <ThemeProvider>
        <LoopPanel
          panel={panelStub('live-loop', 'Live loop')}
          query={qOk(active)}
        />
      </ThemeProvider>,
    )
    expect(screen.getByTestId('loop-live-badge')).toHaveAttribute(
      'data-live',
      'true',
    )
    expect(screen.getByText('Active run')).toBeInTheDocument()
  })
})

describe('PanelFrame — designed states', () => {
  const id = 'demo'
  const base = { id, title: 'Demo', children: (d: { v: number }) => <p>v={d.v}</p> }

  it('loading → skeleton, status=loading', () => {
    renderTP(
      <PanelFrame
        {...base}
        query={{ data: undefined, isLoading: true, isError: false }}
      />,
    )
    expect(screen.getByTestId(`panel-status-${id}`)).toHaveAttribute(
      'data-status',
      'loading',
    )
    expect(screen.getByTestId('skeleton-text')).toBeInTheDocument()
  })

  it('error → designed empty, status=error', () => {
    renderTP(
      <PanelFrame
        {...base}
        query={{ data: undefined, isLoading: false, isError: true }}
      />,
    )
    expect(screen.getByTestId(`panel-status-${id}`)).toHaveAttribute(
      'data-status',
      'error',
    )
    expect(screen.getByText("Couldn't load this panel")).toBeInTheDocument()
  })

  it('empty (isEmpty) → designed empty, status=empty', () => {
    renderTP(
      <PanelFrame
        {...base}
        query={{ data: { v: 0 }, isLoading: false, isError: false }}
        isEmpty={(d) => d.v === 0}
        emptyTitle="No data"
      />,
    )
    expect(screen.getByTestId(`panel-status-${id}`)).toHaveAttribute(
      'data-status',
      'empty',
    )
    expect(screen.getByText('No data')).toBeInTheDocument()
  })

  it('unavailable (available:false) → friendly empty, status=unavailable', () => {
    renderTP(
      <PanelFrame
        id={id}
        title="Demo"
        query={{
          data: { available: false } as never,
          isLoading: false,
          isError: false,
        }}
      >
        {() => <p>body</p>}
      </PanelFrame>,
    )
    expect(screen.getByTestId(`panel-status-${id}`)).toHaveAttribute(
      'data-status',
      'unavailable',
    )
    expect(
      screen.getByText('Knowledge base not configured'),
    ).toBeInTheDocument()
  })

  it('ready → renders children, status=ready', () => {
    renderTP(
      <PanelFrame
        {...base}
        query={{ data: { v: 7 }, isLoading: false, isError: false }}
      />,
    )
    expect(screen.getByTestId(`panel-status-${id}`)).toHaveAttribute(
      'data-status',
      'ready',
    )
    expect(screen.getByText('v=7')).toBeInTheDocument()
  })
})
