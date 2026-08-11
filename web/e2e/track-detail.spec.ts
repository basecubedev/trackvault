import { expect, test } from '@playwright/test'

/**
 * One track, in a real browser: the map, the profile, the states and the
 * correction. And the thing a single-page application most often gets wrong --
 * a deep link that survives a refresh.
 */

async function openTrack(page: import('@playwright/test').Page, title: string) {
  await page.goto('/tracks')
  await page.getByRole('link', { name: title }).click()
  await expect(page.getByTestId('track-title')).toHaveText(title)
}

test('a track detail shows its map, profile, metrics and analysis state', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')

  await expect(page.getByTestId('track-map')).toBeVisible()
  await expect(page.getByTestId('chart')).toBeVisible()
  await expect(page.getByText('Distance', { exact: true })).toBeVisible()
  await expect(page.locator('.badge', { hasText: 'Current' })).toBeVisible()
  await expect(page.getByTestId('time-breakdown')).toContainText('Not recorded')
  await expect(page.getByTestId('calendar-basis')).toContainText('Dated by observed timing')
})

test('a route with a synthetic clock says its timing is unverified', async ({ page }) => {
  await openTrack(page, 'Planned loop with a synthetic clock')

  await expect(page.getByTestId('timing-caveat')).toContainText('not verified as time')
  await expect(page.getByTestId('calendar-basis')).toContainText('Not placed in a calendar period')
  await expect(page.getByText('Moving (route timeline)')).toBeVisible()
})

test('the map keeps a two-segment recording in two segments', async ({ page }) => {
  await openTrack(page, 'Coastal cycle')

  await expect(page.getByText('2 segments, drawn separately.')).toBeVisible()
})

test('hovering the profile marks the sample on the map', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  const chart = page.getByTestId('chart')
  await expect(chart).toBeVisible()
  await expect(page.getByTestId('hover-readout')).toContainText('Point at the chart')

  // The chart sits below the fold on a laptop viewport, and a mouse move to a
  // coordinate outside the viewport lands nowhere. Scrolling first is what a
  // reader does too.
  await chart.scrollIntoViewIfNeeded()
  await chart.hover()
  const box = await chart.boundingBox()
  expect(box).not.toBeNull()
  if (!box) return
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.5, { steps: 4 })

  await expect(page.getByTestId('hover-readout')).not.toContainText('Point at the chart')
  await expect(page.getByTestId('profile-marker')).toBeVisible()
})

test('a classification correction refreshes every view that depends on it', async ({ page }) => {
  await openTrack(page, 'Planned loop with a synthetic clock')
  await expect(page.getByTestId('effective-kind')).toHaveText('Unknown')

  await page.getByTestId('set-recorded').click()

  await expect(page.getByTestId('effective-kind')).toContainText('Recorded')
  await expect(page.getByTestId('effective-kind')).toContainText('your correction')
  // The correction says what the track is. It says nothing about its clock, so
  // it must not have dated the track.
  await expect(page.getByTestId('calendar-basis')).toContainText('Not placed in a calendar period')

  await page.goto('/?year=2025')
  await expect(page.getByTestId('metric-tracks')).toContainText('2')
  await expect(page.getByTestId('unplaced-note')).toContainText('nothing vouches for')

  await page.goto('/tracks')
  await page.getByRole('link', { name: 'Planned loop with a synthetic clock' }).click()
  await page.getByTestId('reset-override').click()
  await expect(page.getByTestId('effective-kind')).toHaveText('Unknown')
})

test('a deep link survives a refresh', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  const url = page.url()

  await page.goto(url)

  await expect(page.getByTestId('track-title')).toHaveText('Talaia ridge walk')
  expect(page.url()).toBe(url)
})
