import { expect, test } from '@playwright/test'

/**
 * The dashboard, in a real browser, over a seeded synthetic archive.
 *
 * What matters here is what the page *claims*: that recorded is the default,
 * that a total says how much of its period it covers, and that clicking a month
 * reaches the tracks that month counted rather than a set the browser worked
 * out for itself.
 */

test('the dashboard opens and defaults to recorded', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible()
  await expect(page.locator('#scope')).toHaveValue('recorded')
  // The year is the archive's newest, not the reader's calendar year.
  await expect(page.locator('#year')).toHaveValue('2025')
  await expect(page.getByTestId('scope-explainer')).toContainText('actually happened')
})

test('the year totals cover the measured recordings only', async ({ page }) => {
  await page.goto('/?year=2025')

  // Two measured recordings are dated; the route with a synthetic clock and the
  // one with no clock at all are neither recorded nor dated, so the recorded
  // year holds exactly the two.
  await expect(page.getByTestId('metric-tracks')).toContainText('2')
  await expect(page.getByTestId('metric-distance')).not.toContainText('—')
})

test('the undecided scope reports what belongs to no month, and why', async ({ page }) => {
  await page.goto('/?year=2025&scope=unknown')

  // One route carries no clock at all and one carries a clock nothing vouches
  // for. Both have a length and no month, so the scope has no year to total --
  // and the two halves are still named rather than quietly dropped.
  await expect(page.getByTestId('nothing-dated')).toBeVisible()
  await expect(page.getByTestId('unplaced-note')).toContainText('nothing vouches for')
  await expect(page.getByTestId('unplaced-note')).toContainText('1 track(s) carry no date')
  await expect(page.getByRole('link', { name: 'List every unknown track' })).toBeVisible()
})

test('the monthly chart shows twelve months', async ({ page }) => {
  await page.goto('/?year=2025')

  await expect(page.getByTestId('chart')).toBeVisible()
  const rows = page.getByTestId('monthly-table').locator('tbody tr')
  await expect(rows).toHaveCount(12)
})

test('the activity filter narrows the totals', async ({ page }) => {
  await page.goto('/?year=2025')
  await expect(page.getByTestId('metric-tracks')).toContainText('2')

  await page.locator('#activity').selectOption('cycling')

  await expect(page.getByTestId('metric-tracks')).toContainText('1')
})

test('opening a month lists exactly the tracks that month counted', async ({ page }) => {
  await page.goto('/?year=2025')
  const october = page.getByTestId('monthly-table').locator('tbody tr').nth(9)
  await expect(october.locator('td').nth(1)).toHaveText('1')

  await page.getByTestId('open-month-10').click()

  await expect(page).toHaveURL(/\/tracks\?year=2025&month=10/)
  await expect(page.getByTestId('result-count')).toContainText('1 track')
  await expect(page.getByText('Talaia ridge walk')).toBeVisible()
})

test('a track with an unverified date is not in the month it claims', async ({ page }) => {
  await page.goto('/tracks?year=2025&month=10')

  await expect(page.getByText('Talaia ridge walk')).toBeVisible()
  await expect(page.getByText('Planned loop with a synthetic clock')).toHaveCount(0)
})
