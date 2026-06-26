import {
  AlertTriangle,
  Brain,
  ClipboardList,
  DollarSign,
  Hammer,
  HeartPulse,
  LayoutDashboard,
  Repeat,
  type LucideIcon,
} from 'lucide-react'

import type { PanelDefinition } from '@/panels/registry'

/**
 * Sidebar nav MODEL (Task 15 shell → Task 18 registry-driven).
 *
 * The IA leads with the TWO pillars agentware embodies — **Loops** (the
 * differentiator) and **Knowledge Base / Memory** — then Performance, Health &
 * Quality, Cost & Infra, and Alerts (RED / golden-signals order, most-important
 * on top). As of Task 18 the section ITEMS are no longer hand-listed: they are
 * GENERATED from the panel registry by `buildNavSections`, so adding a panel is
 * one registry entry. Only the section METADATA (label + icon + order) lives here.
 * Task 27 formalises the section IA (Overview landing tiles, export, deep-links).
 */
export interface NavItem {
  id: string
  label: string
  route: string
}

export interface NavSection {
  id: string
  label: string
  icon: LucideIcon
  items: NavItem[]
}

/**
 * The set of IA sections a panel may belong to (Task 27 — the seven-section IA,
 * RED / golden-signals order, headlining agentware as BOTH a looping agent AND a
 * memory system).
 */
export type PanelSectionId =
  | 'overview'
  | 'plan_aw'
  | 'work_aw'
  | 'loop'
  | 'memory'
  | 'health'
  | 'cost'
  | 'alerts'

/** Panels without an explicit `section` fall here. */
export const DEFAULT_SECTION: PanelSectionId = 'overview'

/**
 * Ordered section metadata. The IA LEADS WITH THE THREE AGENT PILLARS — PLAN_AW
 * (planner), WORK_AW (worker/execution), LOOP (3-phase loop runs) — the way an
 * operator actually thinks about agentware, then the supporting analytics
 * (memory, health & quality, cost, alerts) in golden-signals order.
 */
export const SECTION_META: ReadonlyArray<{
  id: PanelSectionId
  label: string
  icon: LucideIcon
}> = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'plan_aw', label: 'PLAN_AW · Planner', icon: ClipboardList },
  { id: 'work_aw', label: 'WORK_AW · Worker', icon: Hammer },
  { id: 'loop', label: 'LOOP · Runs', icon: Repeat },
  { id: 'memory', label: 'Knowledge Base / Memory', icon: Brain },
  { id: 'health', label: 'Health & Quality', icon: HeartPulse },
  { id: 'cost', label: 'Cost & Infra', icon: DollarSign },
  { id: 'alerts', label: 'Alerts', icon: AlertTriangle },
]

/**
 * Build the sectioned sidebar nav FROM the panel registry. Sections keep the
 * canonical order; each panel becomes a nav item under its section; empty
 * sections are dropped so the sidebar only shows what exists.
 */
export function buildNavSections(panels: PanelDefinition[]): NavSection[] {
  return SECTION_META.map((meta) => ({
    id: meta.id,
    label: meta.label,
    icon: meta.icon,
    items: panels
      .filter((panel) => (panel.section ?? DEFAULT_SECTION) === meta.id)
      .map((panel) => ({
        id: panel.id,
        label: panel.title,
        route: panel.route,
      })),
  })).filter((section) => section.items.length > 0)
}
