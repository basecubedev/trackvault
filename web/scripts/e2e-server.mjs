#!/usr/bin/env node
/**
 * Start the archive the browser tests drive.
 *
 * One process, one origin, the production shape: the built page is served by
 * the same server that answers the API, so what the browser exercises is what a
 * deployment does rather than a development proxy.
 *
 * `E2E_SEED=0` starts an *empty* archive instead. A fresh installation is the
 * first thing anybody sees and the state most likely to render as `NaN km`, so
 * it gets a server of its own rather than a mocked page.
 */
import { execFileSync, spawn } from 'node:child_process'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const dataDir = mkdtempSync(join(tmpdir(), 'gpx-view-e2e-'))
if (process.env['E2E_SEED'] !== '0') {
  execFileSync('node', ['scripts/seed-archive.mjs'], {
    env: { ...process.env, GPX_VIEW_DATA_DIR: dataDir },
    stdio: 'inherit',
  })
  // A basemap the browser can actually render, installed through the real
  // pipeline. Without one, "the map works offline" would be a test of a grey
  // rectangle.
  execFileSync('uv', ['run', '--project', '..', 'python', 'scripts/seed-map.py'], {
    env: { ...process.env, GPX_VIEW_DATA_DIR: dataDir },
    stdio: 'inherit',
  })
}

const server = spawn(
  'uv',
  [
    'run',
    '--project',
    '..',
    'uvicorn',
    'gpx_view.main:app',
    '--host',
    '127.0.0.1',
    '--port',
    process.env['E2E_PORT'] ?? '8099',
  ],
  {
    env: {
      ...process.env,
      GPX_VIEW_DATA_DIR: dataDir,
      GPX_VIEW_WEB_DIR: new URL('../dist', import.meta.url).pathname,
      GPX_VIEW_TIMEZONE: 'UTC',
      // Opted in explicitly: uploading is off unless a deployment says
      // otherwise, and the browser tests cover the capability somebody
      // switched on rather than a default they did not choose.
      GPX_VIEW_UPLOAD_ENABLED: 'true',
    },
    stdio: 'inherit',
  },
)

const stop = () => {
  server.kill('SIGTERM')
}
process.on('SIGINT', stop)
process.on('SIGTERM', stop)
server.on('exit', (code) => {
  process.exit(code ?? 0)
})
