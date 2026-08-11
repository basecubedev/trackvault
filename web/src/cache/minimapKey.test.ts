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
 */

const IDENTITY: MinimapIdentity = {
  geometry: 'a'.repeat(64),
  segments: 1,
  deliveries: ['b'.repeat(64)],
  theme: 'outdoor',
  styleVersion: 1,
  renderVersion: 1,
  width: 132,
  height: 96,
  points: 150,
  maxZoom: 14,
  padding: 6,
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
    expect(minimapCacheKey(IDENTITY)).toContain('trackvault-minimap:v1')
    expect(minimapCacheKey(IDENTITY)).toContain(`geometry=${IDENTITY.geometry}`)
  })
})

describe('a changed input', () => {
  it.each([
    ['the geometry the archive reports', { geometry: 'c'.repeat(64) }],
    ['the number of segments drawn', { segments: 2 }],
    ['the packages drawn under it', { deliveries: ['d'.repeat(64)] }],
    ['a second package appearing', { deliveries: ['b'.repeat(64), 'd'.repeat(64)] }],
    ['the theme', { theme: 'light' as const }],
    ['the style version', { styleVersion: 2 }],
    ['the render version', { renderVersion: 2 }],
    ['the width', { width: 133 }],
    ['the height', { height: 97 }],
    ['the position budget', { points: 200 }],
    ['the zoom ceiling', { maxZoom: 15 }],
    ['the padding', { padding: 7 }],
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
    // Every part is named and separated. Without that, a geometry ending in a
    // digit and a segment count could compose into the same string as another
    // pair -- a collision that is invisible until it shows the wrong track.
    const shifted = differing({ geometry: `${'a'.repeat(63)}1`, segments: 11 })
    const other = differing({ geometry: `${'a'.repeat(63)}11`, segments: 1 })

    expect(shifted).not.toBe(other)
  })
})

describe('the shared part of an identity', () => {
  it('is read from the modules that draw with it', () => {
    const identity = minimapIdentity({
      geometry: 'a'.repeat(64),
      segments: 1,
      deliveries: [],
      theme: 'outdoor',
    })

    // Not asserted as numbers: the point is that a caller supplies none of
    // them, so no row can leave a rendering parameter out of its own key.
    expect(identity.width).toBeGreaterThan(0)
    expect(identity.height).toBeGreaterThan(0)
    expect(identity.points).toBeGreaterThan(1)
    expect(identity.maxZoom).toBeGreaterThan(0)
    expect(identity.styleVersion).toBeGreaterThan(0)
    expect(identity.renderVersion).toBeGreaterThan(0)
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
