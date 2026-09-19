import { describe, expect, it } from 'vitest'
import { derive, limitsOf, normalize, WIDTH_CLASSES } from '../../layout/document'
import { readingOrder, sameGrid } from '../../layout/grid'
import { DEFAULT_TRACK_LAYOUT, TRACK_WIDGETS } from './widgets'

describe('the widgets of a track report', () => {
  it('are named by keys the archive accepts', () => {
    for (const spec of TRACK_WIDGETS) {
      expect(spec.id).toMatch(/^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$/)
      expect(spec.id.length).toBeLessThanOrEqual(64)
    }
    expect(new Set(TRACK_WIDGETS.map((spec) => spec.id)).size).toBe(TRACK_WIDGETS.length)
  })

  it('keep their default size inside their own limits at every width', () => {
    for (const spec of TRACK_WIDGETS) {
      for (const widthClass of WIDTH_CLASSES) {
        const { min, max } = limitsOf(spec, widthClass)
        const { size } = spec.sizes[widthClass]
        expect(size.width, `${spec.id} ${widthClass}`).toBeGreaterThanOrEqual(min.width)
        expect(size.width, `${spec.id} ${widthClass}`).toBeLessThanOrEqual(max.width)
        expect(size.height, `${spec.id} ${widthClass}`).toBeGreaterThanOrEqual(min.height)
        expect(size.height, `${spec.id} ${widthClass}`).toBeLessThanOrEqual(max.height)
      }
    }
  })
})

describe('the default arrangement', () => {
  it('is already one the page can draw as it is, with every widget on it', () => {
    expect(sameGrid(normalize(DEFAULT_TRACK_LAYOUT, TRACK_WIDGETS, 'wide'), DEFAULT_TRACK_LAYOUT)).toBe(
      true,
    )
    expect(DEFAULT_TRACK_LAYOUT.tiles.map((tile) => tile.widget).sort()).toEqual(
      TRACK_WIDGETS.map((spec) => spec.id).sort(),
    )
  })

  it('reads in the order the report always had: numbers, shape, decisions, provenance', () => {
    const order = readingOrder(DEFAULT_TRACK_LAYOUT.tiles).map((tile) => tile.widget)

    expect(order.slice(0, 6).every((widget) => widget.startsWith('metric.'))).toBe(true)
    expect(order.slice(6)).toEqual([
      'map',
      'profile',
      'sensors',
      'classification',
      'source',
      'derivation',
    ])
  })

  it('gives a phone every panel at full width and lets only the small cards pair up', () => {
    const narrow = derive(DEFAULT_TRACK_LAYOUT, TRACK_WIDGETS, 'narrow')

    for (const tile of narrow.tiles) {
      expect(tile.width, tile.widget).toBe(tile.widget.startsWith('metric.') ? 1 : 2)
    }
  })
})
