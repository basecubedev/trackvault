import { expect, test } from '@playwright/test'
import { IMPORT_ARCHIVE } from '../playwright.config'

/**
 * Offering a file to the archive from the browser.
 *
 * The endpoint behind this is the first one that lets a caller make the server
 * write, so the browser half is worth proving with a real file and a real
 * import rather than a stub: what the reader is told afterwards is the part
 * that matters, and three of the four things that can happen are not "done".
 *
 * **Its own deployment.** These are the only tests in the suite that add
 * tracks. Sharing an archive with the rest would make every count in it depend
 * on which file ran first, and a test that depends on execution order fails
 * later, for the wrong reason, in somebody else's change.
 */

function document_(name: string, month: number): string {
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="e2e" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>${name}</name><type>walking</type><trkseg>
    <trkpt lat="39.70000000" lon="3.10000000"><ele>10</ele>
      <time>2025-0${month}-04T09:00:00Z</time><hdop>1.1</hdop></trkpt>
    <trkpt lat="39.70100000" lon="3.10100000"><ele>16</ele>
      <time>2025-0${month}-04T09:01:00Z</time><hdop>1.1</hdop></trkpt>
    <trkpt lat="39.70200000" lon="3.10200000"><ele>22</ele>
      <time>2025-0${month}-04T09:02:00Z</time><hdop>1.1</hdop></trkpt>
  </trkseg></trk>
</gpx>
`
}

function file(name: string, content: string) {
  return { name, mimeType: 'application/gpx+xml', buffer: Buffer.from(content) }
}

test('a file offered from the browser becomes a track', async ({ page }) => {
  await page.goto(`${IMPORT_ARCHIVE}/tracks`)
  await page.getByTestId('toggle-import').click()

  await page
    .getByLabel(/choose files/i)
    .setInputFiles(file('uploaded-walk.gpx', document_('Uploaded from the browser', 7)))

  await expect(page.getByTestId('import-result')).toContainText('imported')
  // The listing catches up on its own, because something new actually arrived.
  await expect(page.getByText('Uploaded from the browser')).toBeVisible()
})

test('offering the same file again is not a second track', async ({ page }) => {
  const twice = file('twice.gpx', document_('Offered twice', 8))
  await page.goto(`${IMPORT_ARCHIVE}/tracks`)
  await page.getByTestId('toggle-import').click()

  await page.getByLabel(/choose files/i).setInputFiles(twice)
  await expect(page.getByTestId('import-result')).toContainText('imported')

  await page.getByLabel(/choose files/i).setInputFiles(twice)

  // Not a failure and not an import. The sentence has to be its own.
  await expect(page.getByTestId('import-result')).toContainText('already in the archive')
})

test('a file the archive cannot read says why, in words', async ({ page }) => {
  await page.goto(`${IMPORT_ARCHIVE}/tracks`)
  await page.getByTestId('toggle-import').click()

  await page
    .getByLabel(/choose files/i)
    .setInputFiles(file('not-a-track.gpx', 'this is not a gpx document'))

  await expect(page.getByTestId('import-result')).toContainText('not imported')
  await expect(page.getByTestId('import-result')).not.toContainText('invalid_gpx')
})

test('several files each get their own verdict', async ({ page }) => {
  await page.goto(`${IMPORT_ARCHIVE}/tracks`)
  await page.getByTestId('toggle-import').click()

  await page
    .getByLabel(/choose files/i)
    .setInputFiles([
      file('good.gpx', document_('One that reads', 9)),
      file('bad.gpx', 'not a track at all'),
    ])

  const results = page.getByTestId('import-result').locator('li')
  await expect(results).toHaveCount(2)
  await expect(results.nth(0)).toContainText('imported')
  await expect(results.nth(1)).toContainText('not imported')
})

test('a recording with sensors shows what they measured', async ({ page }) => {
  const withSensors = `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="e2e" xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v2">
  <trk><name>Ride with a chest strap</name><type>cycling</type><trkseg>
    <trkpt lat="39.80" lon="3.20"><ele>10</ele><time>2025-06-04T09:00:00Z</time><hdop>1.1</hdop>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>118</gpxtpx:hr>
      <gpxtpx:cad>76</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions></trkpt>
    <trkpt lat="39.801" lon="3.201"><ele>16</ele><time>2025-06-04T09:01:00Z</time><hdop>1.1</hdop>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>134</gpxtpx:hr>
      <gpxtpx:cad>81</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions></trkpt>
    <trkpt lat="39.802" lon="3.202"><ele>22</ele><time>2025-06-04T09:02:00Z</time><hdop>1.1</hdop>
      <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>149</gpxtpx:hr>
      <gpxtpx:cad>84</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions></trkpt>
  </trkseg></trk>
</gpx>
`
  await page.goto(`${IMPORT_ARCHIVE}/tracks`)
  await page.getByTestId('toggle-import').click()
  await page.getByLabel(/choose files/i).setInputFiles(file('with-sensors.gpx', withSensors))
  await expect(page.getByTestId('import-result')).toContainText('imported')

  await page.getByRole('link', { name: 'Ride with a chest strap' }).click()

  // Upload, parse, store, derive, draw -- the whole chain, on real bytes.
  await expect(page.getByTestId('sensor-panel')).toBeVisible()
  await expect(page.getByRole('heading', { name: /Heart rate and cadence/ })).toBeVisible()
})

test('one ride imported in two formats is two rows that say so', async ({ page }) => {
  // Every kind: a route export states no measurement, so one of the two rows
  // is `unknown` and the browser's default would show only the other.
  const asTrack = document_('Zeeland, twice over', 3)
  const asRoute = asTrack
    .replace('<trk>', '<rte>')
    .replace('</trk>', '</rte>')
    .replace('<trkseg>', '')
    .replace('</trkseg>', '')
    .replaceAll('<trkpt', '<rtept')
    .replaceAll('</trkpt>', '</rtept>')

  await page.goto(`${IMPORT_ARCHIVE}/tracks?kind=all`)
  await page.getByTestId('toggle-import').click()
  await page.getByLabel(/choose files/i).setInputFiles([
    file('zeeland-track.gpx', asTrack),
    file('zeeland-route.gpx', asRoute),
  ])
  await expect(page.getByTestId('import-result').locator('li')).toHaveCount(2)

  // Two rows -- both files are kept, because they are different evidence --
  // and both say the other describes the same afternoon.
  const rows = page.locator('.track-row', { hasText: 'Zeeland, twice over' })
  await expect(rows).toHaveCount(2)
  await expect(rows.first().getByTestId('same-recording')).toContainText('2 imports')
})
