import { fileURLToPath, URL } from 'node:url'
import { configDefaults, defineConfig } from 'vitest/config'

// Vitest config is kept separate from vite.config.ts so the test runner's bundled
// vite types never clash with the app's vite plugin types. Tests run under
// esbuild's TSX transform (no react plugin needed). The design system (Task 15)
// brings RTL render tests, so the default env is jsdom with a jest-dom setup; the
// `// @vitest-environment node` directive can opt a pure-logic file back to node.
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: true,
    // The Playwright functional specs (e2e/) use the @playwright/test runner,
    // not vitest — keep them out of `npm run test`.
    exclude: [...configDefaults.exclude, 'e2e/**'],
  },
})
