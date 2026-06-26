/**
 * Drill-down route paths (Task 22) — the canonical URL builders for the
 * aggregate → list → detail navigation.
 *
 * Kept in a dependency-free module (no React, no panel imports) so BOTH the
 * aggregate panels (which link INTO a drill-down) and the drill-down views
 * (which link between each other) can import these without creating an import
 * cycle through the panel/registry barrels.
 */

/** `/api/kb/learnings` list. */
export const KB_LEARNINGS_PATH = '/memory/kb/learnings'

/** `/api/kb/projects` list. */
export const KB_PROJECTS_PATH = '/memory/kb/projects'

/** Detail route for a single learning (drill target of the learnings list). */
export function kbLearningDetailPath(id: string): string {
  return `${KB_LEARNINGS_PATH}/${encodeURIComponent(id)}`
}

/** List of knowledge entries carrying a tag. */
export function kbTagPath(tag: string): string {
  return `/memory/kb/tags/${encodeURIComponent(tag)}`
}

/** Per-feature task-transition timeline (drill target of the outcomes panel). */
export function taskTimelinePath(feature: string): string {
  return `/loops/tasks/${encodeURIComponent(feature)}`
}
