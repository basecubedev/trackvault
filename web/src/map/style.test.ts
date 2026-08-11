import { describe, expect, it } from 'vitest'
import { coverage, mapAttribution, mapSource } from '../test-fixtures'
import { basemapStyle, requiredAttribution } from './style'

/**
 * What the style composed from installed coverage must and must not contain.
 *
 * The first group is the offline contract, checked at the one place a host
 * could ever enter the page: there is exactly one function in this application
 * that writes a URL into a MapLibre style, and these are its rules.
 */

const OTHER_SOURCE = mapSource({
  region_id: 'geofabrik:europe/germany',
  region_name: 'Germany',
  source_id: 'map-germany',
  tiles_url: '/api/v1/maps/tiles/def456/{z}/{x}/{y}.mvt',
  bounds: { min_longitude: 5.8, min_latitude: 47.2, max_longitude: 15.1, max_latitude: 55.1 },
})

describe('a style built from installed coverage', () => {
  it('reaches nothing outside this origin', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'outdoor')

    const serialised = JSON.stringify(style)
    for (const external of ['openstreetmap.org', 'geofabrik.de', 'unpkg', 'jsdelivr', 'fonts.g']) {
      expect(serialised).not.toContain(external)
    }
    const hosts = [...serialised.matchAll(/https?:\/\/([^"/]+)/g)].map((match) => match[1])
    expect(new Set(hosts)).toEqual(new Set([window.location.host]))
  })

  it('loads its glyphs from this archive', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'outdoor')

    expect(style.glyphs).toBe('/fonts/{fontstack}/{range}.pbf')
  })

  it('ships no sprite, because no layer draws an icon', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'outdoor')

    expect(style).not.toHaveProperty('sprite')
    expect(JSON.stringify(style)).not.toContain('icon-image')
  })

  it('bounds each source to what its package actually covers', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'outdoor')

    const source = style.sources['map-abc123def456'] as { bounds: number[] }
    expect(source.bounds).toEqual([7.4, 43.48, 7.6, 43.76])
  })

  it('carries the package attribution into the source MapLibre credits', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'outdoor')

    const source = style.sources['map-abc123def456'] as { attribution: string }
    expect(source.attribution).toBe('Map data © OpenStreetMap contributors')
  })

  it('draws every source when a track needs more than one', () => {
    const style = basemapStyle(
      coverage({ sources: [mapSource(), OTHER_SOURCE], any_installed: true }),
      'outdoor',
    )

    expect(Object.keys(style.sources)).toHaveLength(2)
    expect(style.layers.filter((layer) => layer['source'] === 'map-germany').length).toBeGreaterThan(
      0,
    )
  })

  it('puts the most specific source on top', () => {
    // The archive answers most-specific-first because that is how a reader
    // would list them; drawing order is the other way round.
    const style = basemapStyle(
      coverage({ sources: [mapSource(), OTHER_SOURCE], any_installed: true }),
      'outdoor',
    )

    const ids = style.layers.map((layer) => String(layer['id']))
    const specific = ids.findIndex((id) => id.startsWith('map-abc123def456-place-labels'))
    const general = ids.findIndex((id) => id.startsWith('map-germany-place-labels'))
    expect(specific).toBeGreaterThan(general)
  })

  it('draws paths, minor roads and major roads separately', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'outdoor')

    const ids = style.layers.map((layer) => String(layer['id']))
    expect(ids).toContain('map-abc123def456-paths')
    expect(ids).toContain('map-abc123def456-minor-roads')
    expect(ids).toContain('map-abc123def456-major-roads')
  })

  it('draws labels from the one font stack the archive ships glyphs for', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'outdoor')

    const fonts = style.layers
      .filter((layer) => layer['type'] === 'symbol')
      .map((layer) => (layer['layout'] as Record<string, unknown>)['text-font'])
    expect(fonts).toContainEqual(['Noto Sans Regular'])
    expect(fonts).toContainEqual(['Noto Sans Bold'])
  })
})

describe('a style with nothing behind it', () => {
  it('is a neutral background when no package covers the track', () => {
    const style = basemapStyle(coverage({ any_installed: true }), 'outdoor')

    expect(style.sources).toEqual({})
    expect(style.layers).toHaveLength(1)
    expect(style.layers[0]?.['type']).toBe('background')
  })

  it('is a neutral background when the reader asks for no basemap', () => {
    const style = basemapStyle(coverage({ sources: [mapSource()], any_installed: true }), 'none')

    expect(style.sources).toEqual({})
  })

  it('is a neutral background before coverage has been answered', () => {
    expect(basemapStyle(null, 'outdoor').sources).toEqual({})
  })
})

describe('the attribution a set of sources requires', () => {
  it('is deduplicated across packages from one provider', () => {
    const lines = requiredAttribution(
      coverage({ sources: [mapSource(), OTHER_SOURCE], any_installed: true }),
    )

    expect(lines).toEqual(['Map data © OpenStreetMap contributors'])
  })

  it("keeps a second provider's own words", () => {
    const other = mapSource({
      source_id: 'map-other',
      attribution: mapAttribution({
        data_owner: 'Somebody else',
        required_text: 'Map data © Somebody else',
      }),
    })

    const lines = requiredAttribution(coverage({ sources: [mapSource(), other] }))

    expect(lines).toHaveLength(2)
  })
})
