import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// Unmount React trees between tests so the jsdom document stays isolated.
afterEach(() => {
  cleanup()
})

// jsdom doesn't implement matchMedia; framer-motion / theme code may probe it.
if (typeof window !== 'undefined' && !window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

// jsdom has no ResizeObserver; echarts-for-react's resize sensor probes it.
if (typeof globalThis !== 'undefined' && !('ResizeObserver' in globalThis)) {
  ;(globalThis as { ResizeObserver?: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// jsdom does not implement canvas; ECharts' SVG renderer still falls back to a 2D
// context for text measurement. A tiny stub silences the "Not implemented" noise
// and gives echarts a deterministic text width so layout (and thus the painted SVG
// marks the chart tests count) is stable.
if (typeof HTMLCanvasElement !== 'undefined') {
  const ctxStub = {
    measureText: (text: string) => ({ width: text.length * 6 }),
    font: '',
    fillRect: () => {},
    clearRect: () => {},
    save: () => {},
    restore: () => {},
    beginPath: () => {},
    closePath: () => {},
    fill: () => {},
    stroke: () => {},
    setTransform: () => {},
  }
  HTMLCanvasElement.prototype.getContext = (() =>
    ctxStub) as unknown as HTMLCanvasElement['getContext']
}
