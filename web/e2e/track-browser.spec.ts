import { expect, test } from '@playwright/test'

/** The browser: paging, filtering, sorting, and what a row is allowed to say. */

test('the archive pages', async ({ page }) => {
  await page.goto('/tracks')

  await expect(page.getByTestId('result-count')).toContainText('4 tracks')
  await expect(page.getByTestId('pager-position')).toContainText('1–4 of 4')
})

test('the analysis-status filter selects and counts consistently', async ({ page }) => {
  await page.goto('/tracks?analysis_status=current')

  await expect(page.getByTestId('result-count')).toContainText('4 tracks')

  await page.locator('#analysis_status').selectOption('invalid')
  await expect(page.getByTestId('result-count')).toContainText('0 tracks')
})

test('sorting by length uses the current distance', async ({ page }) => {
  await page.goto('/tracks?sort=longest_first')

  const first = page.locator('.track-row').first()
  await expect(first).toContainText('Coastal cycle')
})

test('an unverified date is marked as unverified in the list', async ({ page }) => {
  await page.goto('/tracks')

  const row = page.locator('.track-row', { hasText: 'Planned loop with a synthetic clock' })
  await expect(row).toContainText('unverified date')
})

test('a row shows where its track went', async ({ page }) => {
  await page.goto('/tracks')

  const row = page.locator('.track-row').first()
  const preview = row.getByTestId('track-minimap')
  await expect(preview).toHaveAttribute('data-state', 'drawn')
  // A picture rather than a map component: twenty-five WebGL contexts is more
  // than a browser grants a page, and the sixteenth would cost the first its
  // canvas. See `src/map/minimap.ts`.
  await expect(preview.locator('img')).toHaveAttribute('src', /^data:image\/png/)
  // The picture belongs to the track beside it, and says whose it is.
  const title = await row.locator('.track-row__title a').innerText()
  expect(await preview.locator('img').getAttribute('alt')).toContain(title)
})

test('the list credits the map data its previews drew', async ({ page }) => {
  await page.goto('/tracks')

  await expect(page.getByTestId('list-map-attribution')).toContainText('OpenStreetMap')
})

test('a preview asks for a shape rather than a whole recording', async ({ page }) => {
  const asked: string[] = []
  page.on('request', (request) => {
    if (request.url().includes('/geometry')) asked.push(request.url())
  })

  await page.goto('/tracks')
  await expect(page.getByTestId('track-minimap').first()).toHaveAttribute('data-state', 'drawn')

  expect(asked.length).toBeGreaterThan(0)
  for (const url of asked) {
    expect(url).toMatch(/max_points=\d+/)
  }
})

test('a track can be read without leaving the list', async ({ page }) => {
  await page.goto('/tracks')

  const row = page.locator('.track-row', { hasText: 'Talaia ridge walk' })
  await row.getByRole('button', { name: /Show details for/ }).click()

  // The whole report, not a summary of it: the numbers, the map and the chart.
  await expect(page.getByTestId('detail-distance')).toBeVisible()
  await expect(page.getByTestId('track-map')).toBeVisible()
  await expect(page.getByTestId('chart')).toBeVisible()
  await expect(page).toHaveURL(/open=/)
  // Still the list: the other rows are where they were.
  await expect(page.getByTestId('result-count')).toContainText('4 tracks')
})

test('reading one track closes the one before it', async ({ page }) => {
  await page.goto('/tracks')
  await page
    .locator('.track-row', { hasText: 'Talaia ridge walk' })
    .getByRole('button', { name: /Show details for/ })
    .click()
  await expect(page.getByTestId('detail-distance')).toBeVisible()

  await page
    .locator('.track-row', { hasText: 'Coastal cycle' })
    .getByRole('button', { name: /Show details for/ })
    .click()

  await expect(page.getByTestId('track-title')).toHaveText('Coastal cycle')
  await expect(page.getByTestId('detail-distance')).toHaveCount(1)
})

test('an opened track survives a refresh', async ({ page }) => {
  await page.goto('/tracks')
  await page
    .locator('.track-row', { hasText: 'Talaia ridge walk' })
    .getByRole('button', { name: /Show details for/ })
    .click()
  await expect(page.getByTestId('track-title')).toHaveText('Talaia ridge walk')

  await page.reload()

  await expect(page.getByTestId('track-title')).toHaveText('Talaia ridge walk')
})

test('a filtered view is a link somebody can send', async ({ page }) => {
  await page.goto('/tracks?activity=cycling')

  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
  await page.reload()
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
})
