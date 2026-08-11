import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  availableYears,
  failing,
  on,
  stubArchive,
  track,
  trackList,
  type Route as ApiRoute,
} from '../../test-fixtures'
import { TrackBrowser } from './TrackBrowser'

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

  it('offers a retry when the listing could not be read', async () => {
    show('/tracks', [on('/statistics/years', availableYears()), failing('/api/v1/tracks')])

    await waitFor(() => {
      expect(screen.getByTestId('request-error')).toBeInTheDocument()
    })
    expect(screen.getByTestId('retry')).toBeInTheDocument()
    expect(screen.queryByTestId('empty-filter')).not.toBeInTheDocument()
  })
})
