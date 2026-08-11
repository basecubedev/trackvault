import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { on, stubArchive } from '../../test-fixtures'
import { Credits } from './Credits'

/**
 * The credits page acknowledges; it does not replace the notices file.
 *
 * The map half is derived from what is installed, so the interesting cases are
 * an archive with no maps at all and one whose packages come from a provider
 * nobody wrote into this page.
 */

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('the credits page', () => {
  it('acknowledges the data behind an installed map', async () => {
    stubArchive([
      on('/api/v1/maps/credits', {
        map_data: [
          {
            data_owner: 'OpenStreetMap contributors',
            provider: 'Geofabrik GmbH',
            license_identifier: 'ODbL-1.0',
            license_name: 'Open Database License 1.0',
            links: [{ label: 'OpenStreetMap', url: 'https://www.openstreetmap.org/copyright' }],
            regions: ['Monaco'],
          },
        ],
      }),
    ])

    render(<Credits />)

    const credit = await screen.findByTestId('map-credit')
    expect(credit).toHaveTextContent('OpenStreetMap contributors')
    expect(credit).toHaveTextContent('Packaged by Geofabrik GmbH')
    expect(credit).toHaveTextContent('Open Database License 1.0')
    expect(credit).toHaveTextContent('Monaco')
  })

  it('says there is nothing to credit rather than crediting nobody in particular', async () => {
    stubArchive([on('/api/v1/maps/credits', { map_data: [] })])

    render(<Credits />)

    expect(await screen.findByTestId('no-map-credits')).toHaveTextContent(
      /no offline map is installed/i,
    )
  })

  it('names the software the map is actually drawn with', async () => {
    stubArchive([on('/api/v1/maps/credits', { map_data: [] })])

    render(<Credits />)

    await screen.findByTestId('no-map-credits')
    for (const project of ['MapLibre GL JS', 'Shortbread', 'Apache ECharts', 'React', 'FastAPI']) {
      expect(screen.getByText(project)).toBeInTheDocument()
    }
    expect(screen.getByText(/Noto Sans/)).toHaveTextContent('SIL Open Font License 1.1')
  })

  it('points at the notices file rather than repeating it', async () => {
    stubArchive([on('/api/v1/maps/credits', { map_data: [] })])

    render(<Credits />)

    expect(await screen.findByText('THIRD_PARTY_NOTICES.md')).toBeInTheDocument()
  })
})
