import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import { boundsOf, boundsQuery, toFeatureCollection } from './geojson'
import {
  MINIMAP_HEIGHT,
  MINIMAP_POINTS,
  MINIMAP_THEME,
  MINIMAP_WIDTH,
  renderTrackMinimap,
} from './minimap'
import { basemapStyle, requiredAttribution } from './style'

const PRELOAD_MARGIN = '400px'
/**
 * How far ahead of the viewport a preview starts working.
 *
 * Far enough that scrolling at a normal speed finds a drawn map rather than an
 * empty box, near enough that opening a filtered page does not render
 * twenty-five maps nobody looked at.
 */

/**
 * Where one track went, beside its row.
 *
 * The archive is asked for three things and only once somebody has scrolled to
 * the row: a reduced shape, the coverage installed for *that track's own*
 * extent, and nothing else. Coverage comes from the same authority the detail
 * page uses, so a row and the page it links to never disagree about which map
 * belongs behind a track.
 *
 * What it draws is a picture, not a map component -- see `minimap.ts` for why
 * that is the whole design. A row that cannot be drawn keeps its box and says
 * nothing: a track with no positions, or an archive that could not answer, is
 * not an error a reader of a list has to act on, and the row's numbers and its
 * link are unaffected either way.
 */
export function TrackMinimap({
  trackId,
  title,
  onAttribution,
}: {
  trackId: number
  title: string
  /** What the drawn basemap requires, reported so the page can credit it once. */
  onAttribution?: (lines: readonly string[]) => void
}) {
  const frame = useRef<HTMLDivElement | null>(null)
  const [wanted, setWanted] = useState(false)
  const [image, setImage] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState(false)

  // Held in a ref rather than in the effect's dependencies: what has to be
  // credited does not change because a parent re-rendered, and a caller that
  // passes a fresh closure must not cost this row a second render of its map.
  const credit = useRef(onAttribution)
  useEffect(() => {
    credit.current = onAttribution
  }, [onAttribution])

  useEffect(() => {
    const node = frame.current
    if (node === null) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setWanted(true)
          observer.disconnect()
        }
      },
      { rootMargin: PRELOAD_MARGIN },
    )
    observer.observe(node)
    return () => {
      observer.disconnect()
    }
  }, [])

  useEffect(() => {
    if (!wanted) return
    // The one fact about whether this row still wants anything. A second flag
    // beside it would be a second answer to the same question, and the request
    // this hands to the archive already carries it.
    const controller = new AbortController()
    const gone = () => controller.signal.aborted

    const draw = async () => {
      const shape = await api.readGeometry(trackId, MINIMAP_POINTS, controller.signal)
      const collection = toFeatureCollection(shape)
      const bounds = boundsOf(collection)
      if (bounds === null) {
        // A track with no positions has no rectangle, so there is nothing to
        // ask coverage about and nothing to draw. It is a state, not a failure.
        setUnavailable(true)
        return
      }
      const covered = await api.readCoverage(boundsQuery(bounds), controller.signal)
      if (gone()) return
      credit.current?.(requiredAttribution(covered))
      const drawn = await renderTrackMinimap({
        collection,
        bounds,
        style: basemapStyle(covered, MINIMAP_THEME),
      })
      if (gone()) return
      setImage(drawn)
    }

    draw().catch(() => {
      if (!gone()) setUnavailable(true)
    })

    return () => {
      controller.abort()
    }
  }, [wanted, trackId])

  return (
    <div
      ref={frame}
      className="track-row__minimap"
      data-testid="track-minimap"
      data-state={image !== null ? 'drawn' : unavailable ? 'unavailable' : 'pending'}
      style={{ width: MINIMAP_WIDTH, height: MINIMAP_HEIGHT }}
    >
      {image !== null && (
        <img src={image} width={MINIMAP_WIDTH} height={MINIMAP_HEIGHT} alt={`Where ${title} went`} />
      )}
    </div>
  )
}
