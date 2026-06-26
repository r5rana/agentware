import js from '@eslint/js'
import globals from 'globals'
import tseslint from '@typescript-eslint/eslint-plugin'
import tsParser from '@typescript-eslint/parser'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default [
  { ignores: ['dist', 'node_modules', 'coverage'] },
  js.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser },
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      '@typescript-eslint': tseslint,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...tseslint.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_' },
      ],
    },
  },
  {
    files: ['**/*.{test,spec}.{ts,tsx}'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    files: ['vite.config.ts', 'eslint.config.js', 'playwright.config.ts'],
    languageOptions: {
      globals: { ...globals.node },
    },
  },
  {
    // Node tooling scripts (e.g. the Playwright screenshot-capture utility):
    // run under Node, not the browser, so they get the Node global set.
    files: ['scripts/**/*.{js,mjs}'],
    languageOptions: {
      sourceType: 'module',
      // Node host globals + browser globals: Playwright scripts run under Node
      // but pass callbacks (addInitScript/page.evaluate) that execute in the
      // page context where `window`/`document`/`localStorage` are defined.
      globals: { ...globals.node, ...globals.browser },
    },
  },
]
