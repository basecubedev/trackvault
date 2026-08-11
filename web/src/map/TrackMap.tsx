import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { MapCoverage } from '../api/client'
import type { HoverStore } from '../pages/TrackDetail/hover'
import { boundsOf, type FeatureCollection } from './geojson'
import { type Positioned, sampleUnderPointer } from './hover'
import { basemapStyle, type MapTheme } from './style'

/**
 * The track on a map, with or without a basemap behind it.
 *
 * The basemap comes from packages installed in this deployment and is served by
 * it: there is no provider to configure and no request that leaves the origin.
 * With nothing installed for the area -- or with the theme set to none -- the
 * map draws the geometry over a neutral background and stays useful, because
 * the positions are the part that belongs to the owner anyway.
 *
 * Nothing here re-renders on a mouse move. The hover marker is moved through
 * MapLibre's own API from a subscription, because a React state update per
 * pointer event re-renders the whole detail page to move one dot.
 *
 * Everything decidable without WebGL -- the GeoJSON, the bounds, the
 * antimeridian, which sample the pointer is on -- lives in `geojson.ts` and
 * `hover.ts` and is tested there. This is the mount.
 */
export function TrackMap({
  collection,
  samples,
  coverage,
  theme,
  hover,
}: {
  collection: FeatureCollection
  /** The samples the pointer can land on, in the order the chart draws them. */
  samples: readonly Positioned[]
  /** Which installed packages belong behind this track, or `null` for none. */
  coverage: MapCoverage | null
  theme: MapTheme
  hover: HoverStore
}) {
  const container = useRef<HTMLDivElement | null>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const marker = useRef<maplibregl.Marker | null>(null)
  const positions = useRef(samples)
  positions.current = samples
  const [basemapFailed, setBasemapFailed] = useState(false)
  const style = useMemo(() => basemapStyle(coverage, theme), [coverage, theme])

  useEffect(() => {
    if (!container.current) return
    const instance = new maplibregl.Map({
      container: container.current,
      // A style object rather than a URL. It is composed from what this
      // archive says is installed, so there is no document to fetch and no
      // address that could point anywhere else.
      style: style as maplibregl.StyleSpecification,
      // Compact, but never removed: the attribution a style declares is the
      // provider's requirement rather than a decoration.
      attributionControl: { compact: true },
    })
    instance.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')

    const draw = () => {
      if (instance.getSource('track')) return
      instance.addSource('track', { type: 'geojson', data: collection })
      instance.addLayer({
        id: 'track-casing',
        type: 'line',
        source: 'track',
        paint: { 'line-color': '#ffffff', 'line-width': 6, 'line-opacity': 0.8 },
        layout: { 'line-join': 'round', 'line-cap': 'round' },
      })
      instance.addLayer({
        id: 'track-line',
        type: 'line',
        source: 'track',
        paint: { 'line-color': '#1c5d99', 'line-width': 3 },
        layout: { 'line-join': 'round', 'line-cap': 'round' },
      })
      const bounds = boundsOf(collection)
      // `maxZoom` matters for a short track: fitting a two-hundred-metre walk
      // without it puts the reader at building level with no context at all.
      if (bounds) instance.fitBounds(bounds, { padding: 40, duration: 0, maxZoom: 15 })
    }

    instance.on('load', draw)
    // A style swap wipes every layer, so the track is redrawn whenever a style
    // finishes loading -- including the fallback installed after a failure.
    instance.on('styledata', draw)

    instance.on('error', (event: { error?: { status?: number } }) => {
      // A package can be removed, or turn out to be damaged, while a page is
      // open. Losing it must cost the basemap and nothing else: the track, the
      // profile and the marker keep working over whatever is left.
      setBasemapFailed(true)
      void event
    })

    instance.on('mousemove', (event) => {
      hover.set(
        sampleUnderPointer(
          positions.current,
          { longitude: event.lngLat.lng, latitude: event.lngLat.lat },
          scaleOf(instance),
          hover.read(),
        ),
      )
    })
    instance.on('mouseout', () => {
      hover.set(null)
    })

    map.current = instance
    return () => {
      marker.current?.remove()
      marker.current = null
      instance.remove()
      map.current = null
    }
  }, [style, collection, hover])

  useEffect(() => {
    const instance = map.current
    if (!instance) return
    const source = instance.getSource<maplibregl.GeoJSONSource>('track')
    if (!source) return
    source.setData(collection)
    const bounds = boundsOf(collection)
    if (bounds) instance.fitBounds(bounds, { padding: 40, duration: 0, maxZoom: 15 })
  }, [collection])

  // The marker follows the hover imperatively. No React state is involved, so
  // moving the pointer along a track costs one `setLngLat` per frame rather
  // than a render of the page around it.
  useEffect(
    () =>
      hover.subscribe(() => {
        const instance = map.current
        if (!instance) return
        const index = hover.read()
        const sample = index === null ? undefined : positions.current[index]
        if (!sample) {
          marker.current?.remove()
          marker.current = null
          return
        }
        if (!marker.current) {
          const element = document.createElement('div')
          element.setAttribute('data-testid', 'profile-marker')
          element.className = 'map-marker'
          marker.current = new maplibregl.Marker({ element })
        }
        marker.current.setLngLat([sample.longitude, sample.latitude]).addTo(instance)
      }),
    [hover],
  )

  return (
    <>
      <div ref={container} className="map" data-testid="track-map" />
      {basemapFailed && (
        <p className="muted" role="status" data-testid="basemap-unavailable">
          Part of the installed map could not be read. The track is still drawn.
        </p>
      )}
    </>
  )
}

/**
 * How many degrees one screen pixel covers, at the current view.
 *
 * Read from the map rather than assumed, so "within twelve pixels of the
 * pointer" means the same thing at every zoom level. A tolerance fixed in
 * degrees would be a kilometre wide zoomed out and invisible zoomed in.
 */
function scaleOf(instance: maplibregl.Map): { longitude: number; latitude: number } {
  const bounds = instance.getBounds()
  const canvas = instance.getCanvas()
  const width = canvas.clientWidth || 1
  const height = canvas.clientHeight || 1
  return {
    longitude: Math.abs(bounds.getEast() - bounds.getWest()) / width,
    latitude: Math.abs(bounds.getNorth() - bounds.getSouth()) / height,
  }
}
