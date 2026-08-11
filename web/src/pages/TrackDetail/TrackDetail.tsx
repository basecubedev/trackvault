import { Link, useParams } from 'react-router-dom'
import { TrackReport } from './TrackReport'

/**
 * One track, on a page of its own.
 *
 * The page is the address, the back link and nothing else: what it shows is
 * `TrackReport`, which is the same component a row in the track list opens. A
 * second rendering of a track here would be a second answer to every question
 * the report answers, and they would drift.
 */
export function TrackDetail() {
  const { trackId } = useParams()
  return (
    <>
      <p className="muted">
        <Link to="/tracks">← All tracks</Link>
      </p>
      <TrackReport trackId={Number(trackId)} />
    </>
  )
}
