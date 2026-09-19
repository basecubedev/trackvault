import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { automaticImport, importScan, on, stubArchive } from '../../test-fixtures'
import { AutomaticImportStatus } from './AutomaticImport'

/**
 * Whether the server reads the import folder on its own, and what it found.
 *
 * Nobody watches a background import, so the page is where somebody finds out
 * that it is running, which folder it reads, when it last did, and -- the part
 * that needs acting on -- which file it could not import and why.
 */

async function show(body: unknown): Promise<HTMLElement> {
  stubArchive([on('/tracks/imports/automatic', body)])
  render(<AutomaticImportStatus />)
  await waitFor(() => {
    expect(screen.getByTestId('automatic-import')).toBeInTheDocument()
  })
  return screen.getByTestId('automatic-import')
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

/** Let the page's own clock run on, and whatever it asked for arrive. */
async function later(milliseconds: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(milliseconds)
  })
}

describe('the automatic import, as the tracks page reports it', () => {
  it('says that it is on, which folder it reads and how often', async () => {
    const status = await show(automaticImport())

    expect(status.textContent).toMatch(/automatic import on/i)
    expect(status.textContent).toContain('/import')
    expect(status.textContent).toContain('every 15 minutes')
    expect(status.textContent).toContain('left alone for 5 minutes')
  })

  it('says every minute rather than every 1 minutes', async () => {
    const status = await show(automaticImport({ interval_minutes: 1 }))

    expect(status.textContent).toContain('every minute,')
  })

  it('says a quiet scan found nothing new rather than showing zeros', async () => {
    const status = await show(automaticImport())

    expect(status.textContent).toMatch(/last read .*nothing new/i)
  })

  it('counts what the last scan did in the archive’s own terms', async () => {
    const status = await show(
      automaticImport({ last_scan: importScan({ imported: 3, skipped: 1, waiting: 1 }) }),
    )

    expect(status.textContent).toContain('3 imported')
    expect(status.textContent).toContain('1 still arriving')
  })

  it('names every file that could not be imported, and why', async () => {
    const failed = importScan({
      finished_at: '2026-09-19T11:00:01Z',
      imported: 1,
      failed: 2,
      failures: [
        { name: 'broken.gpx', error_code: 'invalid_gpx' },
        { name: 'locked.gpx', error_code: null },
      ],
    })
    const status = await show(automaticImport({ last_scan: failed }))

    expect(status.textContent).toContain('broken.gpx')
    expect(status.textContent).toContain('could not be read as GPX')
    expect(status.textContent).toContain('locked.gpx')
    expect(status.textContent).toMatch(/locked\.gpx.*could not read or import it/)
  })

  it('says the folder could not be opened rather than that it held nothing new', async () => {
    const status = await show(
      automaticImport({ last_scan: importScan({ directory_available: false, discovered: 0, skipped: 0 }) }),
    )

    expect(status.textContent).toMatch(/could not be opened/i)
    expect(status.textContent).not.toMatch(/nothing new/i)
  })

  it('lists two files that read the same, twice', async () => {
    // Two names that are not UTF-8 can both read "M�nchen.gpx" once made
    // readable. They are still two files, and both need somebody to look.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    const same = { name: 'M\uFFFDnchen.gpx', error_code: 'invalid_gpx' }
    const status = await show(
      automaticImport({ last_scan: importScan({ failed: 2, failures: [same, same] }) }),
    )

    expect(status.querySelectorAll('.automatic-import__failures li')).toHaveLength(2)
    expect(errors).not.toHaveBeenCalled()
    errors.mockRestore()
  })

  it('lists what is not imported now, not what the last busy scan found', async () => {
    // The last scan describes the folder as it is: a broken file still there
    // is in it, and one that was fixed or taken out is not.
    const fixed = importScan({ failed: 1, failures: [{ name: 'fixed.gpx', error_code: 'invalid_gpx' }] })
    const status = await show(automaticImport({ last_scan: importScan(), last_activity: fixed }))

    expect(status.textContent).not.toContain('fixed.gpx')
  })

  it('does not claim a result before the folder was first read', async () => {
    const status = await show(automaticImport({ last_scan: null }))

    expect(status.textContent).toMatch(/not been read yet/i)
  })

  it('says when a scan is running', async () => {
    const status = await show(automaticImport({ scanning: true }))

    expect(status.textContent).toMatch(/reading the folder now/i)
  })

  it('says it is off, and how files in the folder get in instead', async () => {
    const status = await show(automaticImport({ enabled: false, last_scan: null }))

    expect(status.textContent).toMatch(/automatic import off/i)
    expect(status.textContent).toContain('trackvault scan')
  })

  it('says so when no import folder is configured', async () => {
    const status = await show(automaticImport({ enabled: false, directory: null, last_scan: null }))

    expect(status.textContent).toMatch(/no import folder is configured/i)
  })

  it('asks again soon while a scan runs, so "reading the folder now" does not outlive it', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    let current = automaticImport({ scanning: true })
    stubArchive([(url) => (url.includes('/tracks/imports/automatic') ? current : undefined)])
    render(<AutomaticImportStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('automatic-import').textContent).toMatch(/reading the folder now/i)
    })

    current = automaticImport()
    await later(5_000)

    expect(screen.getByTestId('automatic-import').textContent).toMatch(/last read/i)
  })

  it('tells the page when a later scan imported something, and not before', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const onImported = vi.fn()
    const earlier = importScan({ finished_at: '2026-09-19T12:00:01Z', imported: 1, skipped: 3 })
    let current = automaticImport({ last_activity: earlier })
    stubArchive([(url) => (url.includes('/tracks/imports/automatic') ? current : undefined)])
    render(<AutomaticImportStatus onImported={onImported} />)
    await waitFor(() => {
      expect(screen.getByTestId('automatic-import')).toBeInTheDocument()
    })

    await later(60_000)
    expect(onImported).not.toHaveBeenCalled()

    const newer = importScan({ finished_at: '2026-09-19T12:15:01Z', imported: 2, skipped: 4 })
    current = automaticImport({ last_scan: newer, last_activity: newer })
    await later(60_000)

    expect(onImported).toHaveBeenCalledTimes(1)
  })

  it('does not keep asking while the automatic import is off', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const stub = stubArchive([on('/tracks/imports/automatic', automaticImport({ enabled: false }))])
    render(<AutomaticImportStatus />)
    await waitFor(() => {
      expect(screen.getByTestId('automatic-import')).toBeInTheDocument()
    })

    await later(10 * 60_000)

    expect(stub.requested.filter((url) => url.includes('/tracks/imports/automatic'))).toHaveLength(1)
  })
})
