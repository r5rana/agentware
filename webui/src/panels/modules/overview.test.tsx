/**
 * Task 27 — Overview dual-pillar landing + per-panel export/deep-link wiring.
 *
 *   1. The Overview renders BOTH pillar tile groups (Agent/Loop health AND
 *      Knowledge-Base/Memory health), each tile DEEP-LINKING to its panel.
 *   2. `PanelFrame`'s per-panel toolbar exports valid JSON + CSV and offers a
 *      copyable deep-link — inherited by every panel with zero extra code.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
  type RenderResult,
} from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { QueryClient } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import type { UseQueryResult } from '@tanstack/react-query'

import { AppQueryProvider } from '@/services/query'
import { ThemeProvider } from '@/theme/ThemeProvider'
import { OverviewPanel } from './OverviewPanel'
import { PanelFrame } from '@/panels/PanelFrame'
import type { PanelDefinition } from '@/panels/registry'
import {
  LoopResponseSchema,
  type LoopResponse,
} from '@/services/api/contract'
import * as exportMod from '@/lib/export'

import loopFixture from '@/fixtures/loop.json'
import outcomesFixture from '@/fixtures/outcomes.json'
import kbFixture from '@/fixtures/kb.json'
import qualityFixture from '@/fixtures/quality.json'
import loopHealthFixture from '@/fixtures/loopHealth.json'

function jsonResponse(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response
}

function stubRoutedFetch() {
  const spy = vi.fn(async (input: unknown): Promise<Response> => {
    const path = new URL(String(input)).pathname
    if (path === '/api/loop') return jsonResponse(loopFixture)
    if (path === '/api/outcomes') return jsonResponse(outcomesFixture)
    if (path === '/api/kb') return jsonResponse(kbFixture)
    if (path === '/api/quality') return jsonResponse(qualityFixture)
    if (path === '/api/loop-health') return jsonResponse(loopHealthFixture)
    throw new Error(`unexpected fetch: ${path}`)
  })
  vi.stubGlobal('fetch', spy)
  return spy
}

function qOk<T>(data: T): UseQueryResult<T> {
  return { data, isLoading: false, isError: false } as UseQueryResult<T>
}

function renderApp(ui: ReactElement): RenderResult {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <ThemeProvider>
      <AppQueryProvider client={client}>
        <MemoryRouter
          future={{ v7_startTransition: true, v7_relativeSplatPath: true }}
        >
          {ui}
        </MemoryRouter>
      </AppQueryProvider>
    </ThemeProvider>,
  )
}

const overviewPanel = { id: 'overview', title: 'Overview' } as PanelDefinition

describe('OverviewPanel — dual-pillar landing', () => {
  beforeEach(() => {
    document.documentElement.className = ''
    stubRoutedFetch()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders BOTH the loop-health and memory-health tile groups', async () => {
    const loop = LoopResponseSchema.parse(loopFixture) as LoopResponse
    renderApp(<OverviewPanel panel={overviewPanel} query={qOk(loop)} />)

    const loopGroup = await screen.findByTestId('overview-loop-health')
    const memoryGroup = await screen.findByTestId('overview-memory-health')
    expect(loopGroup).toBeInTheDocument()
    expect(memoryGroup).toBeInTheDocument()

    // Agent/Loop pillar surfaces its north-star metrics.
    expect(within(loopGroup).getByText('Run success rate')).toBeInTheDocument()
    expect(
      within(loopGroup).getByText('Iteration efficiency'),
    ).toBeInTheDocument()
    // Memory pillar surfaces its north-star metrics.
    expect(within(memoryGroup).getByText('KB entries')).toBeInTheDocument()
    expect(within(memoryGroup).getByText('Recall quality')).toBeInTheDocument()
  })

  it('deep-links each tile to its panel', async () => {
    const loop = LoopResponseSchema.parse(loopFixture) as LoopResponse
    renderApp(<OverviewPanel panel={overviewPanel} query={qOk(loop)} />)

    const successLink = await screen.findByRole('link', {
      name: /run success rate — open panel/i,
    })
    expect(successLink).toHaveAttribute('href', '/loops/outcomes')

    const kbLink = screen.getByRole('link', { name: /kb entries — open panel/i })
    expect(kbLink).toHaveAttribute('href', '/memory/kb')
  })
})

describe('PanelFrame per-panel export + deep-link (Task 27)', () => {
  const data = { entry_count: 2, entries: [{ id: 'a' }, { id: 'b' }] }

  function renderReadyPanel() {
    return renderApp(
      <PanelFrame
        id="kb"
        title="KB growth"
        exportName="kb"
        query={{ data, isLoading: false, isError: false }}
      >
        {(d) => <p>entries:{d.entries.length}</p>}
      </PanelFrame>,
    )
  }

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('exports valid JSON when the JSON action is clicked', () => {
    const dl = vi.spyOn(exportMod, 'downloadFile').mockImplementation(() => {})
    renderReadyPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Export JSON' }))
    expect(dl).toHaveBeenCalledTimes(1)
    const [filename, content, mime] = dl.mock.calls[0]
    expect(filename).toBe('kb.json')
    expect(mime).toBe('application/json')
    expect(JSON.parse(content)).toEqual(data)
  })

  it('exports valid CSV when the CSV action is clicked', () => {
    const dl = vi.spyOn(exportMod, 'downloadFile').mockImplementation(() => {})
    renderReadyPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Export CSV' }))
    const [filename, content, mime] = dl.mock.calls[0]
    expect(filename).toBe('kb.csv')
    expect(mime).toBe('text/csv')
    // The flattener picks the `entries` array → a header + one row per entry.
    const lines = content.split('\n')
    expect(lines[0]).toBe('id')
    expect(lines).toHaveLength(3)
  })

  it('copies a deep-link to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    renderReadyPanel()
    fireEvent.click(screen.getByRole('button', { name: 'Copy deep link' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(1))
    expect(String(writeText.mock.calls[0][0])).toContain('/')
  })

  it('does NOT render the toolbar while loading (no data yet)', () => {
    renderApp(
      <PanelFrame
        id="kb"
        title="KB growth"
        query={{ data: undefined, isLoading: true, isError: false }}
      >
        {() => <p>body</p>}
      </PanelFrame>,
    )
    expect(screen.queryByRole('button', { name: 'Export JSON' })).toBeNull()
  })
})
