import { api } from '../api/client'
import { useRequest } from '../api/useRequest'

/**
 * What this deployment is, for whoever has to report a problem with it.
 *
 * Deliberately small. It answers "which release am I looking at, and is the
 * archive answering at all", and the algorithm versions underneath answer "why
 * did this number change" without anybody having to find a changelog. It is not
 * a third-party licence list -- the container ships one of those as
 * `/app/THIRD_PARTY_NOTICES.md`, which is where an operator will look for it.
 */
export function About() {
  const info = useRequest((signal) => api.readSystemInfo(signal), [])

  if (info.error !== null) {
    return (
      <div className="panel" role="alert">
        <h1>About TrackVault</h1>
        <p className="notice notice--error">{info.error}</p>
        <button type="button" onClick={info.reload} data-testid="retry">
          Try again
        </button>
      </div>
    )
  }
  if (info.data === null) {
    return (
      <p className="muted" role="status">
        Loading…
      </p>
    )
  }

  return (
    <div className="panel">
      <h1>About TrackVault</h1>
      <dl className="definition">
        <dt>Version</dt>
        <dd data-testid="about-version">{info.data.version}</dd>
        <dt>Backend</dt>
        <dd data-testid="about-status">Answering</dd>
        <dt>Database schema</dt>
        <dd>{info.data.schema_version}</dd>
        <dt>Period timezone</dt>
        <dd>{info.data.timezone}</dd>
      </dl>

      <details className="technical">
        <summary>Which algorithms produced the numbers</summary>
        <dl className="definition">
          <dt>Distance</dt>
          <dd>
            {info.data.analysis.distance_algorithm} v{info.data.analysis.distance_algorithm_version}
          </dd>
          <dt>Movement</dt>
          <dd>
            {info.data.analysis.movement_algorithm} v{info.data.analysis.movement_algorithm_version}
          </dd>
          <dt>Elevation</dt>
          <dd>
            {info.data.analysis.elevation_algorithm} v
            {info.data.analysis.elevation_algorithm_version}
          </dd>
          {info.data.processing.map((profile) => (
            <div key={profile.importer} className="contents">
              <dt>Import ({profile.importer})</dt>
              <dd>
                importer v{profile.importer_version}, classifier {profile.classifier} v
                {profile.classifier_version}
              </dd>
            </div>
          ))}
        </dl>
      </details>

      <p className="muted">
        TrackVault assumes a trusted network: it has no authentication, and it should be reachable
        only from machines you trust or from behind a proxy that authenticates.
      </p>
    </div>
  )
}
