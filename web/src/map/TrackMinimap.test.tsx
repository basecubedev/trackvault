import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  coverage,
  everythingIsVisible,
  geometry,
  mapSource,
  on,
  stubArchive,
  type Route,
} from '../test-fixtures'
import { renderTrackMinimap } from './minimap'
import { TrackMinimap } from './TrackMinimap'

/**
 * The small map beside a row in the track list.
 *
 * Two things make it different from the map on a track's own page, and both are
 * behaviour rather than styling: it costs nothing until somebody scrolls to it,
 * and it never asks for the canonical geometry -- a page of twenty-five rows
 * that each downloaded a full recording would be a listing that fetches a
 * hundred megabytes to draw a hundred pixels.
 *
 * The drawing itself needs WebGL and is exercised in the browser suite. What is
 * asserted here is what the row asks the archive for, what it does with the
 * answer, and what it does when there is no answer.
 */

const IMAGE = 'data:image/png;base64,iVBORw0KGgo='

vi.mock('./minimap', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./minimap')>()),
  renderTrackMinimap: vi.fn(() => Promise.resolve(IMAGE)),
}))

const drawn = vi.mocked(renderTrackMinimap)

function show(routes: Route[], onAttribution?: (lines: readonly string[]) => void) {
  const stub = stubArchive(routes)
  render(
    <TrackMinimap
      trackId={7}
      title="Talaia ridge walk"
      {...(onAttribution ? { onAttribution } : {})}
    />,
  )
  return stub
}

const ARCHIVE_DRAWS_A_TRACK: Route[] = [
  on('/geometry', geometry()),
  on('/maps/coverage', coverage({ sources: [mapSource()], any_installed: true })),
]

beforeEach(() => {
  drawn.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a minimap nobody has scrolled to', () => {
  it('asks the archive for nothing at all', async () => {
    const stub = show(ARCHIVE_DRAWS_A_TRACK)

    await waitFor(() => {
      expect(screen.getByTestId('track-minimap')).toBeInTheDocument()
    })
    expect(stub.requested).toEqual([])
    expect(drawn).not.toHaveBeenCalled()
  })
})

describe('a minimap a reader has reached', () => {
  beforeEach(() => {
    everythingIsVisible()
  })

  it('asks for a shape it can draw, never the canonical geometry', async () => {
    const stub = show(ARCHIVE_DRAWS_A_TRACK)

    await waitFor(() => {
      expect(stub.requested.some((url) => url.includes('/geometry'))).toBe(true)
    })
    const asked = stub.requested.find((url) => url.includes('/geometry')) ?? ''
    const bound = Number(new URL(asked, 'http://archive').searchParams.get('max_points'))
    expect(bound).toBeGreaterThan(1)
    expect(bound).toBeLessThanOrEqual(500)
  })

  it('draws the basemap the archive selects for that track’s own extent', async () => {
    const stub = show(ARCHIVE_DRAWS_A_TRACK)

    await waitFor(() => {
      expect(stub.requested.some((url) => url.includes('/maps/coverage'))).toBe(true)
    })
    // The fixture track runs from 2.8/39.6 to 2.9/39.7. The rectangle asked
    // about is the track's, not the page's and not the world's.
    const asked = stub.requested.find((url) => url.includes('/maps/coverage')) ?? ''
    expect(decodeURIComponent(asked)).toContain('bbox=2.800,39.600,2.900,39.700')
  })

  it('shows the drawn map under a name a reader can use', async () => {
    show(ARCHIVE_DRAWS_A_TRACK)

    await waitFor(() => {
      expect(screen.getByRole('img', { name: /Talaia ridge walk/ })).toBeInTheDocument()
    })
    expect(screen.getByRole<HTMLImageElement>('img', { name: /Talaia ridge walk/ }).src).toBe(IMAGE)
  })

  it('reports what the map it drew has to credit', async () => {
    const credited = vi.fn()
    show(ARCHIVE_DRAWS_A_TRACK, credited)

    await waitFor(() => {
      expect(credited).toHaveBeenCalledWith(['Map data © OpenStreetMap contributors'])
    })
  })

  it('credits nothing when it drew over a neutral background', async () => {
    const credited = vi.fn()
    show([on('/geometry', geometry()), on('/maps/coverage', coverage())], credited)

    await waitFor(() => {
      expect(drawn).toHaveBeenCalled()
    })
    expect(credited).toHaveBeenCalledWith([])
  })

  it('leaves the row intact when the archive cannot answer', async () => {
    show([])

    await waitFor(() => {
      expect(screen.getByTestId('track-minimap')).toHaveAttribute('data-state', 'unavailable')
    })
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(drawn).not.toHaveBeenCalled()
  })

  it('draws nothing for a track that holds no positions', async () => {
    show([
      on('/geometry', geometry({ segments: [], segment_count: 0, point_count: 0 })),
      on('/maps/coverage', coverage()),
    ])

    await waitFor(() => {
      expect(screen.getByTestId('track-minimap')).toHaveAttribute('data-state', 'unavailable')
    })
    // No shape means no rectangle, so there is nothing to ask coverage about
    // either. Asking anyway would be a request whose answer cannot be used.
    expect(drawn).not.toHaveBeenCalled()
  })
})
