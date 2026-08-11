/// <reference types="vitest/config" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The dev server proxies `/api` to the backend so that development and
// production are the same origin. A CORS configuration that exists only in
// development is a configuration nobody exercises before it is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8080', changeOrigin: false },
      '/healthz': { target: 'http://127.0.0.1:8080', changeOrigin: false },
    },
  },
  build: {
    outDir: 'dist',
    // No source maps in the production bundle. Not for secrecy -- there is
    // nothing secret in a page whose entire job is projecting an API this
    // repository publishes -- but because they roughly double what a
    // self-hosted deployment serves and stores to help with a stack trace
    // nobody is collecting. Anyone debugging this has the source and
    // `npm run dev`.
    sourcemap: false,
    // Hashed asset names are what makes a long cache lifetime safe: a new
    // release is a new file name rather than a stale copy somebody has to
    // shift-reload away.
    assetsDir: 'assets',
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
