import { expect, test, type Locator, type Page } from '@playwright/test'

/**
 * Arranging the track report, in a real browser: a drag is a pointer, a resize
 * changes what a map and a chart draw into, and a saved arrangement is the
 * archive's -- it survives a refresh and the list draws it too.
 *
 * Every scenario leaves the archive's arrangement as it found it. The other
 * suites read the default report, and a layout saved here must not become the
 * reason one of them fails.
 */

test.afterEach(async ({ request }) => {
  await request.delete('/api/v1/layouts/track-detail')
})

async function openTrack(page: Page, title: string) {
  await page.goto('/tracks?kind=all')
  await page.getByRole('link', { name: title }).click()
  await expect(page.getByTestId('track-title')).toHaveText(title)
  await expect(page.getByTestId('customize-layout')).toBeEnabled()
}

async function box(locator: Locator) {
  const found = await locator.boundingBox()
  expect(found).not.toBeNull()
  if (!found) throw new Error('not on screen')
  return found
}

async function drag(page: Page, from: Locator, to: { x: number; y: number }) {
  const start = await box(from)
  await page.mouse.move(start.x + 12, start.y + start.height / 2)
  await page.mouse.down()
  await page.mouse.move(to.x, to.y, { steps: 12 })
  await page.mouse.up()
}

test('a widget dragged onto another trades places with it, and stays there', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  await page.getByTestId('customize-layout').click()

  const target = await box(page.getByTestId('move-profile'))
  await drag(page, page.getByTestId('move-map'), {
    x: target.x + 12,
    y: target.y + target.height / 2,
  })
  await page.getByTestId('save-layout').click()
  await expect(page.getByTestId('layout-toolbar')).toHaveCount(0)

  await page.reload()
  await expect(page.getByTestId('track-map')).toBeVisible()
  const map = await box(page.getByTestId('tile-map'))
  const profile = await box(page.getByTestId('tile-profile'))
  expect(map.x).toBeGreaterThan(profile.x)
  expect(Math.abs(map.y - profile.y)).toBeLessThan(2)
})

test('a chart made taller by its corner draws into the room it was given', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  const chart = page.getByTestId('tile-profile').locator('canvas').first()
  await expect(chart).toBeVisible()
  const before = await box(chart)
  const tileBefore = await box(page.getByTestId('tile-profile'))
  await page.getByTestId('customize-layout').click()

  // A corner below the fold is out of the pointer's reach, as it is for anybody.
  const grip = page.getByTestId('tile-profile').locator('.tile__resize')
  await grip.scrollIntoViewIfNeeded()
  const corner = await box(grip)
  await page.mouse.move(corner.x + corner.width / 2, corner.y + corner.height / 2)
  await page.mouse.down()
  await page.mouse.move(corner.x + corner.width / 2, corner.y + corner.height / 2 + 200, {
    steps: 10,
  })
  await page.mouse.up()
  await page.getByTestId('save-layout').click()

  expect((await box(page.getByTestId('tile-profile'))).height).toBeGreaterThan(
    tileBefore.height + 150,
  )
  await expect
    .poll(async () => (await box(chart)).height)
    .toBeGreaterThan(before.height + 150)
})

test('the keyboard moves a widget and says where it went', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  await page.getByTestId('customize-layout').click()

  await page.getByTestId('move-metric.distance').focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByTestId('layout-announcement')).toHaveText(
    'Distance moved to column 3, row 1.',
  )
  await page.getByTestId('save-layout').click()

  await page.reload()
  await expect(page.getByTestId('detail-distance')).toBeVisible()
  const distance = await box(page.getByTestId('tile-metric.distance'))
  const gain = await box(page.getByTestId('tile-metric.elevation-gain'))
  expect(distance.x).toBeGreaterThan(gain.x)
})

test('a hidden widget stays hidden and can be brought back', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  await page.getByTestId('customize-layout').click()
  await page.getByTestId('menu-source').click()
  await page.getByTestId('hide-source').click()
  await page.getByTestId('save-layout').click()

  await page.reload()
  await expect(page.getByTestId('track-map')).toBeVisible()
  await expect(page.getByTestId('calendar-basis')).toHaveCount(0)

  await page.getByTestId('customize-layout').click()
  await page.getByTestId('add-widget').click()
  await page.getByRole('button', { name: 'Add Where this track came from' }).click()
  await page.getByTestId('save-layout').click()
  await expect(page.getByTestId('calendar-basis')).toBeVisible()
})

test('the menu of a small card is reachable, not buried under the next widget', async ({
  page,
}) => {
  await openTrack(page, 'Talaia ridge walk')
  await page.getByTestId('customize-layout').click()

  await page.getByTestId('menu-metric.distance').click()
  await page.getByTestId('hide-metric.distance').click()

  await expect(page.getByTestId('tile-metric.distance')).toHaveCount(0)
  await page.getByTestId('save-layout').click()
  await page.reload()
  await expect(page.getByTestId('track-map')).toBeVisible()
  await expect(page.getByTestId('detail-distance')).toHaveCount(0)
})

test('a narrow screen gets two columns: small cards in pairs, every panel full width', async ({
  page,
}) => {
  await page.setViewportSize({ width: 420, height: 900 })
  await openTrack(page, 'Talaia ridge walk')

  await expect(page.getByTestId('widget-board')).toHaveAttribute('data-width-class', 'narrow')
  const board = await box(page.getByTestId('widget-board'))
  const map = await box(page.getByTestId('tile-map'))
  const distance = await box(page.getByTestId('tile-metric.distance'))
  const gain = await box(page.getByTestId('tile-metric.elevation-gain'))
  expect(map.width).toBeGreaterThan(board.width - 2)
  expect(Math.abs(distance.y - gain.y)).toBeLessThan(2)
  expect(gain.x).toBeGreaterThan(distance.x)
})

test('the list draws the same arrangement and offers no editing', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  await page.getByTestId('customize-layout').click()
  const target = await box(page.getByTestId('move-profile'))
  await drag(page, page.getByTestId('move-map'), {
    x: target.x + 12,
    y: target.y + target.height / 2,
  })
  await page.getByTestId('save-layout').click()
  await expect(page.getByTestId('layout-toolbar')).toHaveCount(0)

  await page.goto('/tracks')
  await page
    .locator('.track-row', { hasText: 'Talaia ridge walk' })
    .getByRole('button', { name: /Show details for/ })
    .click()
  await expect(page.getByTestId('track-map')).toBeVisible()

  const map = await box(page.getByTestId('tile-map'))
  const profile = await box(page.getByTestId('tile-profile'))
  expect(map.x).toBeGreaterThan(profile.x)
  await expect(page.getByTestId('customize-layout')).toHaveCount(0)
})

test('leaving with unsaved changes asks first', async ({ page }) => {
  await openTrack(page, 'Talaia ridge walk')
  await page.getByTestId('customize-layout').click()
  await page.getByTestId('move-metric.distance').focus()
  await page.keyboard.press('ArrowRight')

  page.once('dialog', (dialog) => {
    void dialog.dismiss()
  })
  await page.getByRole('link', { name: '← All tracks' }).click()

  await expect(page.getByTestId('layout-toolbar')).toBeVisible()
  await expect(page).toHaveURL(/\/tracks\/\d+/)
})
