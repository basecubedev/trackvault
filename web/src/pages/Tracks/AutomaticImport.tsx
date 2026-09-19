import { useEffect, useRef, useState } from 'react'
import type { AutomaticImport, ImportScan } from '../../api/client'
import { api } from '../../api/client'
import { explainImportError } from '../../api/explanations'
import { formatInstantWithTime } from '../../api/format'
import type { StateLabel } from '../../api/labels'
import { useRequest } from '../../api/useRequest'
import { Badge } from '../../components/Badge'
import { Notice } from '../../components/Notice'

/**
 * How often the line asks again. The status is one small read, and a folder
 * scanned every quarter of an hour does not need a fresher answer than a minute.
 */
const REFRESH_MS = 60_000

/**
 * How often it asks while a scan is running. Soon enough that "reading the
 * folder now" does not outlive the read by long; far enough apart to stay one
 * request among many.
 */
const REFRESH_WHILE_SCANNING_MS = 5_000

const ON: StateLabel = {
  text: 'Automatic import on',
  hint: 'The server reads the import folder on its own and imports new files.',
  mark: '●',
  tone: 'good',
}

const OFF: StateLabel = {
  text: 'Automatic import off',
  hint: 'The server does not read the import folder on its own.',
  mark: '○',
  tone: 'neutral',
}

/**
 * Whether the server reads the import folder on its own, and what it found.
 *
 * Nobody watches a background import, so this is where somebody learns that
 * it runs, which folder it reads, when it last did, and which file it could not
 * import and why. It puts the archive's counts into words and decides nothing:
 * whether a file was new, a duplicate or broken is the import's verdict.
 *
 * The files that failed are the last scan's, because every scan reports every
 * file in the folder that is not a track -- including one it did not have to
 * read again. So a broken file stays listed for as long as it is there, however
 * much else arrives beside it, and leaves the list once it is fixed or gone.
 *
 * The line asks again on its own while the automatic import is on, and tells
 * the page when a scan brought something new, so the list beside it catches up
 * without anybody reloading.
 */
export function AutomaticImportStatus({
  onImported,
}: {
  /** Called once a scan after the first answer imported or restored a file. */
  onImported?: () => void
}) {
  const [tick, setTick] = useState(0)
  const status = useRequest((signal) => api.readAutomaticImport(signal), [tick])
  const enabled = status.data?.enabled ?? false
  const scanning = status.data?.scanning ?? false

  // Asking only while there is something to ask about: a switched-off import
  // answers the same thing for the lifetime of the tab.
  useEffect(() => {
    if (!enabled) return undefined
    const timer = window.setInterval(
      () => {
        setTick((value) => value + 1)
      },
      scanning ? REFRESH_WHILE_SCANNING_MS : REFRESH_MS,
    )
    return () => {
      window.clearInterval(timer)
    }
  }, [enabled, scanning])

  // The first answer is the page as it was loaded, so only a change after it
  // is news. A scan is identified by when it finished.
  const seen = useRef<string | null | undefined>(undefined)
  const activity = status.data?.last_activity ?? null
  useEffect(() => {
    const finished = activity?.finished_at ?? null
    if (status.data === null || finished === seen.current) return
    const known = seen.current !== undefined
    seen.current = finished
    if (known && activity !== null && activity.imported + activity.repaired > 0) onImported?.()
  }, [status.data, activity, onImported])

  if (status.error !== null) {
    return (
      <p className="muted automatic-import" data-testid="automatic-import">
        Whether files are imported automatically could not be read: {status.error}
      </p>
    )
  }
  if (status.data === null) return null
  const report = status.data

  if (!report.enabled) {
    return (
      <p className="muted automatic-import" data-testid="automatic-import">
        <Badge label={OFF} />{' '}
        {report.directory ? (
          <>
            Files in <code>{report.directory}</code> are imported when somebody runs{' '}
            <code>trackvault scan</code>.
          </>
        ) : (
          'No import folder is configured.'
        )}
      </p>
    )
  }

  const failures = report.last_scan?.failures ?? []
  return (
    <div className="automatic-import" data-testid="automatic-import">
      <p className="muted">
        <Badge label={ON} /> New files in <code>{report.directory}</code> are imported every{' '}
        {every(report.interval_minutes)}, once they have been left alone for{' '}
        {minutes(report.settle_minutes)}. {lastRead(report)}
        {lastChange(report)}
      </p>
      {failures.length > 0 && (
        <Notice>
          Not imported from the folder:
          <ul className="automatic-import__failures">
            {/* Keyed by position too: two names that are not UTF-8 can read the same. */}
            {failures.map((failure, position) => (
              <li key={`${String(position)}:${failure.name}`}>
                <span className="track-import__name">{failure.name}</span> —{' '}
                {failure.error_code === null
                  ? 'the server could not read or import it; its log says why'
                  : explainImportError(failure.error_code)}
              </li>
            ))}
          </ul>
        </Notice>
      )}
    </div>
  )
}

function every(interval: number): string {
  return interval === 1 ? 'minute' : `${interval} minutes`
}

function minutes(count: number): string {
  return count === 1 ? 'a minute' : `${count} minutes`
}

function lastRead(report: AutomaticImport): string {
  if (report.scanning) return 'Reading the folder now.'
  if (report.last_scan === null) return 'The folder has not been read yet.'
  if (!report.last_scan.directory_available) {
    return `Last tried ${formatInstantWithTime(report.last_scan.finished_at)}: the folder could not be opened. Check that it is mounted.`
  }
  return `Last read ${formatInstantWithTime(report.last_scan.finished_at)}: ${summary(report.last_scan)}.`
}

/** The last scan that changed something, when that was not the last scan. */
function lastChange(report: AutomaticImport): string {
  const activity = report.last_activity
  if (activity === null || activity.finished_at === report.last_scan?.finished_at) return ''
  return ` Last change ${formatInstantWithTime(activity.finished_at)}: ${summary(activity)}.`
}

/** One scan in words. Nothing to report is a sentence, not a row of zeros. */
function summary(scan: ImportScan): string {
  const parts = [
    scan.imported > 0 ? `${scan.imported} imported` : null,
    scan.repaired > 0 ? `${scan.repaired} restored` : null,
    scan.failed > 0 ? `${scan.failed} not imported` : null,
    scan.waiting > 0 ? `${scan.waiting} still arriving` : null,
  ].filter((part) => part !== null)
  return parts.length > 0 ? parts.join(', ') : 'nothing new'
}
