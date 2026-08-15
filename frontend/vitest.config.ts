import { defineConfig } from 'vitest/config'

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  test: { include: ['src/**/*.test.{ts,tsx}'], environment: 'jsdom', setupFiles: './src/test/setup.ts', globals: true },
})
