/**
 * Where a drawn minimap is kept between visits.
 *
 * The archive already serves its tiles immutable under a content hash, so the
 * browser's own HTTP cache holds the map data. What nothing held was the
 * *result*: a page of twenty-five rows drew twenty-five maps with WebGL, threw
 * every one of them away on navigation, and drew them again on the way back.
 * This keeps the finished picture instead, under a key that says what it is a
 * picture of.
 *
 * **IndexedDB, not `localStorage`.** These are images. `localStorage` is
 * synchronous, small, and stores text -- a PNG would have to be base64 inflated
 * by a third on the way in and would block the main thread on the way out.
 *
 * **Bytes and a media type, not a `Blob`.** A `Blob` is the obvious record
 * field and it is not portable: implementations have shipped that cannot
 * structured-clone one into a store, and a cache that works in one browser is
 * worse than no cache because nobody notices. Bytes go in, a `Blob` comes back
 * out, and the size the eviction arithmetic uses is the size that was stored.
 *
 * **Nothing here may fail a caller.** Every method answers "no" instead of
 * throwing: a browser with storage switched off, a quota that is full, a
 * database somebody cleared mid-session and a record this build cannot read all
 * end at the same place, which is a track drawn the ordinary way. A cache is an
 * optimisation, and an optimisation that can break a page is a defect.
 */

const DATABASE = 'trackvault-minimap'
const SCHEMA_VERSION = 1
const STORE = 'renders'
const ACCESSED_INDEX = 'last_accessed_at'
/**
 * The schema version lives in the database's own version, not in its name.
 *
 * A renamed database is one nothing opens again -- and a database nothing opens
 * is quota nobody reclaims, on a machine whose owner never asked to keep it.
 * Bumping the version instead makes the upgrade delete the old store on the
 * first page load of the new build. Kept renderings are derived data; there is
 * nothing here to migrate, and drawing them again is the whole point.
 */

export const MAX_CACHE_BYTES = 64 * 1024 * 1024
export const MAX_CACHE_ENTRIES = 2000
/**
 * How much of somebody's disk this may occupy.
 *
 * A minimap is a 132x96 PNG -- a few kilobytes -- so the entry ceiling is what
 * usually binds: two thousand of them is an archive far larger than anyone
 * scrolls through in a session, at a few megabytes. The byte ceiling is the
 * backstop for the day the previews get bigger.
 */

const MEDIA_TYPE = 'image/png'
const MIN_IMAGE_BYTES = 64
const MAX_IMAGE_BYTES = 4 * 1024 * 1024

export interface CacheStatistics {
  entries: number
  bytes: number
}

export interface MinimapCache {
  /** The kept rendering for a key, or `null` for anything that is not one. */
  get(key: string): Promise<Blob | null>
  /** Keep one rendering, evicting older ones if it no longer fits. */
  put(key: string, image: Blob): Promise<void>
  delete(key: string): Promise<void>
  clear(): Promise<void>
  /** What is currently kept. For tests and for looking, never for deciding. */
  stats(): Promise<CacheStatistics>
}

interface StoredRender {
  key: string
  bytes: ArrayBuffer
  media_type: string
  size: number
  created_at: number
  last_accessed_at: number
}

interface CacheLimits {
  bytes: number
  entries: number
}

export interface CacheOptions {
  /**
   * The clock entries are stamped with.
   *
   * Injected rather than read from global state, because eviction order is the
   * one thing here worth asserting and a test that has to sleep to establish it
   * is a test that fails on a busy machine.
   */
  now?: () => number
  limits?: Partial<CacheLimits>
  /** The database to use. Named so two tests cannot see each other's entries. */
  database?: string
}

/**
 * Build a cache over one IndexedDB database.
 *
 * One connection, opened on first use and reused. Opening is attempted once: a
 * browser that refused storage will refuse it again, and retrying per row would
 * turn a switched-off feature into a stream of failures.
 */
export function createMinimapCache(options: CacheOptions = {}): MinimapCache {
  const now = options.now ?? (() => Date.now())
  const database = options.database ?? DATABASE
  const limits: CacheLimits = {
    bytes: options.limits?.bytes ?? MAX_CACHE_BYTES,
    entries: options.limits?.entries ?? MAX_CACHE_ENTRIES,
  }
  let connection: Promise<IDBDatabase | null> | null = null

  const connect = (): Promise<IDBDatabase | null> => {
    connection ??= openDatabase(database)
    return connection
  }

  return {
    async get(key) {
      const db = await connect()
      if (db === null) return null
      let stored: StoredRender | undefined
      try {
        stored = await inStore(
          db,
          'readonly',
          (store) => store.get(key) as IDBRequest<StoredRender | undefined>,
        )
      } catch {
        return null
      }
      if (stored === undefined) return null
      if (!isIntact(stored)) {
        // A record this build cannot vouch for is not repaired and not
        // interpreted: it is removed, and the caller draws the map again. The
        // replacement lands on the next `put` under the same key.
        await ignoring(remove(db, key))
        return null
      }
      await ignoring(touch(db, stored, now()))
      return new Blob([stored.bytes], { type: stored.media_type })
    },

    async put(key, image) {
      const db = await connect()
      if (db === null) return
      let bytes: ArrayBuffer
      try {
        bytes = await image.arrayBuffer()
      } catch {
        return
      }
      const stamp = now()
      const entry: StoredRender = {
        key,
        bytes,
        media_type: image.type,
        size: bytes.byteLength,
        created_at: stamp,
        last_accessed_at: stamp,
      }
      // Nothing implausible is kept, so a damaged entry is one the storage
      // damaged rather than one this wrote. An empty canvas read back as a
      // zero-byte image would otherwise be cached as a track's picture.
      if (!isIntact(entry)) return
      try {
        await write(db, entry)
      } catch (error) {
        if (!isOutOfQuota(error)) return
        // The declared ceiling is not the browser's. Make room by the same rule
        // eviction always uses, and try once more; a second refusal means the
        // machine is full, which is not this cache's problem to solve.
        await ignoring(evictDownTo(db, halved(limits)))
        try {
          await write(db, entry)
        } catch {
          return
        }
      }
      await ignoring(evictDownTo(db, limits))
    },

    async delete(key) {
      const db = await connect()
      if (db === null) return
      await ignoring(remove(db, key))
    },

    async clear() {
      const db = await connect()
      if (db === null) return
      await ignoring(
        finished(db, 'readwrite', (store) => {
          store.clear()
        }),
      )
    },

    async stats() {
      const db = await connect()
      if (db === null) return { entries: 0, bytes: 0 }
      try {
        const kept = await oldestFirst(db)
        return {
          entries: kept.length,
          bytes: kept.reduce((total, entry) => total + entry.size, 0),
        }
      } catch {
        return { entries: 0, bytes: 0 }
      }
    },
  }
}

/** The one cache the application uses. */
export const minimapCache = createMinimapCache()

/**
 * Whether a record is something this build is willing to hand back as an image.
 *
 * Storage is untrusted input, exactly as the archive's own database is: what
 * comes back may have been written by a version that is gone or by a disk that
 * is failing. The checks are structural, not a decoding attempt -- a PNG that
 * decodes to the wrong picture is a bug in the key, not in the bytes.
 */
function isIntact(entry: StoredRender): boolean {
  return (
    typeof entry.key === 'string' &&
    entry.key.length > 0 &&
    isBinary(entry.bytes) &&
    entry.media_type === MEDIA_TYPE &&
    entry.size === entry.bytes.byteLength &&
    entry.size >= MIN_IMAGE_BYTES &&
    entry.size <= MAX_IMAGE_BYTES &&
    Number.isFinite(entry.created_at) &&
    Number.isFinite(entry.last_accessed_at)
  )
}

/**
 * Whether a value really is a buffer of bytes.
 *
 * Branded rather than tested with `instanceof`. What comes back out of a store
 * has been through a structured clone, and a clone is not obliged to hand back
 * an object built by *this* realm's constructor -- `instanceof` then answers
 * "no" about a perfectly good buffer, which would be a cache that silently
 * never hits.
 */
function isBinary(value: unknown): boolean {
  return Object.prototype.toString.call(value) === '[object ArrayBuffer]'
}

async function openDatabase(database: string): Promise<IDBDatabase | null> {
  // Read as an optional property rather than through the global's declared
  // type, which claims it is always there. Not a defensive `if` for a case that
  // cannot happen: private modes and hardened configurations really do leave
  // this undefined, and the declaration is what would be wrong.
  const { indexedDB: factory } = globalThis as { indexedDB?: IDBFactory }
  if (factory === undefined || typeof factory.open !== 'function') return null
  try {
    return await new Promise<IDBDatabase>((resolve, reject) => {
      const opening = factory.open(database, SCHEMA_VERSION)
      opening.onupgradeneeded = () => {
        const db = opening.result
        if (db.objectStoreNames.contains(STORE)) db.deleteObjectStore(STORE)
        const store = db.createObjectStore(STORE, { keyPath: 'key' })
        store.createIndex(ACCESSED_INDEX, 'last_accessed_at')
      }
      opening.onsuccess = () => {
        const db = opening.result
        // A newer build in another tab must be able to upgrade. Holding this
        // connection open would block it indefinitely, and the page whose
        // cache closes simply draws its maps.
        db.onversionchange = () => {
          db.close()
        }
        resolve(db)
      }
      opening.onerror = () => {
        reject(opening.error ?? new Error('the minimap cache could not be opened'))
      }
      opening.onblocked = () => {
        reject(new Error('the minimap cache is held open by another tab'))
      }
    })
  } catch {
    return null
  }
}

function inStore<T>(
  db: IDBDatabase,
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const transaction = db.transaction(STORE, mode)
    const request = work(transaction.objectStore(STORE))
    request.onsuccess = () => {
      resolve(request.result)
    }
    request.onerror = () => {
      reject(request.error ?? new Error('the minimap cache refused a request'))
    }
    transaction.onabort = () => {
      reject(transaction.error ?? new Error('the minimap cache aborted a transaction'))
    }
  })
}

/** Run work over the store and settle when the whole transaction has. */
function finished(
  db: IDBDatabase,
  mode: IDBTransactionMode,
  work: (store: IDBObjectStore) => void,
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const transaction = db.transaction(STORE, mode)
    // The failure that matters for a write is the transaction's, not the
    // request's: a quota refusal aborts the transaction, and a promise settled
    // on the request alone would report a successful write of nothing.
    transaction.oncomplete = () => {
      resolve()
    }
    transaction.onerror = () => {
      reject(transaction.error ?? new Error('the minimap cache refused a write'))
    }
    transaction.onabort = () => {
      reject(transaction.error ?? new Error('the minimap cache aborted a write'))
    }
    try {
      work(transaction.objectStore(STORE))
    } catch (error) {
      // A synchronous refusal -- `put` can throw outright when a value cannot
      // be stored at all -- never reaches the transaction's handlers.
      transaction.abort()
      reject(asError(error))
    }
  })
}

/**
 * Return a rejection reason that still says which failure this was.
 *
 * A storage refusal arrives as a `DOMException`, whose *name* is the whole
 * message: `QuotaExceededError` is answered by making room, anything else is
 * not. That name has to survive, and a `DOMException` is not an `Error`
 * everywhere -- so it is carried across rather than replaced by one.
 */
function asError(error: unknown): Error {
  if (error instanceof Error) return error
  const raised = new Error('the minimap cache refused a write')
  if (typeof error === 'object' && error !== null && 'name' in error) {
    raised.name = String(error.name)
  }
  return raised
}

function write(db: IDBDatabase, entry: StoredRender): Promise<void> {
  return finished(db, 'readwrite', (store) => {
    store.put(entry)
  })
}

function remove(db: IDBDatabase, key: string): Promise<void> {
  return finished(db, 'readwrite', (store) => {
    store.delete(key)
  })
}

/** Record that an entry was used, so eviction takes the ones nobody reads. */
function touch(db: IDBDatabase, stored: StoredRender, at: number): Promise<void> {
  return finished(db, 'readwrite', (store) => {
    store.put({ ...stored, last_accessed_at: at })
  })
}

interface KeptEntry {
  key: string
  size: number
}

/**
 * Every kept entry, least recently used first.
 *
 * A full walk on each write, which is affordable because the entry ceiling
 * bounds it: two thousand records of a key and a number. Keeping a running
 * total in memory instead would be a second account of what is stored, and the
 * two would disagree the first time another tab wrote something.
 */
function oldestFirst(db: IDBDatabase): Promise<KeptEntry[]> {
  return new Promise<KeptEntry[]>((resolve, reject) => {
    const transaction = db.transaction(STORE, 'readonly')
    const cursor = transaction.objectStore(STORE).index(ACCESSED_INDEX).openCursor()
    const kept: KeptEntry[] = []
    cursor.onsuccess = () => {
      const at = cursor.result
      if (at === null) {
        resolve(kept)
        return
      }
      const entry = at.value as StoredRender
      kept.push({ key: entry.key, size: entry.size })
      at.continue()
    }
    cursor.onerror = () => {
      reject(cursor.error ?? new Error('the minimap cache could not be read'))
    }
    transaction.onabort = () => {
      reject(transaction.error ?? new Error('the minimap cache aborted a read'))
    }
  })
}

/** Drop the least recently used entries until the cache is within its limits. */
async function evictDownTo(db: IDBDatabase, limits: CacheLimits): Promise<void> {
  const kept = await oldestFirst(db)
  let bytes = kept.reduce((total, entry) => total + entry.size, 0)
  let entries = kept.length
  const doomed: string[] = []
  for (const entry of kept) {
    if (bytes <= limits.bytes && entries <= limits.entries) break
    doomed.push(entry.key)
    bytes -= entry.size
    entries -= 1
  }
  if (doomed.length === 0) return
  await finished(db, 'readwrite', (store) => {
    for (const key of doomed) store.delete(key)
  })
}

/** Half the room, for the retry after a quota refusal. */
function halved(limits: CacheLimits): CacheLimits {
  return { bytes: Math.floor(limits.bytes / 2), entries: Math.floor(limits.entries / 2) }
}

function isOutOfQuota(error: unknown): boolean {
  return (
    typeof error === 'object' &&
    error !== null &&
    'name' in error &&
    error.name === 'QuotaExceededError'
  )
}

/** Await work whose failure is not the caller's problem. */
async function ignoring(work: Promise<unknown>): Promise<void> {
  try {
    await work
  } catch {
    /* the picture is what matters; keeping it is the optimisation */
  }
}
