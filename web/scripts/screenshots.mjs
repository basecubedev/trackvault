#!/usr/bin/env node
/**
 * Take the screenshots the user documentation shows, over the demo archive.
 *
 * The pictures in `docs/` have to be regenerable, and they have to be of
 * something that is not anybody's movement history. So this drives the real
 * application, in a real browser, against the invented island in
 * `demo_region.py` -- and writes over the committed images, so a change to the
 * interface is a re-run rather than a hunt for which screenshot is now a lie.
 *
 *     npm run screenshots
 *
 * The archive is built once into `DEMO_DATA_DIR` (a temporary directory by
 * default) and reused on the next run, because generating and analysing
 * thirty-five recordings takes a few minutes and the interface is what changes.
 * Delete that directory to rebuild it.
 *
 * The browser comes from Playwright's own download (`npx playwright install
 * chromium`), or from `CHROMIUM` when a machine already has one.
 */
import { execFileSync, spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const HERE = fileURLToPath(new URL('.', import.meta.url))
const WEB = resolve(HERE, '..')
const REPOSITORY = resolve(WEB, '..')
const IMAGES = process.env['SCREENSHOT_DIR'] ?? join(REPOSITORY, 'docs', 'images')

const DEMO_PORT = 8199
const EMPTY_PORT = 8198
/** A viewport a laptop actually has, at twice the pixels, so text stays sharp. */
const VIEWPORT = { width: 1440, height: 900 }
const SCALE = 2

const demoDir = process.env['DEMO_DATA_DIR'] ?? mkdtempSync(join(tmpdir(), 'trackvault-demo-'))
const emptyDir = mkdtempSync(join(tmpdir(), 'trackvault-empty-'))

function buildArchive() {
  if (existsSync(join(demoDir, 'trackvault.sqlite3'))) {
    process.stdout.write(`reusing the demo archive in ${demoDir}\n`)
    return
  }
  process.stdout.write(`building the demo archive in ${demoDir}\n`)
  execFileSync('uv', ['run', '--project', '..', 'python', 'scripts/demo_archive.py'], {
    cwd: WEB,
    env: { ...process.env, TRACKVAULT_DATA_DIR: demoDir },
    stdio: ['ignore', 'ignore', 'inherit'],
  })
}

function serve(dataDir, port) {
  return spawn(
    'uv',
    ['run', '--project', '..', 'uvicorn', 'trackvault.main:app', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd: WEB,
      env: {
        ...process.env,
        TRACKVAULT_DATA_DIR: dataDir,
        TRACKVAULT_WEB_DIR: join(WEB, 'dist'),
        // The pictures are of months and years, so the boundaries they are
        // drawn at have to be stated rather than inherited from this machine.
        TRACKVAULT_TIMEZONE: 'UTC',
        // On, because one of the pictures is of the upload panel. A deployment
        // that has not opted in does not offer it, which is the default.
        TRACKVAULT_UPLOAD_ENABLED: 'true',
      },
      stdio: ['ignore', 'ignore', 'inherit'],
    },
  )
}

async function waitForHealth(port) {
  for (let attempt = 0; attempt < 90; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/healthz`)
      if (response.ok) return
    } catch {
      // Not up yet.
    }
    await new Promise((done) => setTimeout(done, 1000))
  }
  throw new Error(`the archive on port ${port} never became healthy`)
}

/** Wait for what a reader would wait for: the data, the chart and the map. */
async function settle(page, { map = false } = {}) {
  await page.waitForLoadState('networkidle')
  if (map) {
    await page.waitForSelector('canvas.maplibregl-canvas', { timeout: 30_000 })
    // MapLibre reports nothing to the DOM when it has finished drawing, and a
    // screenshot of a half-drawn basemap is worse than a slow script.
    await page.waitForTimeout(3500)
  }
  await page.waitForTimeout(800)
}

/**
 * Save the page from its top down to the bottom of one element.
 *
 * Every page here is longer than a screen and most of what is below the fold is
 * a table repeating the chart above it. Cutting at a named element keeps the
 * picture about the thing the surrounding paragraph is about, and keeps it that
 * way when the page grows another section.
 */
async function shot(page, name, { until, from, pad = 24 } = {}) {
  const clip = { x: 0, y: 0, width: VIEWPORT.width, height: VIEWPORT.height }
  const box = async (target) =>
    (await (typeof target === 'string' ? page.locator(target).first() : target).boundingBox())
  if (from) {
    const top = await box(from)
    if (top) clip.y = Math.max(0, top.y - pad)
  }
  if (until) {
    const bottom = await box(until)
    if (bottom) clip.height = bottom.y + bottom.height + pad - clip.y
  }
  await page.screenshot({ path: join(IMAGES, `${name}.png`), fullPage: true, clip })
  process.stdout.write(`  ${name}.png\n`)
}

async function capture(browser, base, emptyBase) {
  // British English, because the interface is written in it and because a file
  // input is drawn by the *browser*: on a German desktop an unset locale puts
  // "Dateien auswählen" in the middle of an English page.
  const page = await browser.newPage({
    viewport: VIEWPORT,
    deviceScaleFactor: SCALE,
    locale: 'en-GB',
  })

  // A fresh installation. Its own server, because an empty archive cannot be
  // reached by filtering a full one -- and it is the first thing anybody sees.
  await page.goto(`${emptyBase}/`)
  await settle(page)
  await shot(page, 'first-run', { until: '[data-testid="empty-archive"]' })

  await page.goto(`${base}/?year=2025`)
  await settle(page)
  await shot(page, 'dashboard', { until: '[data-testid="chart"]', pad: 4 })

  await page.goto(`${base}/tracks`)
  await settle(page, { map: true })
  await shot(page, 'tracks')

  await page.getByTestId('toggle-import').click()
  await settle(page)
  await shot(page, 'import', { until: page.getByTestId('track-minimap').nth(1) })

  // One recording with sensors on, so the picture of a track page is of a page
  // with everything on it rather than of the half a plain export fills.
  await page.goto(`${base}/tracks?kind=recorded&activity=hiking`)
  await settle(page)
  await page.getByRole('link', { name: 'Raudfjell, clear day' }).click()
  await settle(page, { map: true })
  await shot(page, 'track-detail', { until: '[data-testid="chart"]' })

  // The sensor chart and the classification beside it: the two panels that say
  // what the recording carried and what the archive concluded from it.
  await shot(page, 'track-evidence', {
    from: '[data-testid="sensor-panel"]',
    until: page.getByRole('button', { name: 'Use detected' }),
  })

  // Filled in but deliberately not saved. The picture is of the editor in use,
  // and the archive it is taken from stays the one the generator produced.
  await page.getByTestId('edit-title').click()
  await page.getByTestId('title-input').fill('Raudfjell, the clear day')
  await page.getByTestId('note-input').fill(
    'Up the Fjellvegen side, back the same way. Dry from the pass onwards.',
  )
  await settle(page)
  await shot(page, 'track-title', { until: '[data-testid="title-editor"]', pad: 10 })

  // The page while it is being arranged: the bars, the corners and the toolbar
  // that saves. Taken over the same recording, with the title editor closed
  // again so the picture is of one thing.
  await page.locator('.title-editor').getByRole('button', { name: 'Cancel' }).click()
  await page.getByTestId('customize-layout').click()
  await settle(page)
  await shot(page, 'track-layout', {
    from: '[data-testid="layout-toolbar"]',
    until: '[data-testid="tile-map"]',
    pad: 10,
  })
  await page.getByTestId('layout-toolbar').getByRole('button', { name: 'Cancel' }).click()

  await page.goto(`${base}/maps`)
  await settle(page)
  // The catalog below the installed package is the deployment's own provider,
  // and reading it is the one request this page makes. Without a network it
  // says so instead, and the picture stops at the installed package.
  const catalog = page.getByTestId('catalog-row')
  const rows = await catalog.count()
  await shot(page, 'offline-maps', {
    until: rows > 3 ? catalog.nth(3) : '[data-testid="maps-total-size"]',
    pad: 6,
  })

  await page.close()
}

async function main() {
  mkdirSync(IMAGES, { recursive: true })
  buildArchive()

  const servers = [serve(demoDir, DEMO_PORT), serve(emptyDir, EMPTY_PORT)]
  const stop = () => {
    for (const server of servers) server.kill('SIGTERM')
  }
  process.on('SIGINT', stop)
  process.on('SIGTERM', stop)

  let browser
  try {
    await Promise.all([waitForHealth(DEMO_PORT), waitForHealth(EMPTY_PORT)])
    browser = await chromium.launch({
      ...(process.env['CHROMIUM'] ? { executablePath: process.env['CHROMIUM'] } : {}),
      // A file input is drawn by the browser in the browser's own language, and
      // the browser takes that from the environment it was started in. Without
      // this, a German desktop puts "Dateien auswählen" in an English page.
      env: { ...process.env, LANG: 'en_GB.UTF-8', LANGUAGE: 'en_GB:en', LC_ALL: 'en_GB.UTF-8' },
      // A headless browser has no graphics card. MapLibre needs WebGL, and
      // SwiftShader is what provides it -- without this the basemap is blank.
      args: [
        '--use-gl=angle',
        '--use-angle=swiftshader',
        '--enable-unsafe-swiftshader',
        '--lang=en-GB',
      ],
    })
    process.stdout.write(`writing to ${IMAGES}\n`)
    await capture(browser, `http://127.0.0.1:${DEMO_PORT}`, `http://127.0.0.1:${EMPTY_PORT}`)
  } finally {
    if (browser) await browser.close()
    stop()
  }
}

await main()
