/**
 * Token-bound ECharts theme (Task 20) — the charts must look like they belong to
 * the same system as the rest of the dashboard (Linear/Vercel/Stripe restraint:
 * muted gridlines, neutral axes, color = meaning). So the theme is BUILT from the
 * live design tokens (`src/index.css` CSS custom properties) at render time, per
 * theme, rather than hardcoded — toggling light/dark re-themes the charts too.
 *
 * jsdom does not compute stylesheet-declared CSS vars, so every read carries a
 * fallback equal to the token's declared value; this keeps the theme deterministic
 * under test AND faithful in the browser.
 */
import { readCssVar, type SemanticStatus } from '@/lib/design-tokens'
import type { Theme } from '@/theme/ThemeProvider'

/** Declared token values per theme — the fallbacks when getComputedStyle is empty. */
const TOKEN_FALLBACK: Record<Theme, Record<string, string>> = {
  dark: {
    '--foreground': '#ededed',
    '--muted-foreground': '#a1a1a1',
    '--border': '#262626',
    '--card': '#111111',
    '--ring': '#3b82f6',
    '--success': '#22c55e',
    '--warning': '#f59e0b',
    '--danger': '#ef4444',
  },
  light: {
    '--foreground': '#0a0a0a',
    '--muted-foreground': '#525252',
    '--border': '#e5e5e5',
    '--card': '#ffffff',
    '--ring': '#2563eb',
    '--success': '#16a34a',
    '--warning': '#d97706',
    '--danger': '#dc2626',
  },
}

/** Read a token, falling back to its declared value (so charts render under jsdom). */
export function token(name: string, theme: Theme): string {
  let v = ''
  if (typeof document !== 'undefined') {
    try {
      v = readCssVar(name)
    } catch {
      v = ''
    }
  }
  return v || TOKEN_FALLBACK[theme][name] || '#888888'
}

/** Map a semantic status to its themed hue (color = meaning). */
export function statusColor(status: SemanticStatus, theme: Theme): string {
  switch (status) {
    case 'success':
      return token('--success', theme)
    case 'warning':
      return token('--warning', theme)
    case 'danger':
      return token('--danger', theme)
    default:
      return token('--muted-foreground', theme)
  }
}

/**
 * The ordered chart series palette: a neutral accent (the focus ring blue) leads,
 * then the semantic hues so a single-series chart is calm and a multi-series chart
 * stays legible without decorative color.
 */
export function chartPalette(theme: Theme): string[] {
  return [
    token('--ring', theme),
    token('--success', theme),
    token('--warning', theme),
    token('--danger', theme),
    token('--muted-foreground', theme),
  ]
}

/** Build a full ECharts theme object bound to the current design tokens. */
export function buildEchartsTheme(theme: Theme): Record<string, unknown> {
  const fg = token('--foreground', theme)
  const muted = token('--muted-foreground', theme)
  const border = token('--border', theme)
  const card = token('--card', theme)

  const axis = {
    axisLine: { show: true, lineStyle: { color: border } },
    axisTick: { show: false },
    axisLabel: { color: muted, fontSize: 11 },
    splitLine: { show: true, lineStyle: { color: border, type: 'dashed' as const } },
  }

  return {
    color: chartPalette(theme),
    backgroundColor: 'transparent',
    textStyle: {
      fontFamily:
        "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
      color: fg,
    },
    grid: { left: 8, right: 12, top: 24, bottom: 8, containLabel: true },
    line: {
      symbol: 'circle',
      symbolSize: 6,
      smooth: true,
      lineStyle: { width: 2, cap: 'round' as const },
    },
    categoryAxis: { ...axis, splitLine: { show: false } },
    valueAxis: axis,
    legend: {
      textStyle: { color: muted },
      icon: 'roundRect',
      itemWidth: 10,
      itemHeight: 10,
    },
    tooltip: {
      backgroundColor: card,
      borderColor: border,
      borderWidth: 1,
      textStyle: { color: fg, fontSize: 12 },
      axisPointer: { lineStyle: { color: border }, crossStyle: { color: border } },
    },
  }
}
