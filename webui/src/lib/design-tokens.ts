/**
 * Semantic design tokens (Task 15) — the typed contract over the CSS variables
 * declared in `src/index.css`. Color carries MEANING only: status maps a health
 * state to its single hue. Charts (Task 20) and badges read these so the whole
 * system stays on one palette across both themes.
 */

/** A health/severity state. The neutral canvas owns layout; these own meaning. */
export type SemanticStatus = 'success' | 'warning' | 'danger' | 'neutral'

/** Tailwind utility fragments per status, usable as `bg-*`/`text-*`/`border-*`. */
export const STATUS_TOKEN: Record<
  SemanticStatus,
  { fg: string; bg: string; border: string; cssVar: string }
> = {
  success: {
    fg: 'text-success',
    bg: 'bg-success',
    border: 'border-success',
    cssVar: 'var(--success)',
  },
  warning: {
    fg: 'text-warning',
    bg: 'bg-warning',
    border: 'border-warning',
    cssVar: 'var(--warning)',
  },
  danger: {
    fg: 'text-danger',
    bg: 'bg-danger',
    border: 'border-danger',
    cssVar: 'var(--danger)',
  },
  neutral: {
    fg: 'text-muted-foreground',
    bg: 'bg-muted',
    border: 'border-border',
    cssVar: 'var(--muted-foreground)',
  },
}

/** The semantic status hues, resolvable at runtime from the CSS custom props. */
export const SEMANTIC_COLOR_VARS = [
  '--success',
  '--warning',
  '--danger',
] as const

/** Read a CSS custom property's computed value (for ECharts theme binding). */
export function readCssVar(name: string, el: Element = document.documentElement): string {
  return getComputedStyle(el).getPropertyValue(name).trim()
}
