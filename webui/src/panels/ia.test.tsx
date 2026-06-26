/**
 * Task 27 — information-architecture verification.
 *
 *   1. The seven-section IA exists in RED / golden-signals order and headlines
 *      agentware as BOTH a looping agent (Loops) AND a memory system (Knowledge
 *      Base / Memory), with a dedicated Health & Quality section.
 *   2. EVERY registry entry carries a `section` that is a known IA section.
 *   3. The Loops + Knowledge-Base + Health sections each have >= 1 panel.
 */
import { describe, expect, it } from 'vitest'

import {
  SECTION_META,
  buildNavSections,
  type PanelSectionId,
} from '@/components/layout/nav'
import { PANELS } from '@/panels/registry'

const EXPECTED_SECTIONS: { id: PanelSectionId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'plan_aw', label: 'PLAN_AW · Planner' },
  { id: 'work_aw', label: 'WORK_AW · Worker' },
  { id: 'loop', label: 'LOOP · Runs' },
  { id: 'memory', label: 'Knowledge Base / Memory' },
  { id: 'health', label: 'Health & Quality' },
  { id: 'cost', label: 'Cost & Infra' },
  { id: 'alerts', label: 'Alerts' },
]

describe('information architecture (3-pillar)', () => {
  it('leads with the three agent pillars in nav order', () => {
    expect(SECTION_META.map((s) => ({ id: s.id, label: s.label }))).toEqual(
      EXPECTED_SECTIONS,
    )
  })

  it('every registry entry carries a known IA section', () => {
    const known = new Set(SECTION_META.map((s) => s.id))
    for (const panel of PANELS) {
      expect(panel.section, `panel ${panel.id} missing section`).toBeTruthy()
      expect(known.has(panel.section)).toBe(true)
    }
  })

  it('the three pillars + Memory + Health each have at least one panel', () => {
    const sections = buildNavSections(PANELS)
    for (const id of [
      'plan_aw',
      'work_aw',
      'loop',
      'memory',
      'health',
    ] as PanelSectionId[]) {
      const section = sections.find((s) => s.id === id)
      expect(section, `section ${id} should render`).toBeDefined()
      expect(section?.items.length).toBeGreaterThanOrEqual(1)
    }
  })

  it('Trace explorer and Failure ladder are removed from the IA', () => {
    const ids = new Set(PANELS.map((p) => p.id))
    expect(ids.has('trace')).toBe(false)
    expect(ids.has('failures')).toBe(false)
  })

  it('the Overview landing is the root route', () => {
    const overview = PANELS.find((p) => p.id === 'overview')
    expect(overview?.route).toBe('/')
    expect(overview?.section).toBe('overview')
  })
})
