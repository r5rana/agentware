/**
 * Modular panel-registry architecture (Task 18) — the extensibility CORE.
 *
 * A single `PANELS` array is the ONE source of truth for the dashboard's surface:
 * the sidebar nav (`buildNavSections`, in `components/layout/nav.ts`) and the
 * React Router routes (`PanelRoutes`, in `panels/routes.tsx`) are both GENERATED
 * from it. Adding a panel is therefore ONE registry entry — no edits to the nav,
 * the router, or anywhere else. Each entry binds a panel to exactly one typed,
 * zod-validated TanStack Query hook from the Task-17 data layer via `useData`, so
 * a panel can never receive an unvalidated payload.
 *
 * `section` is REQUIRED (Task 27): every panel is deliberately placed in the
 * seven-section IA (Overview / Loops / Knowledge Base / Performance / Health &
 * Quality / Cost & Infra / Alerts, RED order), the nav is grouped by it, the
 * Overview is a dual-pillar landing, and `PanelFrame` gives every panel CSV/JSON
 * export + a copyable deep-link with zero per-panel code.
 */
import type { ComponentType } from 'react'
import type { LucideIcon } from 'lucide-react'
import type { UseQueryResult } from '@tanstack/react-query'

import type { PanelSectionId } from '@/components/layout/nav'
import {
  AlertsPanel,
  AuthoringPanel,
  ContextTaxPanel,
  CostPanel,
  EvaluationPanel,
  FeaturesPanel,
  HealthPanel,
  KbGrowthPanel,
  LoopAnalyticsPanel,
  LoopHealthPanel,
  LoopPanel,
  OutcomesPanel,
  OverviewPanel,
  PlannerPanel,
  QualityPanel,
  ScalingPanel,
  WorkerPanel,
} from '@/panels/modules'
import {
  useAgents,
  useAlerts,
  useAuthoring,
  useContextTax,
  useCost,
  useEvals,
  useFeatures,
  useHealth,
  useKb,
  useLoop,
  useLoopAnalytics,
  useLoopHealth,
  useOutcomes,
  useQuality,
  useScaling,
} from '@/services/query'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Brain,
  ClipboardCheck,
  ClipboardList,
  DollarSign,
  FileText,
  GitBranch,
  Gauge,
  Hammer,
  Layers,
  LayoutDashboard,
  LineChart,
  ListChecks,
  Repeat,
  ShieldCheck,
} from 'lucide-react'

/** Props every panel component receives: its definition + its bound query. */
export interface PanelProps<T = unknown> {
  /** The registry entry this component is rendered for. */
  panel: PanelDefinition
  /** The schema-validated, typed query result from `panel.useData`. */
  query: UseQueryResult<T>
}

/**
 * A single dashboard panel. The registry maps each entry to a nav item + a route
 * + a data hook; the `component` renders the panel from `query`.
 */
export interface PanelDefinition<T = unknown> {
  /** Stable id (nav key, route key, test handle). */
  id: string
  /** Human title (nav label + panel header). */
  title: string
  /** Absolute route path the panel mounts at (must be unique). */
  route: string
  /** Sidebar icon. */
  icon: LucideIcon
  /** The panel's presentational component. */
  component: ComponentType<PanelProps<T>>
  /** The one typed query hook backing this panel (Task-17 data layer). */
  useData: () => UseQueryResult<T>
  /**
   * IA section this panel belongs to (Task 27 — REQUIRED so every panel is
   * deliberately placed in the seven-section IA; the nav is grouped by it).
   */
  section: PanelSectionId
}

/**
 * Type-preserving registry entry helper: infers `T` from the entry's `useData`
 * hook so the bound query type is captured, then erases it to the heterogeneous
 * `PanelDefinition` the array holds. `component` is the shared placeholder
 * (`PanelProps<unknown>`), which accepts any panel's more-specific query.
 */
export function definePanel<T>(
  def: Omit<PanelDefinition<T>, 'component'> & {
    component: ComponentType<PanelProps<NoInfer<T>>>
  },
): PanelDefinition {
  return def as unknown as PanelDefinition
}

/**
 * THE REGISTRY. Add a panel = add one entry here. Nav + routes follow.
 * (Drill-down + advanced panels — alerts, trace, loop-analytics-API, failures,
 * evals — register the same way in Tasks 22/28–33 once their endpoints land.)
 */
export const PANELS: PanelDefinition[] = [
  definePanel({
    id: 'overview',
    title: 'Overview',
    route: '/',
    icon: LayoutDashboard,
    section: 'overview',
    // Dual-pillar landing: bound to the live loop; reads outcomes/kb/quality too.
    component: OverviewPanel,
    useData: useLoop,
  }),
  // ── PILLAR 1 · PLAN_AW (planner agent) ─────────────────────────────────
  definePanel({
    id: 'plan-aw',
    title: 'Planner overview',
    route: '/plan',
    icon: ClipboardList,
    section: 'plan_aw',
    // Active planner + all previous planner metrics (derive_agents.plan).
    component: PlannerPanel,
    useData: useAgents,
  }),
  definePanel({
    id: 'authoring',
    title: 'Plan authoring',
    route: '/plan/authoring',
    icon: FileText,
    section: 'plan_aw',
    component: AuthoringPanel,
    useData: useAuthoring,
  }),
  // ── PILLAR 2 · WORK_AW (worker / execution agent) ──────────────────────
  definePanel({
    id: 'work-aw',
    title: 'Worker overview',
    route: '/work',
    icon: Hammer,
    section: 'work_aw',
    // Active worker + all previous worker metrics (derive_agents.work).
    component: WorkerPanel,
    useData: useAgents,
  }),
  // ── PILLAR 3 · LOOP (3-phase loop runs) ────────────────────────────────
  definePanel({
    id: 'live-loop',
    title: 'Live loop',
    route: '/loops/live',
    icon: Repeat,
    section: 'loop',
    component: LoopPanel,
    useData: useLoop,
  }),
  definePanel({
    id: 'outcomes',
    title: 'Run outcomes',
    route: '/loops/outcomes',
    icon: ListChecks,
    section: 'loop',
    component: OutcomesPanel,
    useData: useOutcomes,
  }),
  definePanel({
    id: 'loop-analytics',
    title: 'Loop analytics',
    route: '/loops/analytics',
    icon: GitBranch,
    section: 'loop',
    component: LoopAnalyticsPanel,
    useData: useLoopAnalytics,
  }),
  definePanel({
    id: 'loop-health',
    title: 'Loop health',
    route: '/loops/health',
    icon: Activity,
    section: 'loop',
    // Runaway detection: dup tool calls, no-progress, token burn, context overflow.
    component: LoopHealthPanel,
    useData: useLoopHealth,
  }),
  definePanel({
    id: 'kb',
    title: 'KB growth',
    route: '/memory/kb',
    icon: Brain,
    section: 'memory',
    component: KbGrowthPanel,
    useData: useKb,
  }),
  definePanel({
    id: 'scaling',
    title: 'Retrieval scaling',
    route: '/memory/scaling',
    icon: LineChart,
    section: 'memory',
    component: ScalingPanel,
    useData: useScaling,
  }),
  definePanel({
    id: 'features',
    title: 'Features',
    route: '/memory/features',
    icon: Layers,
    section: 'memory',
    component: FeaturesPanel,
    useData: useFeatures,
  }),
  definePanel({
    id: 'context-tax',
    title: 'Context tax',
    route: '/cost/context-tax',
    icon: BarChart3,
    section: 'cost',
    component: ContextTaxPanel,
    useData: useContextTax,
  }),
  definePanel({
    id: 'health',
    title: 'Health',
    route: '/health',
    icon: ShieldCheck,
    section: 'health',
    component: HealthPanel,
    useData: useHealth,
  }),
  definePanel({
    id: 'quality',
    title: 'Retrieval quality',
    route: '/health/quality',
    icon: Gauge,
    section: 'health',
    component: QualityPanel,
    useData: useQuality,
  }),
  definePanel({
    id: 'evaluation',
    title: 'Evaluation & quality',
    route: '/health/evaluation',
    icon: ClipboardCheck,
    section: 'health',
    // Eval-ledger trend (Recall@k/nDCG/MRR/reliability) + ACR-gate decisions +
    // the post-phase self-assessment text per feature. Memory + Health pillars.
    component: EvaluationPanel,
    useData: useEvals,
  }),
  definePanel({
    id: 'cost',
    title: 'Cost & infra',
    route: '/cost',
    icon: DollarSign,
    section: 'cost',
    component: CostPanel,
    useData: useCost,
  }),
  definePanel({
    id: 'alerts',
    title: 'Alerts',
    route: '/alerts',
    icon: AlertTriangle,
    section: 'alerts',
    // Symptom-based, severity-ranked alerts: regression, cost spike, runaway
    // loops, KB drift, unpromoted learnings — each deep-links to its panel.
    component: AlertsPanel,
    useData: useAlerts,
  }),
]

/** Find the registry entry whose route exactly matches `pathname` (nullable). */
export function findPanelByRoute(
  panels: PanelDefinition[],
  pathname: string,
): PanelDefinition | undefined {
  return panels.find((p) => p.route === pathname)
}
