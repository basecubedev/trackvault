import { expect, type Page, test } from '@playwright/test'

/**
 * A drawn minimap outliving the page that drew it.
 *
 * What is asserted is the *saving*, not the picture: the browser suite is the
 * only place a real MapLibre render happens, so it is the only place that can
 * show one not happening. The signal is the tiles the archive is asked for. A
 * rendering builds a map and a map fetches tiles; a reuse builds nothing, so
 * the page asks for none. That is a fact about requests rather than about the
 * timing of a WebGL event, which is what makes it worth asserting.
 *
 * The unit suite covers the key and the store, and the component suite covers
 * "the renderer was not called". This covers the thing neither can: that it
 * survives a reload, and that it is the browser's own storage it survives in.
 */

const CACHE_DATABASE = 'trackvault-minimap'

interface KeptRender {
  key: string
  media_type: string
  size: number
}

/**
 * Count the tiles the page asks the archive for.
 *
 * Counted at the request level rather than read out of the page's own resource
 * timing, because MapLibre fetches tiles from a worker and a document's timing
 * entries do not include what its workers asked for.
 */
function watchTiles(page: Page) {
  let asked = 0
  page.on('request', (request) => {
    if (request.url().includes('/api/v1/maps/tiles/')) asked += 1
  })
  return {
    tiles: () => asked,
    forget: () => {
      asked = 0
    },
  }
}

/** Every rendering the browser is keeping, without its bytes. */
function keptRenders(page: Page): Promise<KeptRender[]> {
  return page.evaluate(
    (database) =>
      new Promise<KeptRender[]>((resolve) => {
        const opening = indexedDB.open(database)
        opening.onsuccess = () => {
          const db = opening.result
          try {
            const rows = db.transaction('renders', 'readonly').objectStore('renders').getAll()
            rows.onsuccess = () => {
              const kept = rows.result as KeptRender[]
              resolve(kept.map(({ key, media_type, size }) => ({ key, media_type, size })))
              db.close()
            }
            rows.onerror = () => {
              resolve([])
            }
          } catch {
            resolve([])
          }
        }
        opening.onerror = () => {
          resolve([])
        }
      }),
    CACHE_DATABASE,
  )
}

/** Wait until every row on the page is showing its map. */
async function everyPreviewIsDrawn(page: Page): Promise<number> {
  const previews = page.getByTestId('track-minimap')
  await expect(previews.first()).toHaveAttribute('data-state', 'drawn')
  const rows = await previews.count()
  for (let row = 0; row < rows; row += 1) {
    await expect(previews.nth(row)).toHaveAttribute('data-state', 'drawn')
  }
  return rows
}

test('a reload shows the maps again without drawing one', async ({ page }) => {
  const watched = watchTiles(page)
  await page.goto('/tracks')
  const rows = await everyPreviewIsDrawn(page)
  expect(watched.tiles()).toBeGreaterThan(0)
  expect(await keptRenders(page)).toHaveLength(rows)

  await page.reload()
  watched.forget()

  await everyPreviewIsDrawn(page)
  // Nothing was built, so nothing asked the archive for a tile. This is the
  // whole point of the feature, stated as the one thing a browser can see.
  expect(watched.tiles()).toBe(0)
})

test('a new tab of the same archive reuses them too', async ({ page, context }) => {
  await page.goto('/tracks')
  await everyPreviewIsDrawn(page)

  // What survives a browser being closed and opened: storage belonging to the
  // origin rather than to the document that filled it.
  const reopened = await context.newPage()
  const watched = watchTiles(reopened)
  await reopened.goto('/tracks')

  await everyPreviewIsDrawn(reopened)
  expect(watched.tiles()).toBe(0)
  await reopened.close()
})

test('a reused map is credited from the coverage the archive reports now', async ({ page }) => {
  await page.goto('/tracks')
  await everyPreviewIsDrawn(page)

  await page.reload()

  await everyPreviewIsDrawn(page)
  // Attribution is a fact about the archive, not something read back out of a
  // picture. A page showing kept renderings credits what is installed today.
  await expect(page.getByTestId('list-map-attribution')).toContainText('OpenStreetMap')
})

test('clearing browser storage costs a redraw and nothing else', async ({ page }) => {
  const watched = watchTiles(page)
  await page.goto('/tracks')
  await everyPreviewIsDrawn(page)
  await page.reload()
  watched.forget()
  await everyPreviewIsDrawn(page)
  expect(watched.tiles()).toBe(0)

  await page.evaluate(
    (database) =>
      new Promise<void>((resolve) => {
        const deleting = indexedDB.deleteDatabase(database)
        deleting.onsuccess = () => {
          resolve()
        }
        deleting.onerror = () => {
          resolve()
        }
        deleting.onblocked = () => {
          resolve()
        }
      }),
    CACHE_DATABASE,
  )
  await page.reload()
  watched.forget()

  const rows = await everyPreviewIsDrawn(page)
  expect(watched.tiles()).toBeGreaterThan(0)
  expect(await keptRenders(page)).toHaveLength(rows)
})

test('what is kept is pictures of maps and nothing else', async ({ page }) => {
  await page.goto('/tracks')
  const rows = await everyPreviewIsDrawn(page)

  const kept = await keptRenders(page)

  expect(kept).toHaveLength(rows)
  for (const render of kept) {
    // A derived picture, its size, and a key that says what it is a picture
    // of. No track data, no API response, nothing personal beyond the drawing
    // itself -- which is why this is browser-local and never a server artifact.
    expect(render.media_type).toBe('image/png')
    expect(render.size).toBeGreaterThan(0)
    expect(render.key).toContain('trackvault-minimap:v1')
  }
  // One database, holding one store. The archive's own data has an authority
  // and this is not a second copy of it.
  const databases = await page.evaluate(() => indexedDB.databases())
  expect(databases.map((entry) => entry.name)).toEqual([CACHE_DATABASE])
})
