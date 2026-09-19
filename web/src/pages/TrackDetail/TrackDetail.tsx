import { Link, useParams } from 'react-router-dom'
import { TrackReport } from './TrackReport'

/**
 * One track, on a page of its own.
 *
 * The page is the address, the back link and nothing else: what it shows is
 * `TrackReport`, which is the same component a row in the track list opens. A
 * second rendering of a track here would be a second answer to every question
 * the report answers, and they would drift.
 *
 * It is also the one place the report can be rearranged. A row in the list
 * draws the same arrangement and offers no editing: arranging a page inside a
 * list of other things is arranging something nobody is looking at whole.
 */
export function TrackDetail() {
  const { trackId } = useParams()
  return (
    <>
      <p className="muted">
        {/*
          Every kind, said out loud, because this link means what it says. The
          browser lists recordings when the address does not narrow it, and a
          link out of a planned route's page that quietly dropped it would be
          the one place the word "all" was not true.
        */}
        <Link to="/tracks?kind=all">← All tracks</Link>
      </p>
      <TrackReport trackId={Number(trackId)} customizable />
    </>
  )
}
