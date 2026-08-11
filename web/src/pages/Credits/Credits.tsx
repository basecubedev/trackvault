import { api } from '../../api/client'
import { useRequest } from '../../api/useRequest'

/**
 * Who this application is built on, and whose data it draws.
 *
 * Deliberately not a licence list. `THIRD_PARTY_NOTICES.md` ships in the image
 * and is the complete distribution record -- versions, licence identifiers,
 * every direct dependency of both ecosystems. This page has a different job: it
 * is the acknowledgement, in words, of the people and projects that made a
 * self-hosted map possible. Two different purposes, and collapsing them would
 * make one of them worse.
 *
 * The map half is read from the packages that are actually installed rather
 * than written here, so a second provider appears the day somebody installs one
 * of its regions and nobody has to remember to edit this file.
 */
export function Credits() {
  const credits = useRequest((signal) => api.readMapCredits(signal), [])

  return (
    <div className="stack">
      <section className="panel">
        <h1>Credits</h1>
        <p className="muted">
          TrackVault is built on open data and open source. This page says thank you; the full
          distribution record is <code>THIRD_PARTY_NOTICES.md</code>, which ships beside the
          application.
        </p>
      </section>

      <section className="panel">
        <h2>Map data</h2>
        {credits.error !== null && (
          <p className="notice notice--error" role="alert">
            {credits.error}
          </p>
        )}
        {credits.data !== null && credits.data.map_data.length === 0 && (
          <p className="muted" data-testid="no-map-credits">
            No offline map is installed, so there is no map data to credit yet.
          </p>
        )}
        {(credits.data?.map_data ?? []).map((credit) => (
          <article key={`${credit.data_owner}/${credit.license_identifier}`} data-testid="map-credit">
            <h3>{credit.data_owner}</h3>
            <p>
              Packaged by {credit.provider}, under {credit.license_name} (
              {credit.license_identifier}).
            </p>
            <p className="muted">Installed regions: {credit.regions.join(', ')}</p>
            {credit.links.length > 0 && (
              <p>
                {credit.links.map((link) => (
                  <a key={link.url} href={link.url} rel="noreferrer noopener" target="_blank">
                    {link.label}
                  </a>
                ))}
              </p>
            )}
          </article>
        ))}
      </section>

      <section className="panel">
        <h2>Software</h2>
        <dl className="definition">
          <dt>Map rendering</dt>
          <dd>
            <a href="https://maplibre.org/" rel="noreferrer noopener" target="_blank">
              MapLibre GL JS
            </a>
          </dd>
          <dt>Tile schema</dt>
          <dd>
            <a href="https://shortbread-tiles.org/" rel="noreferrer noopener" target="_blank">
              Shortbread
            </a>
          </dd>
          <dt>Labels</dt>
          <dd>Noto Sans, under the SIL Open Font License 1.1</dd>
          <dt>Charts</dt>
          <dd>
            <a href="https://echarts.apache.org/" rel="noreferrer noopener" target="_blank">
              Apache ECharts
            </a>
          </dd>
          <dt>Web interface</dt>
          <dd>
            <a href="https://react.dev/" rel="noreferrer noopener" target="_blank">
              React
            </a>
            {', '}
            <a href="https://vite.dev/" rel="noreferrer noopener" target="_blank">
              Vite
            </a>
          </dd>
          <dt>Backend</dt>
          <dd>
            <a href="https://fastapi.tiangolo.com/" rel="noreferrer noopener" target="_blank">
              FastAPI
            </a>
            {', SQLite, '}
            <a href="https://pydantic.dev/" rel="noreferrer noopener" target="_blank">
              Pydantic
            </a>
          </dd>
        </dl>
      </section>
    </div>
  )
}
