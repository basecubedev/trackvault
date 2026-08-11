import { expect, test } from '@playwright/test'

/**
 * Renaming a track, and the promise that makes it safe.
 *
 * The correction is the user's own data and it never edits the file: the source
 * title stays visible beside it, and clearing the field hands the display back
 * to it. A refresh is part of every one of these -- a rename that only exists
 * in a component's state is a rename somebody loses.
 */

async function open(page: import('@playwright/test').Page, title: string) {
  await page.goto('/tracks')
  await page.getByRole('link', { name: title }).click()
  await expect(page.getByTestId('track-title')).toHaveText(title)
}

test('a renamed track keeps its new title across a refresh, and can be reset', async ({ page }) => {
  await open(page, 'Route with no clock')

  await page.getByTestId('edit-title').click()
  await page.getByTestId('title-input').fill('The lane behind the church')
  await page.getByTestId('note-input').fill('The receiver lost its fix in the tunnel.')
  await page.getByTestId('save-title').click()

  await expect(page.getByTestId('track-title')).toHaveText('The lane behind the church')
  await page.reload()
  await expect(page.getByTestId('track-title')).toHaveText('The lane behind the church')
  await expect(page.getByTestId('track-note')).toContainText('lost its fix')

  // The listing reads the same authority.
  await page.goto('/tracks')
  await expect(page.getByRole('link', { name: 'The lane behind the church' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Route with no clock' })).toHaveCount(0)

  // Reset hands the display back to the file, which never stopped saying it.
  await page.getByRole('link', { name: 'The lane behind the church' }).click()
  await page.getByTestId('edit-title').click()
  await page.getByTestId('reset-title').click()

  await expect(page.getByTestId('track-title')).toHaveText('Route with no clock')
  await page.reload()
  await expect(page.getByTestId('track-title')).toHaveText('Route with no clock')
})

test('a rename never touches the title the file gave', async ({ page }) => {
  await open(page, 'Coastal cycle')

  await page.getByTestId('edit-title').click()
  await page.getByTestId('title-input').fill('Along the bay')
  await page.getByTestId('save-title').click()

  await expect(page.getByTestId('track-title')).toHaveText('Along the bay')
  await expect(page.getByText('Coastal cycle')).toBeVisible()

  await page.getByTestId('edit-title').click()
  await page.getByTestId('reset-title').click()
  await expect(page.getByTestId('track-title')).toHaveText('Coastal cycle')
})

test('the editor can be left with the keyboard and changes nothing', async ({ page }) => {
  await open(page, 'Talaia ridge walk')

  await page.getByTestId('edit-title').click()
  await expect(page.getByTestId('title-input')).toBeFocused()
  await page.getByTestId('title-input').fill('Not saved')
  await page.keyboard.press('Escape')

  await expect(page.getByTestId('title-editor')).toHaveCount(0)
  await expect(page.getByTestId('track-title')).toHaveText('Talaia ridge walk')
})
