import { describe, expect, it, vi } from 'vitest'
import type { MinimapCache } from './minimapCache'
import { renderedMinimap } from './renderedMinimap'

/**
 * Drawing a minimap once: not once per visit, and not once per row asking for it.
 *
 * The cache answers the first; this answers the second. Both matter for the
 * same reason -- a rendering costs a WebGL context, and a browser grants a page
 * about sixteen of them.
 */

const PNG = new Blob([new Uint8Array(128).fill(9)], { type: 'image/png' })

function emptyCache(overrides: Partial<MinimapCache> = {}): MinimapCache {
  return {
    get: vi.fn(() => Promise.resolve<Blob | null>(null)),
    put: vi.fn(() => Promise.resolve()),
    delete: vi.fn(() => Promise.resolve()),
    clear: vi.fn(() => Promise.resolve()),
    stats: vi.fn(() => Promise.resolve({ entries: 0, bytes: 0 })),
    ...overrides,
  }
}

/**
 * A render that only finishes when the test says so.
 *
 * `started` resolves once the render has actually been asked for. The lookup
 * comes first and is asynchronous, so a test that released the render straight
 * away would be releasing something nobody had started yet.
 */
function heldRender() {
  let finish: (image: Blob) => void = () => {}
  let started: () => void = () => {}
  const hasStarted = new Promise<void>((resolve) => {
    started = resolve
  })
  const draw = vi.fn(() => {
    started()
    return new Promise<Blob>((resolve) => {
      finish = resolve
    })
  })
  return {
    draw,
    started: hasStarted,
    finish: (image: Blob) => {
      finish(image)
    },
  }
}

describe('a rendering the cache already holds', () => {
  it('is not drawn again', async () => {
    const kept = new Blob([new Uint8Array(64).fill(1)], { type: 'image/png' })
    const draw = vi.fn(() => Promise.resolve(PNG))

    const image = await renderedMinimap('key', draw, emptyCache({ get: () => Promise.resolve(kept) }))

    expect(draw).not.toHaveBeenCalled()
    expect(image).toBe(kept)
  })
})

describe('a rendering nothing holds', () => {
  it('is drawn once and kept', async () => {
    const put = vi.fn(() => Promise.resolve())
    const cache = emptyCache({ put })
    const draw = vi.fn(() => Promise.resolve(PNG))

    const image = await renderedMinimap('key', draw, cache)

    expect(draw).toHaveBeenCalledTimes(1)
    expect(put).toHaveBeenCalledWith('key', PNG)
    expect(image).toBe(PNG)
  })

  it('is looked for before it is drawn, never after', async () => {
    // The order is the whole point: a lookup after the render is a cache that
    // costs exactly what it exists to save.
    const order: string[] = []
    const cache = emptyCache({
      get: () => {
        order.push('looked')
        return Promise.resolve(null)
      },
    })

    await renderedMinimap(
      'key',
      () => {
        order.push('drew')
        return Promise.resolve(PNG)
      },
      cache,
    )

    expect(order).toEqual(['looked', 'drew'])
  })
})

describe('two rows asking for the same picture at once', () => {
  it('draw it once between them', async () => {
    const { draw, started, finish } = heldRender()
    const cache = emptyCache()

    const first = renderedMinimap('shared', draw, cache)
    const second = renderedMinimap('shared', draw, cache)
    await started
    finish(PNG)

    expect(await first).toBe(PNG)
    expect(await second).toBe(PNG)
    expect(draw).toHaveBeenCalledTimes(1)
  })

  it('leave nothing behind, so the next visit still asks the cache', async () => {
    // The register holds work in flight, not results. A key that stayed in it
    // would be a second cache with no eviction and no persistence, answering
    // for a picture nobody could ever evict.
    const { draw, started, finish } = heldRender()
    const get = vi.fn(() => Promise.resolve<Blob | null>(null))
    const cache = emptyCache({ get })
    const first = renderedMinimap('finished', draw, cache)
    await started
    finish(PNG)
    await first

    const later = vi.fn(() => Promise.resolve(PNG))
    await renderedMinimap('finished', later, cache)

    expect(get).toHaveBeenCalledTimes(2)
    expect(later).toHaveBeenCalledTimes(1)
  })

  it('let a failed rendering be attempted again', async () => {
    // A basemap that could not be loaded is a moment, not a verdict. Keeping
    // the failed attempt in the register would make one bad instant permanent
    // for as long as the page is open.
    const cache = emptyCache()
    const failing = vi.fn(() => Promise.reject(new Error('no basemap')))

    await expect(renderedMinimap('failing', failing, cache)).rejects.toThrow('no basemap')
    await expect(renderedMinimap('failing', failing, cache)).rejects.toThrow('no basemap')

    expect(failing).toHaveBeenCalledTimes(2)
  })
})

describe('two rows asking for different pictures', () => {
  it('draw both', async () => {
    const cache = emptyCache()
    const draw = vi.fn(() => Promise.resolve(PNG))

    await Promise.all([
      renderedMinimap('one', draw, cache),
      renderedMinimap('other', draw, cache),
    ])

    expect(draw).toHaveBeenCalledTimes(2)
  })
})

describe('a cache that is broken rather than empty', () => {
  it('cannot stop a rendering when reading throws', async () => {
    const cache = emptyCache({ get: () => Promise.reject(new Error('storage is gone')) })
    const draw = vi.fn(() => Promise.resolve(PNG))

    await expect(renderedMinimap('key', draw, cache)).resolves.toBe(PNG)
    expect(draw).toHaveBeenCalledTimes(1)
  })

  it('cannot stop a rendering when writing throws', async () => {
    const cache = emptyCache({ put: () => Promise.reject(new Error('the disk is full')) })

    await expect(renderedMinimap('key', () => Promise.resolve(PNG), cache)).resolves.toBe(PNG)
  })
})
