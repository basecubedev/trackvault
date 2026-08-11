import { expect, test } from '@playwright/test'

/**
 * The whole journey, once, in the order somebody actually takes it.
 *
 * Every step here is covered in isolation somewhere else. What this adds is
 * that the steps *connect*: a month opens the tracks that month counted, a row
 * opens the track it names, a correction on that page moves the totals on the
 * one before it, and going back does not lose where the reader was.
 */

test('dashboard, month, list, track, correction, and back again', async ({ page }) => {
  await page.goto('/')
  await expect(page.locator('#year')).toHaveValue('2025')

  // The month a bar counted, opened from the table beside the chart.
  await page.getByTestId('open-month-10').click()
  await expect(page).toHaveURL(/\/tracks\?year=2025&month=10&kind=recorded/)
  await expect(page.getByTestId('result-count')).toContainText('1 track')

  // The track that month held.
  await page.getByRole('link', { name: 'Talaia ridge walk' }).click()
  await expect(page.getByTestId('track-title')).toHaveText('Talaia ridge walk')
  await expect(page.getByTestId('detail-distance')).not.toContainText('—')

  // The shape, and the coupling between the two views of it.
  const chart = page.getByTestId('chart')
  await chart.scrollIntoViewIfNeeded()
  const box = await chart.boundingBox()
  expect(box).not.toBeNull()
  if (box) {
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5, { steps: 4 })
    await expect(page.getByTestId('profile-marker')).toBeVisible()
  }

  // A correction, and its effect on the totals one page back.
  await page.getByTestId('set-planned').click()
  await expect(page.getByTestId('effective-kind')).toContainText('Planned')

  await page.goto('/?year=2025')
  await expect(page.getByTestId('metric-tracks')).toContainText('1')

  // And back to where it was, undone.
  await page.goto('/tracks?year=2025&month=10&kind=planned')
  await page.getByRole('link', { name: 'Talaia ridge walk' }).click()
  await page.getByTestId('reset-override').click()
  await expect(page.getByTestId('effective-kind')).toHaveText('Recorded')
})

test('the browser back button walks back through the filters', async ({ page }) => {
  await page.goto('/tracks')
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')

  await page.locator('#activity').selectOption('cycling')
  await expect(page.getByTestId('result-count')).toContainText('1 track')

  await page.goBack()

  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
  await expect(page.locator('#activity')).toHaveValue('')
})

test('resetting the filters clears every one of them', async ({ page }) => {
  await page.goto('/tracks?kind=recorded&activity=cycling&year=2025')
  await expect(page.getByTestId('result-count')).toContainText('1 track')

  await page.getByTestId('reset-filters').click()

  // Back to the view somebody gets by opening the page, which is recordings.
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')
  await expect(page.getByTestId('reset-filters')).toBeDisabled()
})

test('the archive is navigable with the keyboard alone', async ({ page }) => {
  await page.goto('/tracks')
  await expect(page.getByTestId('result-count')).toContainText('2 tracks')

  // Tab until a track link has focus, then open it with Enter. Bounded, so a
  // regression that removes focusability fails rather than hangs.
  for (let step = 0; step < 30; step += 1) {
    await page.keyboard.press('Tab')
    const opened = await page.evaluate(
      () => document.activeElement?.getAttribute('href')?.startsWith('/tracks/') ?? false,
    )
    if (opened) break
  }
  await page.keyboard.press('Enter')

  await expect(page.getByTestId('track-title')).toBeVisible()
})
