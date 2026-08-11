import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type MapCoverage } from '../api/client'
import { describe } from '../api/useRequest'

/**
 * What to do about a track with no map behind it.
 *
 * "No offline map is installed for this area" is true and unhelpful: it leaves
 * a reader to work out which of five hundred regions their own track is in, in
 * a catalog organised by administrative hierarchy rather than by where they
 * walked. The archive knows where the track went and what the provider says its
 * regions occupy, so it names the ones that would fill the gap.
 *
 * **Offered, never installed.** A region is hundreds of megabytes. Fetching one
 * because somebody opened a page would be the background download this project
 * refuses everywhere else, and the reader is the one who knows whether they
 * want the province or the country.
 *
 * **Several, never one.** The provider's extents are rectangles around
 * outlines, and a rectangle around the Netherlands contains Aachen. The
 * alternatives are on screen, each with the regions above it, so the wrong
 * country is visible before the gigabyte rather than after it.
 *
 * Progress lives in the map manager and is not duplicated here. One owner for
 * "how far has this download got" is worth more than saving a click.
 */
export function MapOffer({ coverage }: { coverage: MapCoverage }) {
  const [started, setStarted] = useState<string | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [installing, setInstalling] = useState<string | null>(null)

  if (coverage.suggestions.length === 0) {
    return coverage.catalog_known ? (
      <p className="notice" role="status" data-testid="no-offline-map">
        No offline map is installed for this area. The track is drawn over a neutral background.{' '}
        <Link to="/maps">Manage offline maps</Link>
      </p>
    ) : (
      <p className="notice" role="status" data-testid="offer-no-catalog">
        No offline map is installed for this area, and this archive has not read the region catalog
        yet — so it cannot say which one would cover this track.{' '}
        <Link to="/maps">Open offline maps</Link> to load it.
      </p>
    )
  }

  const install = async (regionId: string, name: string) => {
    setInstalling(regionId)
    setFailed(null)
    try {
      await api.installMap(regionId)
      setStarted(name)
    } catch (cause: unknown) {
      setFailed(describe(cause))
    } finally {
      setInstalling(null)
    }
  }

  return (
    <div className="notice map-offer" data-testid="map-offer">
      <p>
        No offline map is installed for this area. These regions cover where this track went — the
        archive matched the track against what the provider says each region occupies, so pick the
        one you actually walked in.
      </p>
      <ul className="map-offer__regions">
        {coverage.suggestions.map((region) => (
          <li key={region.region_id} data-testid="offered-region">
            <span className="map-offer__name">{region.name}</span>
            {region.ancestry.length > 0 && (
              <span className="muted"> — {region.ancestry.join(' / ')}</span>
            )}
            <span className="muted"> · {describeSize(region)}</span>
            <button
              type="button"
              disabled={installing !== null || started !== null}
              onClick={() => {
                void install(region.region_id, region.name)
              }}
            >
              {installing === region.region_id ? 'Starting…' : 'Download'}
            </button>
          </li>
        ))}
      </ul>
      {started !== null && (
        <p role="status" data-testid="offer-started">
          Downloading {started}. It is hundreds of megabytes and keeps going while you carry on
          reading; <Link to="/maps">Offline maps</Link> shows how far it has got.
        </p>
      )}
      {failed !== null && (
        <p role="alert" data-testid="offer-error">
          {failed}
        </p>
      )}
    </div>
  )
}

/**
 * What a download would cost, or that nobody has asked yet.
 *
 * An unknown size is said rather than left blank or guessed at: the provider is
 * asked about a region when somebody browses to it, and a region reached by
 * suggestion may never have been asked about.
 */
function describeSize(region: MapCoverage['suggestions'][number]): string {
  if (region.size_bytes === null) {
    return region.availability_known ? 'size unknown' : 'size not checked yet'
  }
  const megabytes = region.size_bytes / 1_000_000
  return megabytes >= 1000
    ? `${(megabytes / 1000).toFixed(1)} GB`
    : `${Math.round(megabytes)} MB`
}
