#!/usr/bin/env node
/**
 * Build a small, deterministic archive for the browser tests.
 *
 * Every document is generated here. The browser suite never touches a real
 * recording: a private track in a screenshot or a trace is personal movement
 * data leaving the machine it belongs to, and no assertion is worth that.
 *
 * The set is chosen to cover the states the interface has to render honestly:
 *
 *   a measured recording        -> current metrics, a real calendar date
 *   a second, shorter recording -> something to page and sort against
 *   an unmeasured route         -> instants nothing vouches for, no calendar date
 *   an undated route            -> geometry with no clock at all
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const DEGREE = 111_195.0

function point({ north, east = 0, elevation, second, month, measured }) {
  const latitude = 39.8 + north / DEGREE
  const longitude = 3.1 + east / DEGREE
  const parts = [
    `<trkpt lat="${latitude.toFixed(8)}" lon="${longitude.toFixed(8)}">`,
    `<ele>${elevation.toFixed(1)}</ele>`,
  ]
  if (second !== null) {
    const clock = new Date(Date.UTC(2025, month - 1, 20, 7, 0, 0) + second * 1000)
    parts.push(`<time>${clock.toISOString().replace('.000', '')}</time>`)
  }
  if (measured) parts.push('<hdop>1.1</hdop>')
  parts.push('</trkpt>')
  return `      ${parts.join('')}`
}

function document_({ name, activity, count, metres, month, timed, measured, segments = 1 }) {
  const perSegment = Math.floor(count / segments)
  const step = metres / (count - 1)
  const body = []
  for (let segment = 0; segment < segments; segment += 1) {
    const rows = []
    for (let index = 0; index < perSegment; index += 1) {
      const absolute = segment * perSegment + index
      rows.push(
        point({
          north: absolute * step + segment * 4000,
          elevation: 20 + 180 * Math.sin((Math.PI * absolute) / (count - 1)),
          second: timed ? absolute * 8 + segment * 3600 : null,
          month,
          measured,
        }),
      )
    }
    body.push(`    <trkseg>\n${rows.join('\n')}\n    </trkseg>`)
  }
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="trackvault-e2e" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>${name}</name><type>${activity}</type>
${body.join('\n')}
  </trk>
</gpx>
`
}

const FIXTURES = [
  {
    file: 'measured-october.gpx',
    content: document_({
      name: 'Talaia ridge walk',
      activity: 'walking',
      count: 240,
      metres: 9200,
      month: 10,
      timed: true,
      measured: true,
    }),
  },
  {
    file: 'measured-june.gpx',
    content: document_({
      name: 'Coastal cycle',
      activity: 'cycling',
      count: 180,
      metres: 24000,
      month: 6,
      timed: true,
      measured: true,
      segments: 2,
    }),
  },
  {
    file: 'unverified-route.gpx',
    content: document_({
      name: 'Planned loop with a synthetic clock',
      activity: 'hiking',
      count: 120,
      metres: 8900,
      month: 10,
      timed: true,
      measured: false,
    }),
  },
  {
    file: 'undated-route.gpx',
    content: document_({
      name: 'Route with no clock',
      activity: 'cycling',
      count: 90,
      metres: 5100,
      month: 10,
      timed: false,
      measured: false,
    }),
  },
]

const dataDir = process.env['TRACKVAULT_DATA_DIR'] ?? mkdtempSync(join(tmpdir(), 'trackvault-e2e-'))
const fixtureDir = join(dataDir, 'fixtures')
mkdirSync(fixtureDir, { recursive: true })

const paths = FIXTURES.map(({ file, content }) => {
  const path = join(fixtureDir, file)
  writeFileSync(path, content, 'utf8')
  return path
})

execFileSync('uv', ['run', '--project', '..', 'trackvault', 'import', ...paths], {
  cwd: process.cwd(),
  env: { ...process.env, TRACKVAULT_DATA_DIR: dataDir },
  stdio: 'inherit',
})

process.stdout.write(`${dataDir}\n`)
