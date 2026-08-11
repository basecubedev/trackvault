import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api,
  type InstalledMap,
  type MapCatalogEntry,
  type MapJob,
} from '../../api/client'
import { formatBytes, formatInstant } from '../../api/format'
import { useRequest } from '../../api/useRequest'
import { Badge } from '../../components/Badge'
import { installFailure, jobProgress, stateLabel } from './language'

/**
 * Installing, updating and removing the maps this archive draws with.
 *
 * The one page in the application that reaches the internet, and it does so
 * only when somebody presses something. Everything else -- the installed list,
 * a track's basemap -- is answered from what is already here.
 *
 * Three things the layout is arranged around.
 *
 * **A download is not a page load.** Installing answers immediately with a job;
 * the page polls it and stays usable, so a reader can go and look at a track
 * while eight hundred megabytes arrive.
 *
 * **Progress is text before it is a bar.** "324 MB of 810 MB · 40 %" is
 * readable by a screen reader, by somebody who cannot see the bar and by
 * somebody photographing the screen for a bug report. When the provider
 * declared no size there is no percentage, and the page says the byte count
 * rather than inventing one.
 *
 * **A catalog failure is not a data failure.** The provider being unreachable
 * is a sentence at the top of the available list. The installed maps below it
 * keep working and keep saying so.
 */

const POLL_INTERVAL_MS = 1500

export function MapManager() {
  const [tick, setTick] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)

  const installed = useRequest((signal) => api.readInstalledMaps(signal), [tick])
  const jobs = useRequest((signal) => api.readMapJobs(signal), [tick])
  const catalog = useRequest((signal) => api.readMapCatalog(open, signal), [open, tick])

  const active = useMemo(
    () => (jobs.data?.jobs ?? []).filter((job) => !isFinished(job)),
    [jobs.data],
  )

  // Polling only while something is running. A page that polls forever is a
  // request per second for the lifetime of a browser tab.
  useEffect(() => {
    if (active.length === 0) return undefined
    const timer = window.setInterval(() => {
      setTick((value) => value + 1)
    }, POLL_INTERVAL_MS)
    return () => {
      window.clearInterval(timer)
    }
  }, [active.length])

  const run = useCallback(async (key: string, action: () => Promise<unknown>) => {
    setBusy(key)
    setFailure(null)
    try {
      await action()
      setTick((value) => value + 1)
    } catch (cause) {
      setFailure(installFailure(cause))
    } finally {
      setBusy(null)
    }
  }, [])

  const installsEnabled = installed.data?.installs_enabled ?? false

  return (
    <div className="stack">
      <section className="panel">
        <h1>Offline maps</h1>
        <p className="muted">
          A regional map is downloaded once and stored in this deployment. After that, looking at a
          track draws its background from here and sends nothing to anybody.
        </p>
        {!installsEnabled && installed.data !== null && (
          <p className="notice" role="status" data-testid="installs-disabled">
            Installing maps is switched off for this deployment. Maps already installed keep
            working.
          </p>
        )}
        {failure !== null && (
          <p className="notice notice--error" role="alert" data-testid="map-error">
            {failure}
          </p>
        )}
      </section>

      {active.length > 0 && (
        <section className="panel" aria-live="polite">
          <h2>In progress</h2>
          <ul className="map-jobs">
            {active.map((job) => (
              <li key={job.job_id} data-testid="map-job">
                <strong>{job.region_name}</strong>
                <span className="map-job__state"> {stateLabel(job.state).text}</span>
                <p className="map-job__progress" data-testid="map-job-progress">
                  {jobProgress(job)}
                </p>
                <progress
                  max={job.bytes_total ?? undefined}
                  value={job.bytes_total === null ? undefined : job.bytes_downloaded}
                  aria-label={`${job.region_name}: ${jobProgress(job)}`}
                />
                <button
                  type="button"
                  onClick={() => void run(job.job_id, () => api.cancelMapJob(job.job_id))}
                  disabled={busy === job.job_id}
                >
                  Cancel
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel">
        <h2>Installed</h2>
        {installed.error !== null && (
          <p className="notice notice--error" role="alert">
            {installed.error}
          </p>
        )}
        {installed.data !== null && installed.data.maps.length === 0 && (
          <p className="muted" data-testid="no-maps-installed">
            No map is installed yet. Tracks draw over a neutral background until one is.
          </p>
        )}
        {(installed.data?.maps ?? []).map((entry) => (
          <InstalledCard
            key={entry.region_id}
            entry={entry}
            busy={busy === entry.region_id}
            enabled={installsEnabled}
            confirming={confirming === entry.region_id}
            onUpdate={() => void run(entry.region_id, () => api.installMap(entry.region_id))}
            onAskRemove={() => {
              setConfirming(entry.region_id)
            }}
            onCancelRemove={() => {
              setConfirming(null)
            }}
            onRemove={() => {
              setConfirming(null)
              void run(entry.region_id, () => api.removeMap(entry.region_id))
            }}
          />
        ))}
        {(installed.data?.maps.length ?? 0) > 0 && (
          <p className="muted" data-testid="maps-total-size">
            {formatBytes(installed.data?.total_size_bytes)} of map data stored here.
          </p>
        )}
      </section>

      <section className="panel">
        <h2>Available</h2>
        {catalog.error !== null && (
          <p className="notice notice--error" role="alert" data-testid="catalog-unavailable">
            Map catalog currently unavailable. Installed maps continue to work.
          </p>
        )}
        {catalog.data !== null && !catalog.data.provider_available && (
          <p className="notice" role="status" data-testid="catalog-stale">
            The provider could not be reached. This list is the one last read
            {catalog.data.fetched_at === null
              ? '.'
              : ` on ${formatInstant(catalog.data.fetched_at)}.`}
          </p>
        )}
        {catalog.data !== null && (
          <>
            <nav className="map-breadcrumb" aria-label="Catalog level">
              <button
                type="button"
                onClick={() => {
                  setOpen(null)
                }}
                disabled={open === null}
              >
                All regions
              </button>
              {open !== null && <span> / {open.split(':')[1]}</span>}
            </nav>
            <ul className="map-catalog">
              {catalog.data.entries.map((entry) => (
                <CatalogRow
                  key={entry.region_id}
                  entry={entry}
                  busy={busy === entry.region_id}
                  enabled={installsEnabled}
                  onOpen={() => {
                    setOpen(entry.region_id)
                  }}
                  onInstall={() =>
                    void run(entry.region_id, () => api.installMap(entry.region_id))
                  }
                />
              ))}
            </ul>
            <p className="muted">
              Regions come from {catalog.data.provider_name}. Only the ones it publishes a package
              for can be installed.
            </p>
          </>
        )}
        {catalog.loading && catalog.data === null && (
          <p className="muted" role="status" data-testid="catalog-loading">
            Reading the catalog…
          </p>
        )}
      </section>
    </div>
  )
}

function InstalledCard({
  entry,
  busy,
  enabled,
  confirming,
  onUpdate,
  onAskRemove,
  onCancelRemove,
  onRemove,
}: {
  entry: InstalledMap
  busy: boolean
  enabled: boolean
  confirming: boolean
  onUpdate: () => void
  onAskRemove: () => void
  onCancelRemove: () => void
  onRemove: () => void
}) {
  return (
    <article className="map-card" data-testid="installed-map">
      <header className="map-card__header">
        <h3>{entry.region_name}</h3>
        <Badge label={stateLabel(entry.state)} />
      </header>
      {entry.state !== 'installed' && (
        <p className="notice notice--error" role="alert" data-testid="map-invalid">
          Map package is invalid. Reinstall or update it.
        </p>
      )}
      <dl className="definition">
        <dt>Size</dt>
        <dd>{formatBytes(entry.size_bytes)}</dd>
        <dt>Dataset</dt>
        <dd>{formatInstant(entry.dataset_timestamp)}</dd>
        <dt>Installed</dt>
        <dd>{formatInstant(entry.downloaded_at)}</dd>
        <dt>Licence</dt>
        <dd>{entry.attribution.license_name}</dd>
      </dl>
      <p className="map-card__credit" data-testid="map-card-attribution">
        {entry.attribution.required_text}
        {' — packaged by '}
        {entry.attribution.provider}
      </p>
      {entry.attribution.links.length > 0 && (
        <p className="map-card__links">
          {entry.attribution.links.map((link) => (
            <a key={link.url} href={link.url} rel="noreferrer noopener" target="_blank">
              {link.label}
            </a>
          ))}
        </p>
      )}
      <div className="map-card__actions">
        <button type="button" onClick={onUpdate} disabled={busy || !enabled}>
          Update
        </button>
        {confirming ? (
          <>
            <span className="muted">
              Remove {entry.region_name}? It can be downloaded again later.
            </span>
            <button type="button" onClick={onRemove} data-testid="confirm-remove">
              Remove
            </button>
            <button type="button" onClick={onCancelRemove}>
              Keep
            </button>
          </>
        ) : (
          <button type="button" onClick={onAskRemove} disabled={busy}>
            Remove
          </button>
        )}
      </div>
    </article>
  )
}

function CatalogRow({
  entry,
  busy,
  enabled,
  onOpen,
  onInstall,
}: {
  entry: MapCatalogEntry
  busy: boolean
  enabled: boolean
  onOpen: () => void
  onInstall: () => void
}) {
  return (
    <li className="map-catalog__row" data-testid="catalog-row">
      <span className="map-catalog__name">{entry.name}</span>
      <span className="map-catalog__size">
        {entry.availability_known
          ? entry.installable
            ? formatBytes(entry.package?.size_bytes)
            : 'No package'
          : 'Size unavailable'}
      </span>
      <span className="map-catalog__actions">
        {entry.has_children && (
          <button type="button" onClick={onOpen}>
            Open
          </button>
        )}
        {entry.installable && !entry.installed && (
          <button type="button" onClick={onInstall} disabled={busy || !enabled}>
            Download
          </button>
        )}
        {entry.installed && <span className="muted">Installed</span>}
      </span>
    </li>
  )
}

function isFinished(job: MapJob): boolean {
  return ['completed', 'failed', 'interrupted', 'cancelled'].includes(job.state)
}
