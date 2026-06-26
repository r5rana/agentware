import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
// Same-origin only: the dashboard sidecar serves webui/dist/ and /api/* from the
// SAME localhost origin. The base MUST be an ABSOLUTE same-origin root (`/`), NOT
// a relative `./`: the SPA serves index.html on nested client routes (e.g.
// `/memory/quality`), and a relative base would resolve `./assets/...` against the
// route path (`/memory/assets/...` → 404, unstyled page). An absolute `/assets/...`
// always resolves from the origin root regardless of route depth, while staying
// same-origin (no CDN, no external host) so the committed bundle is still portable.
// Tailwind v4 is wired via its first-party Vite plugin (no PostCSS config needed);
// the semantic design tokens + `@import "tailwindcss"` live in src/index.css.
export default defineConfig({
  base: '/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // No external CDN; everything is self-hosted from dist/.
    rollupOptions: {},
  },
})
