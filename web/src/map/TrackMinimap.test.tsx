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
 * Three things make it different from the map on a track's own page, and all
 * three are behaviour rather than styling: it costs nothing until somebody
 * scrolls to it, it never asks for the canonical geometry -- a page of
 * twenty-five rows that each downloaded a full recording would be a listing
 * that fetches a hundred megabytes to draw a hundred pixels -- and a picture it
 * has already drawn is not drawn again.
 *
 * The drawing itself needs WebGL and is exercised in the browser suite. What is
 * asserted here is what the row asks the archive for, what it does with the
 * answer, when it draws and when it deliberately does not.
 */

const PNG = new Blob([new Uint8Array(128).fill(3)], { type: 'image/png' })

vi.mock('./minimap', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./minimap')>()),
  renderTrackMinimap: vi.fn(() => Promise.resolve(PNG)),
}))

/**
 * A store the test owns, in place of the browser's.
 *
 * The read-through and the concurrency register are the real ones: what is
 * substituted is only the place a picture is kept, so these tests exercise the
 * component's use of the cache rather than a description of it.
 */
const kept = vi.hoisted(() => {
  const store = new Map<string, Blob>()
  return {
    store,
    get: vi.fn((key: string) => Promise.resolve(store.get(key) ?? null)),
    put: vi.fn((key: string, image: Blob) => {
      store.set(key, image)
      return Promise.resolve()
    }),
  }
})

vi.mock('../cache/minimapCache', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../cache/minimapCache')>()),
  minimapCache: {
    get: kept.get,
    put: kept.put,
    delete: () => Promise.resolve(),
    clear: () => Promise.resolve(),
    stats: () => Promise.resolve({ entries: kept.store.size, bytes: 0 }),
  },
}))

const drawn = vi.mocked(renderTrackMinimap)

const GEOMETRY_ID = 'a'.repeat(64)

function show(
  routes: Route[],
  options: {
    onAttribution?: (lines: readonly string[]) => void
    geometryId?: string | null
    title?: string
  } = {},
) {
  const stub = stubArchive(routes)
  const view = render(
    <TrackMinimap
      trackId={7}
      geometryId={options.geometryId === undefined ? GEOMETRY_ID : options.geometryId}
      title={options.title ?? 'Talaia ridge walk'}
      {...(options.onAttribution ? { onAttribution: options.onAttribution } : {})}
    />,
  )
  return { stub, view }
}

const ARCHIVE_DRAWS_A_TRACK: Route[] = [
  on('/geometry', geometry()),
  on('/maps/coverage', coverage({ sources: [mapSource()], any_installed: true })),
]

/** The same archive, with the region installed again from newer data. */
function withDelivery(delivery: string): Route[] {
  return [
    on('/geometry', geometry()),
    on(
      '/maps/coverage',
      coverage({ sources: [mapSource({ delivery_id: delivery })], any_installed: true }),
    ),
  ]
}

async function shownMap() {
  return waitFor(() => screen.getByRole('img', { name: /went/ }))
}

beforeEach(() => {
  drawn.mockClear()
  kept.get.mockClear()
  kept.put.mockClear()
  kept.store.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a minimap nobody has scrolled to', () => {
  it('asks the archive for nothing at all', async () => {
    const { stub } = show(ARCHIVE_DRAWS_A_TRACK)

    await waitFor(() => {
      expect(screen.getByTestId('track-minimap')).toBeInTheDocument()
    })
    expect(stub.requested).toEqual([])
    expect(drawn).not.toHaveBeenCalled()
    expect(kept.get).not.toHaveBeenCalled()
  })
})

describe('a minimap a reader has reached', () => {
  beforeEach(() => {
    everythingIsVisible()
  })

  it('asks for a shape it can draw, never the canonical geometry', async () => {
    const { stub } = show(ARCHIVE_DRAWS_A_TRACK)

    await waitFor(() => {
      expect(stub.requested.some((url) => url.includes('/geometry'))).toBe(true)
    })
    const asked = stub.requested.find((url) => url.includes('/geometry')) ?? ''
    const bound = Number(new URL(asked, 'http://archive').searchParams.get('max_points'))
    expect(bound).toBeGreaterThan(1)
    expect(bound).toBeLessThanOrEqual(500)
  })

  it('draws the basemap the archive selects for that track’s own extent', async () => {
    const { stub } = show(ARCHIVE_DRAWS_A_TRACK)

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

    const image = await shownMap()

    expect(image.getAttribute('src')).toMatch(/^blob:/)
  })

  it('reports what the map it drew has to credit', async () => {
    const credited = vi.fn()
    show(ARCHIVE_DRAWS_A_TRACK, { onAttribution: credited })

    await waitFor(() => {
      expect(credited).toHaveBeenCalledWith(['Map data © OpenStreetMap contributors'])
    })
  })

  it('credits nothing when it drew over a neutral background', async () => {
    const credited = vi.fn()
    show([on('/geometry', geometry()), on('/maps/coverage', coverage())], {
      onAttribution: credited,
    })

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

describe('a picture that has been drawn before', () => {
  beforeEach(() => {
    everythingIsVisible()
  })

  it('is looked for before anything is drawn', async () => {
    show(ARCHIVE_DRAWS_A_TRACK)

    await shownMap()

    expect(kept.get).toHaveBeenCalledTimes(1)
    expect(kept.put).toHaveBeenCalledTimes(1)
  })

  it('is shown again without drawing anything', async () => {
    const { view } = show(ARCHIVE_DRAWS_A_TRACK)
    await shownMap()
    expect(drawn).toHaveBeenCalledTimes(1)
    view.unmount()
    drawn.mockClear()

    show(ARCHIVE_DRAWS_A_TRACK)

    await shownMap()
    expect(drawn).not.toHaveBeenCalled()
  })

  it('survives the track being renamed', async () => {
    // The point of keying on the geometry rather than on the track: correcting
    // a title changes what the row says, not what its map looks like.
    const { view } = show(ARCHIVE_DRAWS_A_TRACK, { title: 'Talaia ridge walk' })
    await shownMap()
    view.unmount()
    drawn.mockClear()

    show(ARCHIVE_DRAWS_A_TRACK, { title: 'The good ridge walk' })

    await screen.findByRole('img', { name: /The good ridge walk/ })
    expect(drawn).not.toHaveBeenCalled()
  })

  it('is not reused after the track has been processed again', async () => {
    // Reprocessing that moves a position is a different shape, so the picture
    // of the old one is not found. An old rendering must never be able to stand
    // in for a track the archive has since changed.
    const { view } = show(ARCHIVE_DRAWS_A_TRACK)
    await shownMap()
    view.unmount()
    drawn.mockClear()

    show(ARCHIVE_DRAWS_A_TRACK, { geometryId: 'b'.repeat(64) })

    await shownMap()
    expect(drawn).toHaveBeenCalledTimes(1)
  })

  it('is not reused after the map behind it was installed again', async () => {
    const { view } = show(withDelivery('c'.repeat(64)))
    await shownMap()
    view.unmount()
    drawn.mockClear()

    show(withDelivery('d'.repeat(64)))

    await shownMap()
    expect(drawn).toHaveBeenCalledTimes(1)
  })

  it('is not kept at all for a track the archive cannot identify a shape for', async () => {
    show(ARCHIVE_DRAWS_A_TRACK, { geometryId: null })

    await shownMap()

    expect(drawn).toHaveBeenCalledTimes(1)
    expect(kept.get).not.toHaveBeenCalled()
    expect(kept.put).not.toHaveBeenCalled()
  })
})

describe('two rows showing the same track', () => {
  beforeEach(() => {
    everythingIsVisible()
  })

  it('draw one picture between them', async () => {
    // One recording imported from two files is two rows over one afternoon and
    // one map. Twenty-five rows each holding a WebGL context is the failure
    // this whole design exists to avoid.
    const stub = stubArchive(ARCHIVE_DRAWS_A_TRACK)
    render(
      <>
        <TrackMinimap trackId={7} geometryId={GEOMETRY_ID} title="From the phone" />
        <TrackMinimap trackId={8} geometryId={GEOMETRY_ID} title="From the watch" />
      </>,
    )

    await screen.findByRole('img', { name: /From the phone/ })
    await screen.findByRole('img', { name: /From the watch/ })
    expect(drawn).toHaveBeenCalledTimes(1)
    expect(stub.requested.filter((url) => url.includes('/geometry'))).toHaveLength(2)
  })
})

describe('a cache that is not working', () => {
  beforeEach(() => {
    everythingIsVisible()
  })

  it('cannot stop a row showing its map when it cannot be read', async () => {
    kept.get.mockImplementationOnce(() => Promise.reject(new Error('storage is gone')))

    show(ARCHIVE_DRAWS_A_TRACK)

    await shownMap()
    expect(drawn).toHaveBeenCalledTimes(1)
  })

  it('cannot stop a row showing its map when it cannot be written', async () => {
    kept.put.mockImplementationOnce(() => Promise.reject(new Error('the disk is full')))

    show(ARCHIVE_DRAWS_A_TRACK)

    await shownMap()
    expect(drawn).toHaveBeenCalledTimes(1)
  })
})
