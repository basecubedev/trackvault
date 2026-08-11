import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  analysis,
  availableYears,
  coverage,
  everythingIsVisible,
  failing,
  geometry,
  mapSource,
  on,
  profile,
  stubArchive,
  track,
  trackList,
  type Route as ApiRoute,
} from '../../test-fixtures'
import { TrackBrowser } from './TrackBrowser'

// The preview draws with WebGL, which jsdom does not have. Everything this
// page decides about previews -- that each row gets one, and that what they
// drew is credited once -- is decided before any of that.
vi.mock('../../map/minimap', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../map/minimap')>()),
  renderTrackMinimap: vi.fn(() => Promise.resolve('data:image/png;base64,iVBORw0KGgo=')),
}))

// The same two for the report a row opens: a canvas and a WebGL context are
// neither available here nor what these tests are about.
vi.mock('../../charts/LazyChart', () => ({
  LazyChart: ({ label }: { label: string }) => <div data-testid="chart">{label}</div>,
}))
vi.mock('../../map/TrackMap', () => ({
  TrackMap: ({ collection }: { collection: { features: unknown[] } }) => (
    <div data-testid="track-map">{collection.features.length} segments</div>
  ),
}))

/**
 * The track browser, which is mostly about states nobody enjoys reaching.
 *
 * The URL is the filter state, so a filtered view is a link and a refresh keeps
 * it. Everything else here is the difference between "there is nothing" and
 * "there is nothing *like that*" -- two answers that need opposite reactions.
 */

function show(entry: string, routes: ApiRoute[]) {
  const stub = stubArchive(routes)
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Address />
      <Routes>
        <Route path="/tracks" element={<TrackBrowser />} />
        <Route path="/tracks/:trackId" element={<Location />} />
      </Routes>
    </MemoryRouter>,
  )
  return stub
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

/** The address as it stands, for the parts of the view that live in it. */
function Address() {
  const location = useLocation()
  return <output data-testid="address">{`${location.pathname}${location.search}`}</output>
}

/**
 * Everything the report a row opens reads, plus the listing behind it.
 *
 * The listing is `/api/v1/tracks?…` and one track is `/api/v1/tracks/7`, so the
 * two are matched on fragments that cannot be confused for each other.
 */
function archiveWith(listing: ApiRoute): ApiRoute[] {
  return [
    on('/profile', profile()),
    on('/analysis', analysis()),
    on('/geometry', geometry()),
    on('/maps/coverage', coverage()),
    on('/statistics/years', availableYears()),
    on('/api/v1/tracks/', track()),
    listing,
  ]
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the track browser', () => {
  it('sends every filter in the URL to the archive', async () => {
    const stub = show('/tracks?kind=recorded&activity=walking&year=2025&month=10&offset=25', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks', trackList([track()], { total: 40, offset: 25 })),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-7')).toBeInTheDocument()
    })
    const listed = stub.requested.find((url) => url.includes('/api/v1/tracks?'))
    expect(listed).toContain('kind=recorded')
    expect(listed).toContain('activity=walking')
    expect(listed).toContain('year=2025')
    expect(listed).toContain('month=10')
    expect(listed).toContain('offset=25')
  })

  it('offers only the years the archive actually holds', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears({ years: [2021, 2019] })),
      on('/api/v1/tracks', trackList([])),
    ])

    await waitFor(() => {
      expect(screen.getByLabelText('Year')).toBeInTheDocument()
    })
    const options = [...screen.getByLabelText('Year').querySelectorAll('option')].map(
      (node) => node.textContent,
    )
    expect(options).toEqual(['Any year', '2021', '2019'])
  })

  it('resets every filter in one action', async () => {
    show('/tracks?kind=planned&activity=cycling&year=2021', [
      on('/statistics/years', availableYears({ years: [2021] })),
      on('/api/v1/tracks', trackList([])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('reset-filters')).toBeEnabled()
    })
    await userEvent.click(screen.getByTestId('reset-filters'))

    await waitFor(() => {
      expect(screen.getByTestId('reset-filters')).toBeDisabled()
    })
    expect(screen.getByLabelText<HTMLSelectElement>('Kind').value).toBe('')
    expect(screen.getByLabelText<HTMLSelectElement>('Activity').value).toBe('')
  })

  it('tells an empty archive apart from an empty filter', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears({ years: [], archive_track_count: 0 })),
      on('/api/v1/tracks', trackList([])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('empty-archive')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('empty-filter')).not.toBeInTheDocument()
  })

  it('says a filter matched nothing when the archive is not empty', async () => {
    show('/tracks?activity=running', [
      on('/statistics/years', availableYears({ archive_track_count: 12 })),
      on('/api/v1/tracks', trackList([])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('empty-filter')).toBeInTheDocument()
    })
    expect(screen.getByTestId('empty-filter').textContent).toContain('The archive holds tracks')
  })

  it('never shows a stale metric as the track’s own', async () => {
    const stale = track({
      analysis: {
        status: 'outdated',
        distance_m: null,
        elevation_gain_m: null,
        elapsed_duration_s: null,
        moving_duration_s: null,
      },
    })
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks', trackList([stale])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('row-distance')).toBeInTheDocument()
    })
    expect(screen.getByTestId('row-distance').textContent).toBe('—')
    expect(screen.getByTestId('track-7').textContent).toContain('Needs re-analysis')
  })

  it('describes a track its source never named', async () => {
    const nameless = track({
      title: null,
      metadata: { title: null, note: null, source_title: null, is_overridden: false },
    })
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks', trackList([nameless])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-7')).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: 'Walking, 20 Oct 2025' })).toBeInTheDocument()
    expect(screen.queryByText('Untitled')).not.toBeInTheDocument()
  })

  it('reports the page it is on and bounds the pager', async () => {
    show('/tracks?offset=25', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks', trackList([track()], { total: 40, offset: 25 })),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('pager-position').textContent).toBe('26–40 of 40')
    })
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled()
  })

  it('gives every row a map of where the track went', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks', trackList([track(), track({ id: 8, title: 'Coastal cycle' })])),
    ])

    await waitFor(() => {
      expect(screen.getAllByTestId('track-minimap')).toHaveLength(2)
    })
  })

  it('credits the map data its previews drew, once for the page', async () => {
    everythingIsVisible()
    show('/tracks', [
      // Before the listing route: a track's geometry lives under its own
      // address, and the routes here answer the first fragment that matches.
      on('/geometry', geometry()),
      on('/maps/coverage', coverage({ sources: [mapSource()], any_installed: true })),
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks', trackList([track(), track({ id: 8, title: 'Coastal cycle' })])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('list-map-attribution')).toBeInTheDocument()
    })
    // Two rows, one package, one line. A credit repeated per row reads as a
    // rendering fault rather than as thoroughness.
    expect(screen.getByTestId('list-map-attribution').textContent).toBe(
      'Map data © OpenStreetMap contributors',
    )
  })

  it('says nothing about map data when no preview drew any', async () => {
    everythingIsVisible()
    show('/tracks', [
      on('/geometry', geometry()),
      on('/maps/coverage', coverage()),
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks', trackList([track()])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-minimap')).toHaveAttribute('data-state', 'drawn')
    })
    expect(screen.queryByTestId('list-map-attribution')).not.toBeInTheDocument()
  })

  it('offers a retry when the listing could not be read', async () => {
    show('/tracks', [on('/statistics/years', availableYears()), failing('/api/v1/tracks')])

    await waitFor(() => {
      expect(screen.getByTestId('request-error')).toBeInTheDocument()
    })
    expect(screen.getByTestId('retry')).toBeInTheDocument()
    expect(screen.queryByTestId('empty-filter')).not.toBeInTheDocument()
  })
})

/**
 * Reading a track without leaving the list.
 *
 * Finding the right track means looking at several, and a page load per look --
 * with a browser back button between them -- is what this replaced. What a row
 * opens is the track's own report, the same component its page renders, so the
 * two can never come to say different things.
 *
 * One at a time, deliberately: an open report holds a map, and a map holds a
 * WebGL context. Twenty-five of those is the defect the previews already avoid.
 */
describe('a track opened inside the list', () => {
  it('costs nothing until somebody asks for it', async () => {
    const stub = show('/tracks', archiveWith(on('/api/v1/tracks?', trackList([track()]))))

    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toHaveAttribute('aria-expanded', 'false')
    })
    expect(stub.requested.some((url) => url.includes('/profile'))).toBe(false)
    expect(stub.requested.some((url) => url.includes('/analysis'))).toBe(false)
    expect(screen.queryByTestId('detail-distance')).not.toBeInTheDocument()
  })

  it('opens the whole report in place, not a summary of it', async () => {
    show('/tracks', archiveWith(on('/api/v1/tracks?', trackList([track()]))))

    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('expand-7'))

    await waitFor(() => {
      expect(screen.getByTestId('detail-distance')).toBeInTheDocument()
    })
    // The map, the profile, the evidence and the corrections -- the page's
    // whole answer, because that is what somebody scanning the list needs.
    expect(screen.getByTestId('track-map')).toBeInTheDocument()
    expect(screen.getByTestId('chart')).toBeInTheDocument()
    expect(screen.getByTestId('evidence-list')).toBeInTheDocument()
    expect(screen.getByTestId('set-planned')).toBeInTheDocument()
    expect(screen.getByTestId('expand-7')).toHaveAttribute('aria-expanded', 'true')
  })

  it('keeps the list a list: opening one row closes the other', async () => {
    show(
      '/tracks',
      archiveWith(
        on('/api/v1/tracks?', trackList([track(), track({ id: 8, title: 'Coastal cycle' })])),
      ),
    )

    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('expand-7'))
    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toHaveAttribute('aria-expanded', 'true')
    })

    await userEvent.click(screen.getByTestId('expand-8'))

    await waitFor(() => {
      expect(screen.getByTestId('expand-8')).toHaveAttribute('aria-expanded', 'true')
    })
    expect(screen.getByTestId('expand-7')).toHaveAttribute('aria-expanded', 'false')
  })

  it('closes again on a second press', async () => {
    show('/tracks', archiveWith(on('/api/v1/tracks?', trackList([track()]))))

    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('expand-7'))
    await waitFor(() => {
      expect(screen.getByTestId('detail-distance')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByTestId('expand-7'))

    await waitFor(() => {
      expect(screen.queryByTestId('detail-distance')).not.toBeInTheDocument()
    })
  })

  it('is in the address, so a track somebody found is a link they can send', async () => {
    show('/tracks', archiveWith(on('/api/v1/tracks?', trackList([track()]))))

    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('expand-7'))

    await waitFor(() => {
      expect(screen.getByTestId('address').textContent).toContain('open=7')
    })
  })

  it('opens straight away when the address says which track', async () => {
    show('/tracks?open=7', archiveWith(on('/api/v1/tracks?', trackList([track()]))))

    await waitFor(() => {
      expect(screen.getByTestId('detail-distance')).toBeInTheDocument()
    })
  })

  it('stops the row above it disagreeing with a correction made inside it', async () => {
    const planned = track({
      classification: { ...track().classification, effective_kind: 'planned', override: 'planned', is_overridden: true },
    })
    let answered = 0
    const listing: ApiRoute = (url) => {
      if (!url.includes('/api/v1/tracks?')) return undefined
      answered += 1
      return trackList([answered === 1 ? track() : planned])
    }
    show('/tracks', [on('/classification', planned), ...archiveWith(listing)])

    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toBeInTheDocument()
    })
    expect(screen.getByTestId('track-7').textContent).toContain('Recorded')
    await userEvent.click(screen.getByTestId('expand-7'))
    await waitFor(() => {
      expect(screen.getByTestId('set-planned')).toBeInTheDocument()
    })

    await userEvent.click(screen.getByTestId('set-planned'))

    await waitFor(() => {
      expect(screen.getByTestId('track-7').textContent).toContain('Planned')
    })
  })

  it('says what it cannot show, and leaves the rest of the list alone', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks?', trackList([track()])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('expand-7')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('expand-7'))

    await waitFor(() => {
      expect(screen.getByTestId('track-unavailable')).toBeInTheDocument()
    })
    expect(screen.getByTestId('result-count')).toBeInTheDocument()
  })

  it('closes when the selection changes, rather than staying open on a page that may not hold it', async () => {
    show('/tracks?open=7', archiveWith(on('/api/v1/tracks?', trackList([track()]))))

    await waitFor(() => {
      expect(screen.getByTestId('detail-distance')).toBeInTheDocument()
    })

    await userEvent.selectOptions(screen.getByLabelText('Activity'), 'cycling')

    await waitFor(() => {
      expect(screen.queryByTestId('detail-distance')).not.toBeInTheDocument()
    })
    expect(screen.getByTestId('address').textContent).not.toContain('open=')
  })
})

/**
 * One ride, several files.
 *
 * The archive keeps every import because each is different evidence -- one
 * format carries a heart rate another cannot. What the list owes the reader is
 * to stop presenting them as three afternoons.
 */
describe('a recording that arrived more than once', () => {
  it('says how many files describe it', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on(
        '/api/v1/tracks?',
        trackList([
          track({ id: 7, same_recording_ids: [8, 9] }),
          track({ id: 8, same_recording_ids: [7, 9] }),
          track({ id: 9, same_recording_ids: [7, 8] }),
        ]),
      ),
    ])

    await waitFor(() => {
      expect(screen.getAllByTestId('same-recording')).toHaveLength(3)
    })
    expect(screen.getAllByTestId('same-recording')[0]?.textContent).toContain('3 imports')
  })

  it('says nothing on a track nothing matched', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks?', trackList([track()])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-7')).toBeInTheDocument()
    })
    // A single import is not "one of one". It is a track.
    expect(screen.queryByTestId('same-recording')).not.toBeInTheDocument()
  })
})

/**
 * Roughly where a track was.
 *
 * The mark is not decoration. The archive compared rectangles, which is right
 * inside a country and wrong at a border, and a label that did not say so would
 * read as a fact it cannot be.
 */
describe('where a track was', () => {
  const located = {
    regions: ['Zeeland'],
    countries: [{ name: 'Netherlands', code: 'NL' }],
  }

  it('names the region and the country beside it', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks?', trackList([track({ approximate_location: located })])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-place')).toBeInTheDocument()
    })
    const text = screen.getByTestId('track-place').textContent
    expect(text).toContain('Zeeland')
    expect(text).toContain('Netherlands')
  })

  it('marks it as approximate rather than stating it', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks?', trackList([track({ approximate_location: located })])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-place')).toBeInTheDocument()
    })
    expect(screen.getByTestId('track-place').textContent).toContain('≈')
    expect(screen.getByTestId('track-place').title).toMatch(/wrong one/i)
  })

  it('says nothing when nothing could answer', async () => {
    show('/tracks', [
      on('/statistics/years', availableYears()),
      on('/api/v1/tracks?', trackList([track()])),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-7')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('track-place')).not.toBeInTheDocument()
  })
})
