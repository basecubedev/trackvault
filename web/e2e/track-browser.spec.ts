import { expect, test } from '@playwright/test'

/** The browser: paging, filtering, sorting, and what a row is allowed to say. */

/**
 * Which kind the browser opens on.
 *
 * The seeded archive holds two recordings and two tracks whose kind the
 * evidence does not decide, so "the default applied" and "the default did not
 * apply" are two visibly different lists rather than the same one twice.
 */
test('opening the browser lists recordings, and says so', async ({ page }) => {
  await page.goto('/tracks')

  await expect(page.locator('#kind')).toHaveValue('recorded')
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
  await expect(page.locator('.track-row', { hasText: 'Talaia ridge walk' })).toBeVisible()
  await expect(page.locator('.track-row', { hasText: 'Coastal cycle' })).toBeVisible()
  // The two the archive cannot call recordings are not quietly mixed in.
  await expect(page.locator('.track-row', { hasText: 'Route with no clock' })).toHaveCount(0)
  await expect(
    page.locator('.track-row', { hasText: 'Planned loop with a synthetic clock' }),
  ).toHaveCount(0)
})

test('the default never overrules an address that asks for a kind', async ({ page }) => {
  await page.goto('/tracks?kind=unknown')

  await expect(page.locator('#kind')).toHaveValue('unknown')
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
  await expect(page.locator('.track-row', { hasText: 'Route with no clock' })).toBeVisible()
  await expect(page.locator('.track-row', { hasText: 'Talaia ridge walk' })).toHaveCount(0)

  // ...and it survives the reload that a default applied on top would undo.
  await page.reload()
  await expect(page.locator('#kind')).toHaveValue('unknown')
  await expect(page.locator('.track-row', { hasText: 'Route with no clock' })).toBeVisible()
})

test('every kind is one selection away, and still selectable', async ({ page }) => {
  await page.goto('/tracks')
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')

  await page.locator('#kind').selectOption('all')

  await expect(page).toHaveURL(/kind=all/)
  await expect(page.getByTestId('result-count')).toContainText('4 tracks')
  await expect(page.locator('.track-row', { hasText: 'Route with no clock' })).toBeVisible()

  await page.locator('#kind').selectOption('planned')

  await expect(page).toHaveURL(/kind=planned/)
  await expect(page.getByTestId('result-count')).toContainText('0 tracks')
})

test('the archive pages', async ({ page }) => {
  await page.goto('/tracks?kind=all')

  await expect(page.getByTestId('result-count')).toContainText('4 tracks')
  await expect(page.getByTestId('pager-position')).toContainText('1–4 of 4')
})

test('the analysis-status filter selects and counts consistently', async ({ page }) => {
  await page.goto('/tracks?kind=all&analysis_status=current')

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
  // The track this is about carries a clock nothing vouches for, which is why
  // its kind is undecided -- so the list has to be asked for every kind.
  await page.goto('/tracks?kind=all')

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
  await expect(preview.locator('img')).toHaveAttribute('src', /^blob:/)
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
  // Still the list: the other rows are where they were. Two of them, because
  // this opened the list the way somebody opens it -- on recordings.
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
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
  // One cycling *recording*: the address narrows the activity and the browser's
  // own default narrows the kind, and the two compose rather than replace.
  await page.goto('/tracks?activity=cycling')

  await expect(page.getByTestId('result-count')).toContainText('1 track')
  await page.reload()
  await expect(page.getByTestId('result-count')).toContainText('1 track')
})
