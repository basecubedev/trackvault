import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Track } from '../../api/client'
import {
  analysis,
  coverage,
  failing,
  mapSource,
  on,
  profile,
  stubArchive,
  track,
  type Route as ApiRoute,
} from '../../test-fixtures'
import { TrackDetail } from './TrackDetail'

// A canvas and a WebGL context are neither available in jsdom nor interesting
// here. What each of them *decides* is tested as plain functions beside them.
vi.mock('../../charts/LazyChart', () => ({
  LazyChart: ({ label }: { label: string }) => <div data-testid="chart">{label}</div>,
}))
vi.mock('../../map/TrackMap', () => ({
  TrackMap: ({ collection }: { collection: { features: unknown[] } }) => (
    <div data-testid="track-map">{collection.features.length} segments</div>
  ),
}))

function show(routes: ApiRoute[]) {
  const stub = stubArchive(routes)
  render(
    <MemoryRouter initialEntries={['/tracks/7']}>
      <Routes>
        <Route path="/tracks/:trackId" element={<TrackDetail />} />
        <Route path="/tracks" element={<p>the list</p>} />
      </Routes>
    </MemoryRouter>,
  )
  return stub
}

function archive(current: Track = track(), ...extra: ApiRoute[]) {
  return [
    ...extra,
    on('/profile', profile()),
    on('/analysis', analysis()),
    on('/api/v1/tracks/7', current),
  ]
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the track detail page', () => {
  it('leads with what the track is, then its numbers', async () => {
    show(archive())

    await waitFor(() => {
      expect(screen.getByTestId('track-title').textContent).toBe('Talaia')
    })
    const headings = screen.getAllByRole('heading').map((node) => node.textContent)
    expect(headings.slice(0, 3)).toEqual([
      'Talaia',
      'Where it went',
      'Elevation and speed',
    ])
    expect(screen.getByTestId('detail-distance').textContent).toContain('9.27 km')
  })

  it('explains the evidence instead of printing its codes', async () => {
    show(archive())

    await waitFor(() => {
      expect(screen.getByTestId('evidence-list')).toBeInTheDocument()
    })
    const evidence = screen.getByTestId('evidence-list').textContent
    expect(evidence).toContain('A receiver reported how well it was measuring')
    expect(evidence).not.toContain('gps_accuracy_present')
    expect(evidence).not.toContain('namespace')
  })

  it('explains a quality flag rather than showing its enum name', async () => {
    show(archive(track(), on('/analysis', analysis({ quality: ['large_unobserved_gaps'] }))))

    await waitFor(() => {
      expect(screen.getByTestId('quality-list')).toBeInTheDocument()
    })
    expect(screen.getByTestId('quality-list').textContent).toContain(
      'The recording stopped for longer than its own sampling explains',
    )
    expect(screen.getByTestId('quality-list').textContent).not.toContain('large_unobserved_gaps')
  })

  it('breaks a recording’s elapsed time into what it consisted of', async () => {
    show(archive())

    await waitFor(() => {
      expect(screen.getByTestId('time-breakdown')).toBeInTheDocument()
    })
    const breakdown = screen.getByTestId('time-breakdown').textContent
    expect(breakdown).toContain('Not recorded')
    expect(breakdown).toContain('Undecided')
  })

  it('shows no headline figure while the analysis is outdated, and still draws the shape', async () => {
    const stale = track({
      analysis: { status: 'outdated', distance_m: null, elevation_gain_m: null, elapsed_duration_s: null, moving_duration_s: null },
    })
    show(archive(stale))

    await waitFor(() => {
      expect(screen.getByTestId('analysis-notice')).toBeInTheDocument()
    })
    expect(screen.getByTestId('detail-distance').textContent).toContain('—')
    expect(screen.getByTestId('track-map')).toBeInTheDocument()
    expect(screen.getByText('gpx-view analyze --outdated')).toBeInTheDocument()
  })

  it('keeps the geometry usable when a stored analysis is damaged', async () => {
    const damaged = track({
      analysis: { status: 'invalid', distance_m: null, elevation_gain_m: null, elapsed_duration_s: null, moving_duration_s: null },
    })
    show(archive(damaged))

    await waitFor(() => {
      expect(screen.getByTestId('analysis-notice')).toBeInTheDocument()
    })
    expect(screen.getByTestId('track-map')).toBeInTheDocument()
    expect(screen.getByTestId('chart')).toBeInTheDocument()
  })

  it('says a route’s durations are not somebody’s afternoon', async () => {
    const route = track({
      classification: { ...track().classification, effective_kind: 'planned', detected_kind: 'planned' },
      timeline: {
        started_at: '2025-10-20T07:00:00Z',
        ended_at: '2025-10-20T09:00:00Z',
        basis: 'unknown',
        is_actual_calendar_time: false,
        is_actual_activity_timing: false,
      },
    })
    show(archive(route))

    await waitFor(() => {
      expect(screen.getByTestId('timing-caveat')).toBeInTheDocument()
    })
    expect(screen.getByTestId('timing-caveat').textContent).toContain('not verified as time')
    expect(screen.getByTestId('calendar-basis').textContent).toContain('Not placed')
  })

  it('answers a missing track with a state and a way back', async () => {
    show([on('/profile', profile()), on('/analysis', analysis())])

    await waitFor(() => {
      expect(screen.getByTestId('track-unavailable')).toBeInTheDocument()
    })
    expect(screen.getByRole('heading', { name: 'No such track' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /all tracks/i })).toBeInTheDocument()
  })

  it('offers a retry when the archive could not be reached at all', async () => {
    show([failing('/api/v1/tracks/7')])

    await waitFor(() => {
      expect(screen.getByTestId('track-unavailable')).toBeInTheDocument()
    })
    expect(screen.getByTestId('retry')).toBeInTheDocument()
  })

  it('corrects the kind without a confirmation dialog and refetches what it moves', async () => {
    const corrected = track({
      classification: { ...track().classification, effective_kind: 'planned', override: 'planned', is_overridden: true },
    })
    const stub = show(archive(track(), on('/classification', corrected)))

    await waitFor(() => {
      expect(screen.getByTestId('set-planned')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('set-planned'))

    await waitFor(() => {
      expect(screen.getByTestId('effective-kind').textContent).toContain('Planned')
    })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(stub.writes.some((write) => write.method === 'PUT')).toBe(true)
  })
})

describe('renaming a track', () => {
  it('saves a title and keeps the source title visible beside it', async () => {
    const renamed = track({
      title: 'Sunday around the lake',
      metadata: {
        title: 'Sunday around the lake',
        note: null,
        source_title: 'Talaia',
        is_overridden: true,
      },
    })
    const stub = show(archive(track(), on('/metadata', renamed)))

    await waitFor(() => {
      expect(screen.getByTestId('edit-title')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('edit-title'))
    await userEvent.type(screen.getByTestId('title-input'), 'Sunday around the lake')
    await userEvent.click(screen.getByTestId('save-title'))

    await waitFor(() => {
      expect(screen.getByTestId('track-title').textContent).toBe('Sunday around the lake')
    })
    const write = stub.writes.find((entry) => entry.url.includes('/metadata'))
    expect(write?.method).toBe('PATCH')
    expect(write?.body).toEqual({ title: 'Sunday around the lake', note: null })
    expect(screen.getByText('Talaia')).toBeInTheDocument()
  })

  it('resets to the title the file gave', async () => {
    const named = track({
      title: 'Mine',
      metadata: { title: 'Mine', note: null, source_title: 'Talaia', is_overridden: true },
    })
    const stub = show(archive(named, on('/metadata', track())))

    await waitFor(() => {
      expect(screen.getByTestId('track-title').textContent).toBe('Mine')
    })
    await userEvent.click(screen.getByTestId('edit-title'))
    await userEvent.click(screen.getByTestId('reset-title'))

    await waitFor(() => {
      expect(screen.getByTestId('track-title').textContent).toBe('Talaia')
    })
    expect(stub.writes.find((entry) => entry.url.includes('/metadata'))?.body).toEqual({
      title: null,
      note: null,
    })
  })

  it('describes a track the file never named, rather than calling it Untitled', async () => {
    const nameless = track({
      title: null,
      metadata: { title: null, note: null, source_title: null, is_overridden: false },
    })
    show(archive(nameless))

    await waitFor(() => {
      expect(screen.getByTestId('track-title')).toBeInTheDocument()
    })
    expect(screen.getByTestId('track-title').textContent).toBe('Walking, 20 Oct 2025')
    expect(screen.getByTestId('fallback-title-note')).toBeInTheDocument()
  })
})


describe('the basemap behind a track', () => {
  it('asks the archive which installed map covers the track, and nobody else', async () => {
    const stub = show(archive(track(), on('/api/v1/maps/coverage', coverage())))

    await screen.findByTestId('track-map')
    await waitFor(() => {
      expect(stub.requested.some((url) => url.includes('/maps/coverage?bbox='))).toBe(true)
    })
    for (const url of stub.requested) {
      expect(url.startsWith('/api/v1/')).toBe(true)
    }
  })

  it('shows the attribution the covering package requires', async () => {
    show(
      archive(
        track(),
        on('/api/v1/maps/coverage', coverage({ sources: [mapSource()], any_installed: true })),
      ),
    )

    expect(await screen.findByTestId('map-attribution')).toHaveTextContent(
      'Map data © OpenStreetMap contributors',
    )
    expect(screen.getByTestId('map-attribution')).toHaveTextContent('Geofabrik GmbH')
  })

  it('offers a way to install one when nothing covers the area', async () => {
    show(
      archive(track(), on('/api/v1/maps/coverage', coverage({ any_installed: false }))),
    )

    const notice = await screen.findByTestId('no-offline-map')
    expect(notice).toHaveTextContent(/no offline map is installed for this area/i)
    expect(screen.getByRole('link', { name: /manage offline maps/i })).toHaveAttribute(
      'href',
      '/maps',
    )
  })

  it('lets a reader turn the basemap off entirely', async () => {
    show(
      archive(
        track(),
        on('/api/v1/maps/coverage', coverage({ sources: [mapSource()], any_installed: true })),
      ),
    )
    await screen.findByTestId('map-theme')

    await userEvent.selectOptions(screen.getByTestId('map-theme'), 'none')

    expect(screen.getByTestId('map-theme')).toHaveValue('none')
    expect(screen.getByTestId('track-map')).toBeInTheDocument()
  })

  it('keeps the track and the profile when coverage cannot be read', async () => {
    show(archive(track(), failing('/api/v1/maps/coverage')))

    expect(await screen.findByTestId('track-map')).toBeInTheDocument()
    expect(screen.getByTestId('detail-distance')).toBeInTheDocument()
    expect(screen.queryByTestId('map-attribution')).not.toBeInTheDocument()
  })
})
