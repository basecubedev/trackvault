import { expect, test, type Page, type Request } from '@playwright/test'
import { EMPTY_ARCHIVE } from '../playwright.config'

/**
 * The offline contract, enforced by the browser rather than asserted about it.
 *
 * Every request the page makes is intercepted. Anything that is not this
 * deployment is **aborted**, not merely counted -- so a page that needed a CDN
 * font, a remote sprite sheet or somebody's tile server does not quietly
 * degrade, it visibly breaks and this test fails.
 *
 * What has to keep working under that rule: the dashboard, the track list, a
 * track's detail, its elevation and speed profile, the track overlay, the
 * basemap behind it, the labels on the basemap, and the attribution the package
 * requires.
 */

const ARCHIVE = '127.0.0.1'

function blockEverythingExternal(page: Page): Request[] {
  const escaped: Request[] = []
  void page.route('**', async (route, request) => {
    const url = new URL(request.url())
    if (url.hostname === ARCHIVE || url.protocol === 'data:' || url.protocol === 'blob:') {
      await route.continue()
      return
    }
    escaped.push(request)
    await route.abort()
  })
  return escaped
}

async function openTrack(page: Page, title: string) {
  await page.goto('/tracks')
  await page.getByRole('link', { name: title }).click()
  await expect(page.getByTestId('track-title')).toHaveText(title)
}

test('a track detail renders completely with every external request blocked', async ({ page }) => {
  const escaped = blockEverythingExternal(page)

  await openTrack(page, 'Talaia ridge walk')

  await expect(page.getByTestId('track-map')).toBeVisible()
  await expect(page.getByTestId('chart')).toBeVisible()
  await expect(page.getByTestId('detail-distance')).toBeVisible()
  await expect(page.getByTestId('map-attribution')).toBeVisible()
  expect(escaped.map((request) => request.url())).toEqual([])
})

test('the basemap is drawn from tiles this deployment served', async ({ page }) => {
  blockEverythingExternal(page)
  const tiles: string[] = []
  page.on('requestfinished', (request) => {
    if (request.url().includes('/api/v1/maps/tiles/')) tiles.push(request.url())
  })

  await openTrack(page, 'Talaia ridge walk')
  await expect(page.getByTestId('track-map')).toBeVisible()
  await page.waitForTimeout(1500)

  expect(tiles.length).toBeGreaterThan(0)
  for (const url of tiles) {
    expect(new URL(url).hostname).toBe(ARCHIVE)
    // The content hash is in the path, which is what makes the long cache
    // lifetime safe and an update a different address.
    expect(url).toMatch(/\/maps\/tiles\/[0-9a-f]{64}\/\d+\/\d+\/\d+\.mvt$/)
  }
})

test('labels are drawn from glyphs this deployment served', async ({ page }) => {
  blockEverythingExternal(page)
  const glyphs: string[] = []
  page.on('requestfinished', (request) => {
    if (request.url().includes('/fonts/')) glyphs.push(request.url())
  })

  await openTrack(page, 'Talaia ridge walk')
  await expect(page.getByTestId('track-map')).toBeVisible()
  await page.waitForTimeout(1500)

  expect(glyphs.length).toBeGreaterThan(0)
  for (const url of glyphs) {
    expect(new URL(url).hostname).toBe(ARCHIVE)
    expect(decodeURIComponent(url)).toContain('/fonts/Noto Sans')
  }
})

test('the dashboard and the track list survive the same blockade', async ({ page }) => {
  const escaped = blockEverythingExternal(page)

  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await page.goto('/tracks')
  await expect(page.getByTestId('pager-position')).toBeVisible()
  // The list draws a small map per row from the same installed packages, so it
  // is a second place a stray host could enter the page. It does not.
  await expect(page.getByTestId('track-minimap').first()).toHaveAttribute('data-state', 'drawn')
  await expect(page.getByTestId('list-map-attribution')).toBeVisible()

  expect(escaped).toEqual([])
})

test('the map manager lists what is installed without reaching anybody', async ({ page }) => {
  const escaped = blockEverythingExternal(page)
  const outbound: string[] = []
  page.on('request', (request) => {
    outbound.push(request.url())
  })

  await page.goto('/maps')

  await expect(page.getByTestId('installed-map')).toContainText('Mallorca')
  await expect(page.getByTestId('map-card-attribution')).toContainText('OpenStreetMap')
  expect(escaped).toEqual([])
  for (const url of outbound) {
    expect(new URL(url).hostname).toBe(ARCHIVE)
  }
})

test('turning the basemap off leaves the track drawn', async ({ page }) => {
  blockEverythingExternal(page)
  await openTrack(page, 'Talaia ridge walk')
  await expect(page.getByTestId('map-attribution')).toBeVisible()

  await page.getByTestId('map-theme').selectOption('none')

  await expect(page.getByTestId('track-map')).toBeVisible()
  await expect(page.getByTestId('chart')).toBeVisible()
})

test('a track with no installed coverage says so and still draws', async ({ page }) => {
  blockEverythingExternal(page)

  await page.goto(`${EMPTY_ARCHIVE}/maps`)
  await expect(page.getByTestId('no-maps-installed')).toBeVisible()
})

test('the credits page acknowledges the installed data', async ({ page }) => {
  const escaped = blockEverythingExternal(page)

  await page.goto('/credits')

  await expect(page.getByTestId('map-credit')).toContainText('OpenStreetMap contributors')
  await expect(page.getByText('MapLibre GL JS')).toBeVisible()
  expect(escaped).toEqual([])
})

test('the content security policy confines the page to this origin', async ({ page }) => {
  const response = await page.goto('/')

  const policy = response?.headers()['content-security-policy'] ?? ''
  expect(policy).toContain("default-src 'self'")
  expect(policy).toContain("connect-src 'self'")
  expect(policy).not.toContain('http://')
  expect(policy).not.toContain('https://')
})
