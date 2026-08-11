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

test('a filtered view is a link somebody can send', async ({ page }) => {
  await page.goto('/tracks?activity=cycling')

  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
  await page.reload()
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
})
