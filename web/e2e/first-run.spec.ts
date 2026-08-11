import { expect, test } from '@playwright/test'
import { EMPTY_ARCHIVE } from '../playwright.config'

/**
 * A fresh installation, in a real browser, against a real empty archive.
 *
 * This is the first thing anybody sees and the state most likely to render as
 * `NaN km`, an empty chart or a crashed map -- none of which any amount of
 * seeded-archive testing would ever reach. So it runs against a second server
 * with nothing in it.
 *
 * The other thing it checks is what the page *offers*. There is no upload
 * endpoint, deliberately, so telling somebody to upload their first GPX would
 * send them looking for a button that does not exist.
 */

test('an empty dashboard explains itself instead of showing nothing', async ({ page }) => {
  await page.goto(`${EMPTY_ARCHIVE}/`)

  await expect(page.getByTestId('empty-archive')).toBeVisible()
  await expect(page.getByTestId('empty-archive')).toContainText('trackvault import')
  await expect(page.getByText('NaN')).toHaveCount(0)
  await expect(page.getByText('undefined')).toHaveCount(0)
})

test('an empty archive never invites an upload that does not exist', async ({ page }) => {
  await page.goto(`${EMPTY_ARCHIVE}/`)

  await expect(page.getByTestId('empty-archive')).toBeVisible()
  await expect(page.getByText(/upload/i)).toHaveCount(0)
})

test('an empty track list says the archive is empty, not that a filter matched nothing', async ({
  page,
}) => {
  await page.goto(`${EMPTY_ARCHIVE}/tracks`)

  await expect(page.getByTestId('empty-archive')).toBeVisible()
  await expect(page.getByTestId('empty-filter')).toHaveCount(0)
  await expect(page.getByTestId('pager-position')).toContainText('0 of 0')
})

test('navigation works on an archive with nothing in it', async ({ page }) => {
  await page.goto(`${EMPTY_ARCHIVE}/`)

  await page.getByRole('link', { name: 'Tracks' }).click()
  await expect(page).toHaveURL(/\/tracks$/)
  await page.getByRole('link', { name: 'About' }).click()

  await expect(page.getByTestId('about-version')).not.toBeEmpty()
  await expect(page.getByTestId('about-status')).toHaveText('Answering')
})

test('a deep link into an empty archive lands somewhere legible', async ({ page }) => {
  await page.goto(`${EMPTY_ARCHIVE}/tracks/1`)

  await expect(page.getByTestId('track-unavailable')).toBeVisible()
  await expect(page.getByRole('link', { name: /all tracks/i })).toBeVisible()
})
