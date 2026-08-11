import 'fake-indexeddb/auto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMinimapCache, type MinimapCache } from './minimapCache'

/**
 * Where a drawn minimap is kept, and everything that can go wrong with keeping it.
 *
 * IndexedDB is the one browser API `jsdom` does not implement, so the suite
 * runs against `fake-indexeddb`. That is worth a development dependency
 * precisely because of the tests below: quota, corruption and eviction are the
 * paths a cache is judged on and the paths nobody exercises by hand, and
 * leaving them to a browser suite would mean leaving them untested.
 *
 * Every test asserts one half of the same promise: what is kept comes back
 * exactly, and *nothing* that goes wrong is allowed to reach a caller as a
 * failure. A cache that can break a page is worse than no cache.
 */

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, ...new Array<number>(120).fill(7)])

function png(fill = 7, bytes = 124): Blob {
  const content = new Uint8Array(bytes)
  content.set([0x89, 0x50, 0x4e, 0x47])
  content.fill(fill, 4)
  return new Blob([content], { type: 'image/png' })
}

let databases = 0

/** A cache nothing else can see, on a clock this test controls. */
function freshCache(options: { limits?: { bytes?: number; entries?: number } } = {}) {
  databases += 1
  const database = `minimap-test-${String(databases)}`
  let clock = 1000
  const cache = createMinimapCache({
    database,
    now: () => {
      clock += 1
      return clock
    },
    ...(options.limits ? { limits: options.limits } : {}),
  })
  return { cache, database }
}

async function bytesOf(image: Blob | null): Promise<number[]> {
  if (image === null) throw new Error('there was nothing kept')
  return [...new Uint8Array(await image.arrayBuffer())]
}

/** Reach past the repository, to write what only a damaged store would hold. */
function writeRaw(database: string, record: unknown): Promise<void> {
  return new Promise((resolve, reject) => {
    const opening = indexedDB.open(database)
    opening.onsuccess = () => {
      const db = opening.result
      const transaction = db.transaction('renders', 'readwrite')
      transaction.objectStore('renders').put(record)
      transaction.oncomplete = () => {
        db.close()
        resolve()
      }
      transaction.onerror = () => {
        reject(new Error('the raw write failed'))
      }
    }
    opening.onerror = () => {
      reject(new Error('the raw open failed'))
    }
  })
}

/**
 * Make the store refuse the next writes the way a full disk refuses them.
 *
 * A real `QuotaExceededError`, raised where a real one is raised. The declared
 * ceiling is not the browser's, so being told "no" by the storage itself is a
 * normal event this has to survive rather than an exotic one.
 */
function refuseWrites(times: number): () => void {
  const refused = vi.spyOn(IDBObjectStore.prototype, 'put')
  for (let attempt = 0; attempt < times; attempt += 1) {
    refused.mockImplementationOnce(() => {
      throw new DOMException('the quota is exhausted', 'QuotaExceededError')
    })
  }
  return () => {
    refused.mockRestore()
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a cache nothing has been put in', () => {
  it('answers a lookup with nothing rather than an error', async () => {
    const { cache } = freshCache()

    await expect(cache.get('nothing')).resolves.toBeNull()
  })

  it('reports itself empty', async () => {
    const { cache } = freshCache()

    await expect(cache.stats()).resolves.toEqual({ entries: 0, bytes: 0 })
  })
})

describe('a rendering that was kept', () => {
  it('comes back byte for byte, as a PNG', async () => {
    const { cache } = freshCache()
    await cache.put('key', new Blob([PNG], { type: 'image/png' }))

    const kept = await cache.get('key')

    expect(kept?.type).toBe('image/png')
    expect(await bytesOf(kept)).toEqual([...PNG])
  })

  it('survives a second cache opened over the same storage', async () => {
    // What a page reload is: the same origin, the same database, a new
    // connection. Persistence is the entire reason this is not a Map.
    const { cache, database } = freshCache()
    await cache.put('key', png())

    const reopened = createMinimapCache({ database })

    expect(await bytesOf(await reopened.get('key'))).toEqual([...new Uint8Array(await png().arrayBuffer())])
  })

  it('is replaced rather than duplicated when it is put again', async () => {
    const { cache } = freshCache()
    await cache.put('key', png(1))

    await cache.put('key', png(2))

    expect(await cache.stats()).toEqual({ entries: 1, bytes: 124 })
    expect((await bytesOf(await cache.get('key')))[10]).toBe(2)
  })

  it('is gone once it is deleted', async () => {
    const { cache } = freshCache()
    await cache.put('key', png())

    await cache.delete('key')

    await expect(cache.get('key')).resolves.toBeNull()
  })

  it('is gone once the whole cache is cleared', async () => {
    const { cache } = freshCache()
    await cache.put('one', png())
    await cache.put('other', png())

    await cache.clear()

    await expect(cache.stats()).resolves.toEqual({ entries: 0, bytes: 0 })
  })
})

describe('a cache entry that cannot be trusted', () => {
  it.each([
    ['bytes that are not bytes', { bytes: 'not an image' }],
    ['a media type nothing here writes', { media_type: 'text/html' }],
    ['a size that disagrees with the bytes', { size: 999 }],
    ['too few bytes to be an image at all', { bytes: new ArrayBuffer(3), size: 3 }],
    ['a stamp that is not a time', { last_accessed_at: Number.NaN }],
  ])('is not handed back: %s', async (_what, damage) => {
    const { cache, database } = freshCache()
    await cache.put('key', png())
    await writeRaw(database, {
      key: 'key',
      bytes: new ArrayBuffer(124),
      media_type: 'image/png',
      size: 124,
      created_at: 1,
      last_accessed_at: 1,
      ...damage,
    })

    await expect(cache.get('key')).resolves.toBeNull()
  })

  it('is removed, so the next rendering replaces it', async () => {
    const { cache, database } = freshCache()
    await cache.put('key', png())
    await writeRaw(database, { key: 'key', bytes: 'rubbish', media_type: 'image/png', size: 0 })

    await cache.get('key')

    await expect(cache.stats()).resolves.toEqual({ entries: 0, bytes: 0 })
  })

  it('is never written in the first place when the image is implausible', async () => {
    const { cache } = freshCache()

    await cache.put('empty', new Blob([], { type: 'image/png' }))
    await cache.put('wrong-type', new Blob([PNG], { type: 'image/jpeg' }))

    await expect(cache.stats()).resolves.toEqual({ entries: 0, bytes: 0 })
  })
})

describe('a cache that has run out of room', () => {
  it('makes room and keeps the rendering anyway', async () => {
    const { cache } = freshCache()
    await cache.put('older', png(1))
    const stop = refuseWrites(1)

    try {
      await cache.put('newer', png(2))
    } finally {
      stop()
    }

    // The first attempt was refused, room was made, the second succeeded.
    expect(await bytesOf(await cache.get('newer'))).toContain(2)
  })

  it('gives up quietly when the machine is simply full', async () => {
    const { cache } = freshCache()
    const stop = refuseWrites(10)

    try {
      await expect(cache.put('key', png())).resolves.toBeUndefined()
    } finally {
      stop()
    }

    await expect(cache.get('key')).resolves.toBeNull()
  })
})

describe('a cache at its limit', () => {
  it('drops the least recently used entry to fit a new one', async () => {
    const { cache } = freshCache({ limits: { entries: 2 } })
    await cache.put('first', png(1))
    await cache.put('second', png(2))

    await cache.put('third', png(3))

    expect(await cache.stats()).toEqual({ entries: 2, bytes: 248 })
    await expect(cache.get('first')).resolves.toBeNull()
    await expect(cache.get('third')).resolves.not.toBeNull()
  })

  it('counts reading an entry as using it', async () => {
    // Otherwise "least recently used" would mean "oldest", and the picture a
    // reader looks at on every visit would be the first one thrown away.
    const { cache } = freshCache({ limits: { entries: 2 } })
    await cache.put('first', png(1))
    await cache.put('second', png(2))
    await cache.get('first')

    await cache.put('third', png(3))

    await expect(cache.get('first')).resolves.not.toBeNull()
    await expect(cache.get('second')).resolves.toBeNull()
  })

  it('drops as many entries as the byte ceiling needs', async () => {
    const { cache } = freshCache({ limits: { bytes: 300 } })
    await cache.put('first', png(1))
    await cache.put('second', png(2))

    await cache.put('third', png(3))

    const kept = await cache.stats()
    expect(kept.bytes).toBeLessThanOrEqual(300)
    expect(kept.entries).toBe(2)
  })
})

describe('a browser that will not store anything', () => {
  it('leaves every operation harmless', async () => {
    vi.stubGlobal('indexedDB', undefined)
    const cache: MinimapCache = createMinimapCache({ database: 'never-opened' })

    await expect(cache.get('key')).resolves.toBeNull()
    await expect(cache.put('key', png())).resolves.toBeUndefined()
    await expect(cache.delete('key')).resolves.toBeUndefined()
    await expect(cache.clear()).resolves.toBeUndefined()
    await expect(cache.stats()).resolves.toEqual({ entries: 0, bytes: 0 })
  })

  it('is what a cleared browser looks like, and it recovers', async () => {
    // Somebody clearing site data is a normal event, not a corrupted state.
    const { cache, database } = freshCache()
    await cache.put('key', png())

    await new Promise<void>((resolve) => {
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
    })
    const after = createMinimapCache({ database })
    await expect(after.get('key')).resolves.toBeNull()

    await after.put('key', png())
    await expect(after.get('key')).resolves.not.toBeNull()
  })
})
