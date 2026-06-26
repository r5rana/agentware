/**
 * Task 22 — drill-down navigation (aggregate → list → detail) verification.
 *
 *   1. KB chain: the KB-growth AGGREGATE (entry count) → "Browse learnings"
 *      LIST → a learning's DETAIL renders the underlying record (its body).
 *   2. Tag chain: a learning detail's tag link → the by-tag LIST renders the
 *      entries carrying that tag.
 *   3. Loop chain: the run-outcomes AGGREGATE → a feature row → that feature's
 *      per-task timeline DETAIL.
 *
 * Each test drives the FULL registry-generated router (`PanelRoutes`) over a
 * URL-routing fetch mock returning the recorded synthetic fixtures, so the
 * navigation + the zod-validated data layer are exercised end-to-end.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient } from '@tanstack/react-query'

import { PanelRoutes } from '@/panels/routes'
import { AppQueryProvider } from '@/services/query'
import { ThemeProvider } from '@/theme/ThemeProvider'

import kbFixture from '@/fixtures/kb.json'
import kbLearningsFixture from '@/fixtures/kbLearnings.json'
import kbLearningDetailFixture from '@/fixtures/kbLearningDetail.json'
import kbProjectsFixture from '@/fixtures/kbProjects.json'
import kbTagFixture from '@/fixtures/kbTag.json'
import outcomesFixture from '@/fixtures/outcomes.json'
import tasksFixture from '@/fixtures/tasks.json'

/** A 200 JSON response stub. */
function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

/** Route every /api/* request to its recorded synthetic fixture. */
function stubRoutedFetch() {
  const spy = vi.fn(async (input: unknown): Promise<Response> => {
    const path = new URL(String(input)).pathname
    if (path === '/api/kb') return jsonResponse(kbFixture)
    if (path === '/api/kb/learnings') return jsonResponse(kbLearningsFixture)
    if (path.startsWith('/api/kb/learnings/'))
      return jsonResponse(kbLearningDetailFixture)
    if (path === '/api/kb/projects') return jsonResponse(kbProjectsFixture)
    if (path.startsWith('/api/kb/tags/')) return jsonResponse(kbTagFixture)
    if (path.startsWith('/api/tasks/')) return jsonResponse(tasksFixture)
    if (path === '/api/outcomes') return jsonResponse(outcomesFixture)
    throw new Error(`unexpected fetch: ${path}`)
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

/** Render the full router at `initialPath` with an isolated, retry-off cache. */
function renderApp(initialPath: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <ThemeProvider>
      <AppQueryProvider client={client}>
        <MemoryRouter
          initialEntries={[initialPath]}
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          <PanelRoutes />
        </MemoryRouter>
      </AppQueryProvider>
    </ThemeProvider>,
  )
}

beforeEach(() => {
  document.documentElement.className = ''
  stubRoutedFetch()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Wait until a panel's always-present status marker reaches `ready`. */
async function expectReady(id: string): Promise<void> {
  await waitFor(() =>
    expect(screen.getByTestId(`panel-status-${id}`)).toHaveAttribute(
      'data-status',
      'ready',
    ),
  )
}

describe('drill-down navigation', () => {
  it('KB: aggregate count → learnings list → a learning detail (renders the record)', async () => {
    renderApp('/memory/kb')

    // 1) AGGREGATE — the KB panel reaches `ready` and shows the entry count.
    await expectReady('kb')

    // 2) Drill into the LIST via the "Browse learnings" link.
    fireEvent.click(screen.getByRole('link', { name: /browse learnings/i }))
    await expectReady('kb-learnings')
    // The list shows the synthetic learnings.
    expect(
      await screen.findByText('Geofence Reminders Not Firing'),
    ).toBeInTheDocument()

    // 3) Drill into the DETAIL by clicking the learning's row.
    fireEvent.click(screen.getByText('Geofence Reminders Not Firing'))
    await expectReady('kb-learning-learn-geofence-reminders')
    // The DETAIL renders the underlying record's BODY.
    expect(
      await screen.findByText(/defineTask was nested/i),
    ).toBeInTheDocument()
  })

  it('KB: a learning detail tag → the by-tag list', async () => {
    renderApp('/memory/kb/learnings/learn-geofence-reminders')

    await expectReady('kb-learning-learn-geofence-reminders')

    // Click the #geofence tag → the by-tag list.
    fireEvent.click(screen.getByRole('link', { name: '#geofence' }))
    await expectReady('kb-tag-geofence')
    expect(
      await screen.findByText('Geofence Reminders Not Firing'),
    ).toBeInTheDocument()
  })

  it('Loops: run-outcomes aggregate → a feature row → its task timeline', async () => {
    renderApp('/loops/outcomes')

    // AGGREGATE — outcomes panel ready with the success-rate tile.
    await expectReady('outcomes')

    // Drill into the per-feature timeline by clicking the feature row.
    fireEvent.click(screen.getByText('260101-observability-demo'))
    await expectReady('tasks-260101-observability-demo')
    // The timeline DETAIL renders the feature's transitions.
    expect(await screen.findByText('Completion')).toBeInTheDocument()
  })
})
