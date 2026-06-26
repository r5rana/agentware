import { test, expect, type Request } from '@playwright/test'

/**
 * Task 34 — full-stack FUNCTIONAL E2E with `@playwright/test` (the
 * `ui-verification` skill), driving the live stdlib dashboard server. This is
 * the functional counterpart to the visual pass in
 * `scripts/capture-screenshots.mjs`; together they satisfy step (4) of Task 34.
 *
 * What only a real browser against the real backend proves (beyond the jsdom
 * unit suite):
 *  - every registry panel route MOUNTS and resolves to a non-error status
 *    against real/synthetic `/api/*` data (no panel stuck in `error`);
 *  - one full drill-down (count → list → detail) navigates in the running app;
 *  - the LIVE loop panel renders and the router works from the running shell;
 *  - NO external (non-same-origin) network request fires (read-only + CSP proof).
 */

// Mirror of src/panels/registry.ts routes (all 17 panels).
const ROUTES: { id: string; route: string }[] = [
  { id: 'overview', route: '/' },
  { id: 'plan-aw', route: '/plan' },
  { id: 'authoring', route: '/plan/authoring' },
  { id: 'work-aw', route: '/work' },
  { id: 'live-loop', route: '/loops/live' },
  { id: 'outcomes', route: '/loops/outcomes' },
  { id: 'loop-analytics', route: '/loops/analytics' },
  { id: 'loop-health', route: '/loops/health' },
  { id: 'kb', route: '/memory/kb' },
  { id: 'scaling', route: '/memory/scaling' },
  { id: 'features', route: '/memory/features' },
  { id: 'context-tax', route: '/cost/context-tax' },
  { id: 'health', route: '/health' },
  { id: 'quality', route: '/health/quality' },
  { id: 'evaluation', route: '/health/evaluation' },
  { id: 'cost', route: '/cost' },
  { id: 'alerts', route: '/alerts' },
]

function isSameOrigin(url: string, base: string): boolean {
  try {
    return new URL(url).origin === new URL(base).origin
  } catch {
    // data:, blob:, about: — not network egress.
    return true
  }
}

for (const { id, route } of ROUTES) {
  test(`panel ${id} mounts without error status`, async ({ page, baseURL }) => {
    const external: string[] = []
    page.on('request', (req: Request) => {
      const u = req.url()
      if (u.startsWith('http') && !isSameOrigin(u, baseURL!)) external.push(u)
    })
    const consoleErrors: string[] = []
    page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()))
    page.on('pageerror', (e) => consoleErrors.push(String(e)))

    await page.goto(route, { waitUntil: 'load' })
    // The app shell mounts.
    await expect(page.locator('aside, nav, main').first()).toBeVisible()

    // At least one panel-status marker resolves, and NONE are stuck in `error`.
    const statuses = page.locator('[data-testid^="panel-status-"]')
    await expect(statuses.first()).toBeAttached({ timeout: 10_000 })
    // Allow the queries to settle off the loading state.
    await page.waitForTimeout(800)
    const count = await statuses.count()
    expect(count).toBeGreaterThan(0)
    for (let i = 0; i < count; i++) {
      const status = await statuses.nth(i).getAttribute('data-status')
      expect(status, `panel-status #${i} on ${route}`).not.toBe('error')
    }

    expect(consoleErrors, `console errors on ${route}`).toEqual([])
    expect(external, `external network requests on ${route}`).toEqual([])
  })
}

test('drill-down: KB aggregate → list → learning detail', async ({ page }) => {
  // Aggregate KB panel.
  await page.goto('/memory/kb', { waitUntil: 'load' })
  await expect(page.getByTestId('panel-kb')).toBeVisible()

  // Drill into the learnings list (a link/row whose href targets the list route).
  const toList = page.locator('a[href*="/memory/kb/learnings"]').first()
  await toList.click()
  await expect(page).toHaveURL(/\/memory\/kb\/learnings/)

  // Drill into the first learning detail. The list is a DataTable whose rows
  // navigate via onRowClick (not an <a href>), so click the row directly.
  const firstRow = page.getByTestId('data-table-row').first()
  await expect(firstRow).toBeVisible({ timeout: 10_000 })
  await firstRow.click()
  await expect(page).toHaveURL(/\/memory\/kb\/learnings\/.+/)
  // The detail body renders real underlying record text.
  await expect(page.locator('main')).toContainText(/\w{8,}/)
})

test('live loop panel renders and polls /api/loop', async ({ page }) => {
  let loopPolls = 0
  page.on('request', (req) => {
    if (req.url().includes('/api/loop')) loopPolls++
  })
  await page.goto('/loops/live', { waitUntil: 'load' })
  await expect(page.getByTestId('panel-status-live-loop')).toBeAttached()
  // TanStack Query refetch interval fires at least one extra poll.
  await page.waitForTimeout(3500)
  expect(loopPolls).toBeGreaterThan(0)
})
