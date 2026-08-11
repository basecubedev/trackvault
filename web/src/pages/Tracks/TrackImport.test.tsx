import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { on, stubArchive, systemInfo, type Route } from '../../test-fixtures'
import { TrackImport } from './TrackImport'

/**
 * Offering files to the archive from the browser.
 *
 * The interesting part is not the upload; it is what a reader is told
 * afterwards. Four things can happen to a file and three of them are not
 * "done": the archive already had it, the archive repaired its copy of it, or
 * the archive could not read it. A control that answered "uploaded" to all four
 * would be lying in the two cases somebody needs to act on.
 *
 * One request per file, so twenty files produce twenty verdicts rather than one.
 */

function gpx(name: string): File {
  return new File(['<gpx/>'], name, { type: 'application/gpx+xml' })
}

function show(routes: Route[], onImported = () => {}) {
  const stub = stubArchive(routes)
  render(<TrackImport enabled onImported={onImported} />)
  return stub
}

const IMPORTED = { status: 'imported', sha256: 'a'.repeat(64), track_ids: [7], error_code: null }

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('offering files to the archive', () => {
  it('sends one request per file, and the file itself as the body', async () => {
    const stub = show([on('/tracks/imports', IMPORTED)])

    await userEvent.upload(screen.getByLabelText(/choose files/i), [
      gpx('walk.gpx'),
      gpx('ride.gpx'),
    ])

    await waitFor(() => {
      expect(stub.writes).toHaveLength(2)
    })
    expect(stub.writes.every((write) => write.method === 'POST')).toBe(true)
    expect(stub.requested.some((url) => url.includes('filename=walk.gpx'))).toBe(true)
    expect(stub.requested.some((url) => url.includes('filename=ride.gpx'))).toBe(true)
  })

  it('names every file and what became of it', async () => {
    show([on('/tracks/imports', IMPORTED)])

    await userEvent.upload(screen.getByLabelText(/choose files/i), [gpx('walk.gpx')])

    await waitFor(() => {
      expect(screen.getByTestId('import-result')).toBeInTheDocument()
    })
    expect(screen.getByTestId('import-result').textContent).toContain('walk.gpx')
    expect(screen.getByTestId('import-result').textContent).toMatch(/imported/i)
  })

  it('does not call a file the archive already held "imported"', async () => {
    show([
      on('/tracks/imports', {
        status: 'duplicate',
        sha256: 'b'.repeat(64),
        track_ids: [3],
        error_code: null,
      }),
    ])

    await userEvent.upload(screen.getByLabelText(/choose files/i), [gpx('again.gpx')])

    await waitFor(() => {
      expect(screen.getByTestId('import-result')).toBeInTheDocument()
    })
    // "Already in the archive" is the useful sentence. "Imported" would send
    // somebody looking for a second copy that does not exist.
    expect(screen.getByTestId('import-result').textContent).toMatch(/already/i)
  })

  it('says why a file the archive could not read failed', async () => {
    show([
      on('/tracks/imports', {
        status: 'failed',
        sha256: 'c'.repeat(64),
        track_ids: [],
        error_code: 'invalid_gpx',
      }),
    ])

    await userEvent.upload(screen.getByLabelText(/choose files/i), [gpx('broken.gpx')])

    await waitFor(() => {
      expect(screen.getByTestId('import-result')).toBeInTheDocument()
    })
    const text = screen.getByTestId('import-result').textContent
    expect(text).toContain('broken.gpx')
    // The reason in words rather than the enum name a reader cannot act on.
    expect(text).toMatch(/could not be read as GPX/i)
    expect(text).not.toContain('invalid_gpx')
  })

  it('reports a refused request rather than looking like it worked', async () => {
    show([])

    await userEvent.upload(screen.getByLabelText(/choose files/i), [gpx('walk.gpx')])

    await waitFor(() => {
      expect(screen.getByTestId('import-result')).toBeInTheDocument()
    })
    expect(screen.getByTestId('import-result').textContent).toMatch(/not found|archive answered/i)
  })

  it('tells the page to reload once something actually arrived', async () => {
    const imported = vi.fn()
    show([on('/tracks/imports', IMPORTED)], imported)

    await userEvent.upload(screen.getByLabelText(/choose files/i), [gpx('walk.gpx')])

    await waitFor(() => {
      expect(imported).toHaveBeenCalled()
    })
  })

  it('leaves the list alone when nothing new arrived', async () => {
    const imported = vi.fn()
    show(
      [
        on('/tracks/imports', {
          status: 'duplicate',
          sha256: 'b'.repeat(64),
          track_ids: [3],
          error_code: null,
        }),
      ],
      imported,
    )

    await userEvent.upload(screen.getByLabelText(/choose files/i), [gpx('again.gpx')])

    await waitFor(() => {
      expect(screen.getByTestId('import-result')).toBeInTheDocument()
    })
    expect(imported).not.toHaveBeenCalled()
  })
})

describe('an archive that refuses uploads', () => {
  it('offers no control at all, rather than one the server would refuse', () => {
    stubArchive([on('/system/info', systemInfo())])
    render(<TrackImport enabled={false} onImported={() => {}} />)

    expect(screen.queryByLabelText(/choose files/i)).not.toBeInTheDocument()
    expect(screen.getByTestId('import-disabled')).toBeInTheDocument()
  })
})
