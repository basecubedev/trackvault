import { describe, expect, it } from 'vitest'
import { coverage, mapSource } from '../test-fixtures'
import { drawnDeliveries, type MinimapIdentity, minimapCacheKey, minimapIdentity } from './minimapKey'

/**
 * What decides that two minimaps are the same picture.
 *
 * Every test here is one half of the invariant the whole cache rests on: a
 * rendering may be reused when, and only when, everything that decided how it
 * looks is identical. The "only when" half is the one that matters -- a missed
 * reuse costs a redraw, a wrong reuse shows somebody the wrong track.
 *
 * The table below is deliberately exhaustive over `MinimapIdentity`, and it is
 * meant to be read as a mutation guard: drop a field from the key and exactly
 * one of these rows fails. That is the only evidence that the tests protect the
 * identity rather than describe it.
 */

const IDENTITY: MinimapIdentity = {
  shape: 'a'.repeat(64),
  deliveries: ['b'.repeat(64)],
  theme: 'outdoor',
  projectionVersion: 1,
  styleVersion: 1,
  renderVersion: 1,
  width: 132,
  height: 96,
  points: 150,
  maxZoom: 14,
  padding: 6,
  pixelRatio: 1,
}

function differing(change: Partial<MinimapIdentity>): string {
  return minimapCacheKey({ ...IDENTITY, ...change })
}

describe('the same inputs', () => {
  it('always produce the same key', () => {
    expect(minimapCacheKey(IDENTITY)).toBe(minimapCacheKey({ ...IDENTITY }))
  })

  it('produce a key that says what it is a key for', () => {
    // Namespaced and readable. A key nobody can interpret in a debugger is a
    // key nobody can tell a hit from a collision with.
    expect(minimapCacheKey(IDENTITY)).toContain('trackvault-minimap:v2')
    expect(minimapCacheKey(IDENTITY)).toContain(`shape=${IDENTITY.shape}`)
  })
})

describe('a changed input', () => {
  it.each([
    ['the line the archive answered with', { shape: 'c'.repeat(64) }],
    ['the packages drawn under it', { deliveries: ['d'.repeat(64)] }],
    ['a second package appearing', { deliveries: ['b'.repeat(64), 'd'.repeat(64)] }],
    ['no package at all', { deliveries: [] }],
    ['the theme', { theme: 'light' as const }],
    ['how a shape becomes a line and a frame', { projectionVersion: 2 }],
    ['what a basemap looks like', { styleVersion: 2 }],
    ['how the camera draws it', { renderVersion: 2 }],
    ['the width', { width: 133 }],
    ['the height', { height: 97 }],
    ['the position budget', { points: 200 }],
    ['the zoom ceiling', { maxZoom: 15 }],
    ['the padding', { padding: 7 }],
    ['the display resolution', { pixelRatio: 2 }],
  ])('makes a different key: %s', (_what, change) => {
    expect(differing(change)).not.toBe(minimapCacheKey(IDENTITY))
  })

  it('distinguishes the order two packages are stacked in', () => {
    // Which map draws over which is the picture. Two regions swapped is a
    // different rendering, and a set would have said it was the same one.
    const one = differing({ deliveries: ['b'.repeat(64), 'd'.repeat(64)] })
    const other = differing({ deliveries: ['d'.repeat(64), 'b'.repeat(64)] })

    expect(one).not.toBe(other)
  })
})

describe('the fields a key is built from', () => {
  it('cannot run into each other', () => {
    // Every part is named and separated. Without that, a value ending in a
    // digit and the number after it could compose into the same string as
    // another pair -- a collision that is invisible until it shows the wrong
    // track.
    const shifted = differing({ shape: `${'a'.repeat(63)}1`, points: 11 })
    const other = differing({ shape: `${'a'.repeat(63)}11`, points: 1 })

    expect(shifted).not.toBe(other)
  })

  it('are every field of the identity, with none left out', () => {
    // The guard against the defect this file was rewritten for: a field added
    // to `MinimapIdentity` and forgotten in the key would be an input that
    // changes the picture and not the key.
    const named = Object.keys(IDENTITY)
    const key = minimapCacheKey(IDENTITY)
    const parts = key.split('|').slice(1).length

    expect(parts).toBe(named.length)
  })
})

describe('the shared part of an identity', () => {
  it('is read from the modules that draw with it', () => {
    const identity = minimapIdentity({
      shape: 'a'.repeat(64),
      deliveries: [],
      theme: 'outdoor',
    })

    // Not asserted as numbers: the point is that a caller supplies none of
    // them, so no row can leave a rendering parameter out of its own key.
    expect(identity.width).toBeGreaterThan(0)
    expect(identity.height).toBeGreaterThan(0)
    expect(identity.points).toBeGreaterThan(1)
    expect(identity.maxZoom).toBeGreaterThan(0)
    expect(identity.padding).toBeGreaterThanOrEqual(0)
    expect(identity.projectionVersion).toBeGreaterThan(0)
    expect(identity.styleVersion).toBeGreaterThan(0)
    expect(identity.renderVersion).toBeGreaterThan(0)
    expect(identity.pixelRatio).toBeGreaterThan(0)
  })

  it('takes the resolution from the display rather than assuming one', () => {
    const plain = { shape: 'a'.repeat(64), deliveries: [], theme: 'outdoor' as const }
    const before = minimapIdentity(plain).pixelRatio

    Object.defineProperty(globalThis, 'devicePixelRatio', { value: 3, configurable: true })
    try {
      expect(minimapIdentity(plain).pixelRatio).toBe(3)
      expect(minimapCacheKey(minimapIdentity(plain))).not.toBe(
        minimapCacheKey({ ...minimapIdentity(plain), pixelRatio: before }),
      )
    } finally {
      Object.defineProperty(globalThis, 'devicePixelRatio', { value: before, configurable: true })
    }
  })

  it('treats a browser that reports nothing usable as an ordinary one', () => {
    const before = globalThis.devicePixelRatio
    Object.defineProperty(globalThis, 'devicePixelRatio', { value: 0, configurable: true })
    try {
      expect(
        minimapIdentity({ shape: 'a'.repeat(64), deliveries: [], theme: 'outdoor' }).pixelRatio,
      ).toBe(1)
    } finally {
      Object.defineProperty(globalThis, 'devicePixelRatio', { value: before, configurable: true })
    }
  })
})

describe('the packages a coverage answer draws', () => {
  it('are named by their delivery identity, in the order they were given', () => {
    const answered = coverage({
      sources: [
        mapSource({ delivery_id: 'a'.repeat(64) }),
        mapSource({ delivery_id: 'b'.repeat(64) }),
      ],
    })

    expect(drawnDeliveries(answered)).toEqual(['a'.repeat(64), 'b'.repeat(64)])
  })

  it('are none at all when the archive holds no map for the track', () => {
    expect(drawnDeliveries(coverage())).toEqual([])
    expect(drawnDeliveries(null)).toEqual([])
  })

  it('change when a region is installed again from newer data', () => {
    // The whole invalidation story for maps: the same region, different bytes,
    // a different content hash -- so the picture drawn over the old package is
    // never found again, and nobody has to purge anything.
    const before = drawnDeliveries(coverage({ sources: [mapSource({ delivery_id: 'a'.repeat(64) })] }))
    const after = drawnDeliveries(coverage({ sources: [mapSource({ delivery_id: 'e'.repeat(64) })] }))

    expect(after).not.toEqual(before)
  })
})
