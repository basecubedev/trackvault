import { defineConfig, devices } from '@playwright/test'

const PORT = 8099
const EMPTY_PORT = 8098

/**
 * The browser suite drives the production shape: the built page served by the
 * same process that answers the API, over a seeded synthetic archive.
 *
 * A second server runs an **empty** archive on its own port. A fresh
 * installation is the first thing anybody sees and the state most likely to
 * render as `NaN km` or an empty chart, and it cannot be reached by filtering
 * the seeded one -- so it gets a real, empty deployment rather than a mock.
 *
 * Chromium only. A browser matrix multiplies the runtime and, for an
 * application this size, tests the same code four times.
 */
export const EMPTY_ARCHIVE = `http://127.0.0.1:${EMPTY_PORT}`
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env['CI'],
  retries: 0,
  reporter: process.env['CI'] ? 'line' : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'off',
    screenshot: 'off',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: [
    {
      command: 'node scripts/e2e-server.mjs',
      url: `http://127.0.0.1:${PORT}/healthz`,
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
    {
      command: 'node scripts/e2e-server.mjs',
      url: `${EMPTY_ARCHIVE}/healthz`,
      env: { E2E_SEED: '0', E2E_PORT: String(EMPTY_PORT) },
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
})
