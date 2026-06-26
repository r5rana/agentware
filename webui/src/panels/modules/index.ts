/**
 * Barrel for the bespoke panel modules (Task 21). Each panel is a registry
 * module that consumes its bound, schema-validated TanStack Query hook and
 * renders through the shared `PanelFrame` (loading / error / empty / ready).
 * The registry imports its `component` fields from here.
 */
export { OverviewPanel } from './OverviewPanel'
export { HealthPanel } from './HealthPanel'
export { LoopPanel } from './LoopPanel'
export { LoopAnalyticsPanel } from './LoopAnalyticsPanel'
export {
  LoopHealthPanel,
  HealthBadge,
  loopHealthStatusToSemantic,
} from './LoopHealthPanel'
export { OutcomesPanel } from './OutcomesPanel'
export {
  AlertsPanel,
  SeverityBadge,
  alertSeverityToSemantic,
} from './AlertsPanel'
export { AuthoringPanel } from './AuthoringPanel'
export { KbGrowthPanel } from './KbGrowthPanel'
export { QualityPanel } from './QualityPanel'
export { ScalingPanel } from './ScalingPanel'
export { ContextTaxPanel } from './ContextTaxPanel'
export { CostPanel } from './CostPanel'
export { FeaturesPanel } from './FeaturesPanel'
export {
  TaskTimelinePanel,
  type TaskTimelinePanelProps,
} from './TaskTimelinePanel'
// PLAN_AW / WORK_AW pillar panels (the two headline agent views).
export { PlannerPanel, WorkerPanel } from './AgentPanels'
export {
  EvaluationPanel,
  EvaluationView,
  type EvaluationViewProps,
} from './EvaluationPanel'
