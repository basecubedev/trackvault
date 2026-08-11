import { Suspense, lazy } from 'react'
import { Navigate, Route, BrowserRouter as Router, Routes } from 'react-router-dom'
import { Credits } from '../pages/Credits/Credits'
import { Dashboard } from '../pages/Dashboard/Dashboard'
import { MapManager } from '../pages/Maps/MapManager'
import { TrackBrowser } from '../pages/Tracks/TrackBrowser'
import { About } from './About'
import { Header } from './Header'

// Only the detail page draws a map, and the map library is the largest thing
// this application ships. Loading it when somebody opens a track keeps the two
// pages that do not need it from paying for it.
const TrackDetail = lazy(() =>
  import('../pages/TrackDetail/TrackDetail').then((module) => ({ default: module.TrackDetail })),
)

/**
 * The whole application, which is five pages and a header.
 *
 * Routing is client side and the server serves `index.html` for every path it
 * does not own, so a deep link to `/tracks/123` survives a refresh. What the
 * server does own -- `/api`, `/docs`, `/openapi.json`, `/healthz` -- is
 * answered by the server and never by this router.
 */
export function App() {
  return (
    <Router>
      <Header />
      <main id="content">
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/tracks" element={<TrackBrowser />} />
            <Route path="/tracks/:trackId" element={<TrackDetail />} />
            <Route path="/maps" element={<MapManager />} />
            <Route path="/credits" element={<Credits />} />
            <Route path="/about" element={<About />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </main>
    </Router>
  )
}
