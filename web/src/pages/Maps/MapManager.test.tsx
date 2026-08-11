import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  catalogEntry,
  failing,
  installedMap,
  installedMaps,
  mapCatalog,
  mapJob,
  on,
  stubArchive,
  type Route,
} from '../../test-fixtures'
import { jobProgress, installFailure } from './language'
import { MapManager } from './MapManager'
import { ApiError } from '../../api/client'

/**
 * What the map manager has to get right in a browser-shaped environment.
 *
 * The states that matter are the awkward ones: a provider that cannot be
 * reached while maps are installed, a region the provider offers no package
 * for, a download whose size nobody declared, and a package whose file has
 * gone missing underneath its row.
 */

function show(routes: Route[]) {
  stubArchive(routes)
  return render(
    <MemoryRouter>
      <MapManager />
    </MemoryRouter>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('an archive with no maps', () => {
  it('says so rather than showing an empty table', async () => {
    show([
      on('/api/v1/maps/catalog', mapCatalog()),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps()),
    ])

    expect(await screen.findByTestId('no-maps-installed')).toHaveTextContent(
      /no map is installed yet/i,
    )
  })
})

describe('an installed map', () => {
  const routes = [
    on('/api/v1/maps/catalog', mapCatalog()),
    on('/api/v1/maps/jobs', { jobs: [] }),
    on(
      '/api/v1/maps',
      installedMaps({ maps: [installedMap()], total_size_bytes: 1_720_320 }),
    ),
  ]

  it('shows what it is, where it came from and what it costs', async () => {
    show(routes)

    const card = await screen.findByTestId('installed-map')
    expect(card).toHaveTextContent('Monaco')
    expect(card).toHaveTextContent('1.7 MB')
    expect(card).toHaveTextContent('Open Database License 1.0')
    expect(card).toHaveTextContent('8 Aug 2026')
  })

  it('credits the data and the packager, from the package metadata', async () => {
    show(routes)

    expect(await screen.findByTestId('map-card-attribution')).toHaveTextContent(
      'Map data © OpenStreetMap contributors — packaged by Geofabrik GmbH',
    )
  })

  it('offers the licence as a link somebody can reach with a keyboard', async () => {
    show(routes)

    await screen.findByTestId('installed-map')
    const link = screen.getByRole('link', { name: 'OpenStreetMap' })
    expect(link).toHaveAttribute('href', 'https://www.openstreetmap.org/copyright')
  })

  it('asks before removing several hundred megabytes', async () => {
    show(routes)
    await screen.findByTestId('installed-map')

    await userEvent.click(screen.getByRole('button', { name: 'Remove' }))

    expect(screen.getByText(/can be downloaded again later/i)).toBeInTheDocument()
    expect(screen.getByTestId('confirm-remove')).toBeInTheDocument()
  })

  it('reports a package whose file has gone as invalid rather than installed', async () => {
    show([
      on('/api/v1/maps/catalog', mapCatalog()),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps({ maps: [installedMap({ state: 'invalid' })] })),
    ])

    expect(await screen.findByTestId('map-invalid')).toHaveTextContent(/reinstall or update/i)
  })
})

describe('the available list', () => {
  it('states a download size before anything is downloaded', async () => {
    show([
      on('/api/v1/maps/catalog', mapCatalog({ entries: [catalogEntry()] })),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps()),
    ])

    const row = await screen.findByTestId('catalog-row')
    expect(row).toHaveTextContent('Monaco')
    expect(row).toHaveTextContent('1.7 MB')
    expect(screen.getByRole('button', { name: 'Download' })).toBeEnabled()
  })

  it('says a region has no package rather than offering a download that would fail', async () => {
    show([
      on(
        '/api/v1/maps/catalog',
        mapCatalog({
          entries: [
            catalogEntry({
              region_id: 'geofabrik:europe/germany',
              name: 'Germany',
              installable: false,
              package: null,
              has_children: true,
            }),
          ],
        }),
      ),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps()),
    ])

    const row = await screen.findByTestId('catalog-row')
    expect(row).toHaveTextContent('No package')
    expect(screen.queryByRole('button', { name: 'Download' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument()
  })

  it('does not invent a size the provider never declared', async () => {
    show([
      on(
        '/api/v1/maps/catalog',
        mapCatalog({ entries: [catalogEntry({ availability_known: false, package: null })] }),
      ),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps()),
    ])

    expect(await screen.findByTestId('catalog-row')).toHaveTextContent('Size unavailable')
  })
})

describe('a provider that cannot be reached', () => {
  it('says the catalog is unavailable and that installed maps still work', async () => {
    show([
      failing('/api/v1/maps/catalog'),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps({ maps: [installedMap()] })),
    ])

    expect(await screen.findByTestId('catalog-unavailable')).toHaveTextContent(
      /installed maps continue to work/i,
    )
    expect(screen.getByTestId('installed-map')).toHaveTextContent('Monaco')
  })

  it('marks a cached catalog as the last one that was read', async () => {
    show([
      on(
        '/api/v1/maps/catalog',
        mapCatalog({ provider_available: false, entries: [catalogEntry()] }),
      ),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps()),
    ])

    expect(await screen.findByTestId('catalog-stale')).toHaveTextContent(/could not be reached/i)
  })
})

describe('a download in progress', () => {
  it('shows the byte count and the percentage as text', async () => {
    show([
      on('/api/v1/maps/catalog', mapCatalog()),
      on('/api/v1/maps/jobs', { jobs: [mapJob()] }),
      on('/api/v1/maps', installedMaps()),
    ])

    expect(await screen.findByTestId('map-job-progress')).toHaveTextContent(
      '324 MB of 810 MB · 40 %',
    )
  })

  it('offers a cancel button while it runs', async () => {
    show([
      on('/api/v1/maps/catalog', mapCatalog()),
      on('/api/v1/maps/jobs', { jobs: [mapJob()] }),
      on('/api/v1/maps', installedMaps()),
    ])

    await screen.findByTestId('map-job')
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled()
  })
})

describe('a deployment with installs switched off', () => {
  it('says so and offers no download button', async () => {
    show([
      on('/api/v1/maps/catalog', mapCatalog({ entries: [catalogEntry()] })),
      on('/api/v1/maps/jobs', { jobs: [] }),
      on('/api/v1/maps', installedMaps({ installs_enabled: false })),
    ])

    expect(await screen.findByTestId('installs-disabled')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Download' })).toBeDisabled()
    })
  })
})

describe('the words a failure is reported in', () => {
  it('tells a reader out of disk what is needed and that nothing was lost', () => {
    const message = installFailure(
      new ApiError(507, 'map_insufficient_disk_space', 'needs 900000000 bytes, 100 available'),
    )

    expect(message).toContain('Not enough free space')
    expect(message).toContain('untouched')
  })

  it('tells a reader whose download failed that their map is still installed', () => {
    expect(installFailure(new ApiError(400, 'map_download_failed', 'http 500'))).toContain(
      'still installed',
    )
  })

  it('never claims a percentage the provider gave no total for', () => {
    const text = jobProgress(mapJob({ bytes_total: null, percentage: null }))

    expect(text).toContain('total size unknown')
    expect(text).not.toContain('%')
  })
})
