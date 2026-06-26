// Pristine look-and-feel capture (Task 25 / Task 34 visual pass).
//
// Deterministic Playwright fallback per the plan's PRE-AUTHORIZED Approval #3:
// when no browser-driving MCP can be connected, drive the running dashboard with
// headless Chromium and capture full-page screenshots of EVERY panel route at
// desktop/tablet/mobile widths in BOTH light and dark themes, collecting console
// errors and layout-overflow checks as evidence.
//
// Usage:
//   node scripts/capture-screenshots.mjs --base http://127.0.0.1:8799 --out <dir>
//
// Read-only: only navigates + screenshots; never mutates the backend or KB.
import { chromium } from '@playwright/test'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`)
  return i !== -1 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}

const BASE = arg('base', 'http://127.0.0.1:8799')
const OUT = arg('out', './screenshots')

// Every panel route from the registry (src/panels/registry.ts) — kept in sync
// with all 17 registered panels (incl. the Tasks 28–33 additions: loop-health,
// trace, failures, evaluation, alerts).
const ROUTES = [
  { id: 'overview', route: '/' },
  { id: 'live-loop', route: '/loops/live' },
  { id: 'loop-analytics', route: '/loops/analytics' },
  { id: 'loop-health', route: '/loops/health' },
  { id: 'trace', route: '/loops/trace' },
  { id: 'failures', route: '/loops/failures' },
  { id: 'outcomes', route: '/loops/outcomes' },
  { id: 'authoring', route: '/loops/authoring' },
  { id: 'kb', route: '/memory/kb' },
  { id: 'scaling', route: '/memory/scaling' },
  { id: 'features', route: '/memory/features' },
  { id: 'context-tax', route: '/performance/context-tax' },
  { id: 'health', route: '/health' },
  { id: 'quality', route: '/health/quality' },
  { id: 'evaluation', route: '/health/evaluation' },
  { id: 'cost', route: '/cost' },
  { id: 'alerts', route: '/alerts' },
]

const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'mobile', width: 390, height: 844 },
]

const THEMES = ['light', 'dark']

async function main() {
  await mkdir(OUT, { recursive: true })
  const browser = await chromium.launch()
  const report = []

  for (const theme of THEMES) {
    for (const vp of VIEWPORTS) {
      const context = await browser.newContext({
        viewport: { width: vp.width, height: vp.height },
        // deviceScaleFactor is intentionally left at 1: a 2x scale factor trips a
        // Chromium full-page-screenshot compositing bug that paints the whole
        // page white (the dark canvas renders correctly at 1x and in the real
        // browser). 1x at these widths is more than enough resolution for the
        // visual-review evidence.
      })
      // Seed the theme BEFORE any app code runs (ThemeProvider reads this key).
      await context.addInitScript((t) => {
        try {
          window.localStorage.setItem('agentware-theme', t)
        } catch {
          /* ignore */
        }
      }, theme)

      for (const r of ROUTES) {
        const page = await context.newPage()
        const consoleErrors = []
        page.on('console', (msg) => {
          if (msg.type() === 'error') consoleErrors.push(msg.text())
        })
        page.on('pageerror', (err) => consoleErrors.push(String(err)))

        const url = `${BASE}${r.route}`
        // Use 'load' not 'networkidle': the live-loop panel interval-polls
        // /api/loop forever, so the network never goes idle. Wait for the app
        // shell to mount, then settle charts/animations with a fixed delay.
        await page.goto(url, { waitUntil: 'load', timeout: 30000 })
        await page
          .waitForSelector('aside, nav, main', { timeout: 10000 })
          .catch(() => {})
        // Wait for the panels to leave the loading state before shooting — some
        // panels (Overview composes 6 queries; large /api/kb,/quality payloads)
        // take >1s to resolve, so a fixed delay would capture skeletons. Poll the
        // always-present panel-status markers until none report `loading`.
        await page
          .waitForFunction(
            () => {
              const els = Array.from(
                document.querySelectorAll('[data-testid^="panel-status-"]'),
              )
              return (
                els.length > 0 &&
                els.every((e) => e.getAttribute('data-status') !== 'loading')
              )
            },
            // Heavy derivations (/api/loop, /api/loop-health, /api/alerts) can
            // take tens of seconds over a LARGE real KB, so allow a generous cap
            // before falling through to a (skeleton) shot rather than failing.
            { timeout: 45000 },
          )
          .catch(() => {})
        // Settle charts/animations once data is in.
        await page.waitForTimeout(900)

        // Layout-overflow check: does the document scroll horizontally?
        const overflow = await page.evaluate(() => {
          const el = document.documentElement
          return {
            scrollW: el.scrollWidth,
            clientW: el.clientWidth,
            overflowX: el.scrollWidth - el.clientWidth,
          }
        })

        const file = `${r.id}__${theme}__${vp.name}.png`
        await page.screenshot({
          path: path.join(OUT, file),
          fullPage: true,
        })
        report.push({
          panel: r.id,
          route: r.route,
          theme,
          viewport: vp.name,
          file,
          consoleErrors,
          horizontalOverflowPx: overflow.overflowX,
        })
        await page.close()
      }
      await context.close()
    }
  }

  await browser.close()
  await writeFile(
    path.join(OUT, 'report.json'),
    JSON.stringify(report, null, 2),
  )

  const totalErrors = report.reduce((n, r) => n + r.consoleErrors.length, 0)
  const overflows = report.filter((r) => r.horizontalOverflowPx > 1)
  console.log(`captured ${report.length} screenshots into ${OUT}`)
  console.log(`console errors: ${totalErrors}`)
  console.log(
    `horizontal overflow (>1px): ${overflows.length} ` +
      overflows.map((o) => `${o.panel}/${o.theme}/${o.viewport}=${o.horizontalOverflowPx}px`).join(', '),
  )
  if (totalErrors > 0) {
    for (const r of report.filter((x) => x.consoleErrors.length)) {
      console.log(`  [${r.panel}/${r.theme}/${r.viewport}]`, r.consoleErrors.slice(0, 3))
    }
  }
}

main().catch((err) => {
  console.error(err)
  process.exit(1)
})
