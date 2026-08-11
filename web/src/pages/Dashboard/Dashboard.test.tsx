import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  availableYears,
  failing,
  monthly,
  on,
  stubArchive,
  totals,
  year,
  type Route as ApiRoute,
} from '../../test-fixtures'
import { Dashboard, selectedYear } from './Dashboard'

// The chart is a canvas mount and jsdom has no canvas. What the chart *decides*
// is tested as plain functions beside it; what is asserted here is the page,
// including the table that carries the same numbers without a pointer.
vi.mock('../../charts/LazyChart', () => ({
  LazyChart: ({ label }: { label: string }) => <div data-testid="chart">{label}</div>,
}))

/**
 * What the dashboard has to get right, in a browser-shaped environment.
 *
 * Four things it must never do: default to a total that mixes recorded with
 * planned, render an unavailable metric as zero, present a partial period as a
 * complete one, and open an empty year because the reader's clock says so.
 */

function show(entry = '/') {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/tracks" element={<Location />} />
      </Routes>
    </MemoryRouter>,
  )
}

function Location() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

/**
 * The yearly totals, which live one path segment above the monthly ones.
 *
 * Matched explicitly rather than by fragment: `/statistics/year/2025/monthly`
 * contains `/statistics/year/`, and a route that ignored that would answer the
 * monthly request with a yearly body -- which is a fixture bug that looks
 * exactly like a page bug.
 */
function onYear(body: ReturnType<typeof year>): ApiRoute {
  return (url) =>
    url.includes('/statistics/year/') && !url.includes('/monthly') ? body : undefined
}

function archive(...routes: ApiRoute[]) {
  return stubArchive([
    ...routes,
    on('/statistics/years', availableYears()),
    on('/monthly', monthly()),
    onYear(year()),
  ])
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the dashboard', () => {
  it('asks for recorded totals by default', async () => {
    const stub = archive()

    show()

    await waitFor(() => {
      expect(stub.requested.some((url) => url.includes('scope=recorded'))).toBe(true)
    })
    expect(screen.getByTestId('scope-explainer').textContent).toContain('actually happened')
  })

  it('opens on the newest year the archive has, not on the reader’s year', async () => {
    const stub = archive(on('/statistics/years', availableYears({ years: [2021, 2019] })))

    show()

    await waitFor(() => {
      expect(stub.requested.some((url) => url.includes('/statistics/year/2021'))).toBe(true)
    })
    expect(stub.requested.some((url) => url.includes('/statistics/year/2026'))).toBe(false)
  })

  it('renders twelve months, including the ones with nothing in them', async () => {
    archive(on('/monthly', monthly([{ track_count: 2, distance_m: 12_000 }])))

    show()

    await waitFor(() => {
      expect(screen.getAllByRole('row')).toHaveLength(13)
    })
    expect(screen.getByTestId('open-month-12')).toBeInTheDocument()
  })

  it('renders an unavailable metric as unavailable rather than as zero', async () => {
    archive(onYear(year({ track_count: 3, distance_m: null, moving_duration_s: null })))

    show()

    await waitFor(() => {
      expect(screen.getByTestId('metric-tracks').textContent).toContain('3')
    })
    expect(screen.getByTestId('metric-distance').textContent).toContain('—')
    expect(screen.getByTestId('metric-distance').textContent).toContain('unavailable')
    expect(screen.getByTestId('metric-moving').textContent).toContain('—')
    expect(screen.queryByText('0.0 km')).not.toBeInTheDocument()
  })

  it('says how much of a partial period the total covers', async () => {
    archive(
      onYear(
        year({
          track_count: 49,
          analysed_track_count: 47,
          tracks_with_outdated_analysis: 1,
          tracks_with_invalid_analysis: 1,
          distance_m: 470_000,
        }),
      ),
    )

    show()

    await waitFor(() => {
      expect(screen.getByText('47 of 49 tracks current')).toBeInTheDocument()
    })
    expect(screen.getByTestId('coverage-warning').textContent).toContain('1 track needs re-analysis')
    expect(screen.getByTestId('coverage-warning').textContent).toContain('invalid derived data')
    // The counter links to exactly the tracks it counted.
    expect(
      screen.getByRole('link', { name: '1 track needs re-analysis' }).getAttribute('href'),
    ).toBe('/tracks?kind=recorded&analysis_status=outdated')
  })

  it('reports what belongs to no calendar period beside the year', async () => {
    archive(
      onYear(
        year(
          { track_count: 1, distance_m: 1000 },
          {
            without_date: totals({ track_count: 2 }),
            with_unverified_date: totals({ track_count: 1, distance_m: 8900 }),
          },
        ),
      ),
    )

    show()

    await waitFor(() => {
      expect(screen.getByTestId('unplaced-note')).toBeInTheDocument()
    })
    expect(screen.getByTestId('unplaced-note').textContent).toContain('nothing vouches for')
  })

  it('opens a month against the same period the chart drew', async () => {
    archive(on('/monthly', monthly([{}, {}, {}, {}, {}, {}, {}, {}, {}, { track_count: 4 }])))

    show()
    await waitFor(() => {
      expect(screen.getByTestId('open-month-10')).toBeInTheDocument()
    })
    await userEvent.click(screen.getByTestId('open-month-10'))

    await waitFor(() => {
      expect(screen.getByTestId('location').textContent).toBe(
        '/tracks?year=2025&month=10&kind=recorded',
      )
    })
  })

  it('tells an empty archive apart from an empty selection', async () => {
    archive(on('/statistics/years', availableYears({ years: [], archive_track_count: 0 })))

    show()

    await waitFor(() => {
      expect(screen.getByTestId('empty-archive')).toBeInTheDocument()
    })
    expect(screen.getByTestId('empty-archive').textContent).toContain('gpx-view import')
    // There is no upload endpoint, so the page must not invite one.
    expect(screen.getByTestId('empty-archive').textContent).not.toContain('Upload')
  })

  it('says so when the archive holds tracks but none this scope can date', async () => {
    archive(
      on(
        '/statistics/years',
        availableYears({
          years: [],
          archive_track_count: 4,
          unplaced: { without_date: 1, with_unverified_date: 3 },
        }),
      ),
    )

    show()

    await waitFor(() => {
      expect(screen.getByTestId('nothing-dated')).toBeInTheDocument()
    })
    expect(screen.getByTestId('unplaced-note').textContent).toContain('nothing vouches for')
  })

  it('offers a retry when the archive cannot be reached', async () => {
    stubArchive([failing('/statistics/years')])

    show()

    await waitFor(() => {
      expect(screen.getByTestId('request-error')).toBeInTheDocument()
    })
    expect(screen.getByTestId('retry')).toBeInTheDocument()
  })

  it('offers no moving time for a scope where it would be a claim about a person', async () => {
    archive(on('/statistics/years', availableYears({ scope: 'planned' })))

    show('/?scope=planned')

    await waitFor(() => {
      expect(screen.getByTestId('metric-moving')).toBeInTheDocument()
    })
    expect(screen.getByTestId('metric-moving').textContent).toContain('—')
    expect(screen.getByTestId('metric-moving').textContent).toContain('Only recorded tracks')
  })
})

describe('choosing which year to show', () => {
  it('honours a year the archive has', () => {
    expect(selectedYear('2019', [2021, 2019])).toBe(2019)
  })

  it('falls back to the newest available year rather than showing an empty one', () => {
    expect(selectedYear('2026', [2021, 2019])).toBe(2021)
    expect(selectedYear(null, [2021, 2019])).toBe(2021)
    expect(selectedYear('not-a-year', [2021])).toBe(2021)
  })

  it('answers null when the scope has no dated tracks at all', () => {
    expect(selectedYear('2021', [])).toBeNull()
  })
})
