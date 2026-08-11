import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { coverage, mapJob, on, stubArchive, type Route } from '../test-fixtures'
import { MapOffer } from './MapOffer'

/**
 * What a track says when there is no map behind it.
 *
 * It used to say "no offline map is installed for this area", which is true and
 * useless: the reader still has to work out *which* of 555 regions their track
 * is in. The archive knows where the track went and where the regions are, so
 * it says which ones would fill the gap.
 *
 * The offer is several regions and never one. A catalog extent is a rectangle
 * around an outline and a rectangle around the Netherlands contains Aachen, so
 * the alternatives -- and where each one sits -- have to be on screen.
 */

const BALEARES = {
  region_id: 'geofabrik:europe/spain/islas-baleares',
  name: 'Islas Baleares',
  ancestry: ['Europe', 'Spain'],
  size_bytes: 96_000_000,
  availability_known: true,
}

const SPAIN = {
  region_id: 'geofabrik:europe/spain',
  name: 'Spain',
  ancestry: ['Europe'],
  size_bytes: 1_500_000_000,
  availability_known: true,
}

function show(routes: Route[], offered = coverage({ suggestions: [BALEARES, SPAIN], catalog_known: true })) {
  const stub = stubArchive(routes)
  render(
    <MemoryRouter>
      <MapOffer coverage={offered} />
    </MemoryRouter>,
  )
  return stub
}

/** The first Download button, once the offer has rendered. */
async function firstDownload(): Promise<HTMLElement> {
  const buttons = await screen.findAllByRole('button', { name: /Download/ })
  const first = buttons[0]
  if (first === undefined) throw new Error('the offer rendered no download button')
  return first
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a track with no map behind it', () => {
  it('offers the regions the archive says could cover it, most specific first', async () => {
    show([])

    const offered = await screen.findAllByTestId('offered-region')
    expect(offered.map((node) => node.textContent)).toEqual([
      expect.stringContaining('Islas Baleares'),
      expect.stringContaining('Spain'),
    ])
  })

  it('says where each region sits, so the wrong country is visible before the download', async () => {
    show([])

    const first = (await screen.findAllByTestId('offered-region'))[0]
    expect(first?.textContent).toContain('Europe / Spain')
  })

  it('says what a download would cost', async () => {
    show([])

    const first = (await screen.findAllByTestId('offered-region'))[0]
    expect(first?.textContent).toContain('96')
  })

  it('installs by naming a region and nothing else', async () => {
    const stub = show([on('/maps/install', mapJob())])

    await userEvent.click(await firstDownload())

    await waitFor(() => {
      expect(stub.writes).toHaveLength(1)
    })
    // A region, never an address. The endpoint has no field that could name a
    // host, and this is the caller that must not start inventing one.
    expect(stub.writes[0]?.body).toEqual({ region_id: BALEARES.region_id })
  })

  it('says where the download can be watched, rather than pretending to be the map manager', async () => {
    show([on('/maps/install', mapJob())])

    await userEvent.click(await firstDownload())

    await waitFor(() => {
      expect(screen.getByTestId('offer-started')).toBeInTheDocument()
    })
    expect(screen.getByRole('link', { name: /Offline maps/ })).toBeInTheDocument()
  })

  it('reports a refused download instead of looking like it worked', async () => {
    show([])

    await userEvent.click(await firstDownload())

    await waitFor(() => {
      expect(screen.getByTestId('offer-error')).toBeInTheDocument()
    })
  })

  it('says the catalog has not been read, which is not the same as having nothing to offer', async () => {
    show([], coverage({ suggestions: [], catalog_known: false }))

    await waitFor(() => {
      expect(screen.getByTestId('offer-no-catalog')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('offered-region')).not.toBeInTheDocument()
  })

  it('falls back to saying so plainly when the archive has nothing to suggest', async () => {
    show([], coverage({ suggestions: [], catalog_known: true }))

    await waitFor(() => {
      expect(screen.getByTestId('no-offline-map')).toBeInTheDocument()
    })
    expect(screen.queryByTestId('offered-region')).not.toBeInTheDocument()
  })
})
