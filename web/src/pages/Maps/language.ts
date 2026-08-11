import { ApiError } from '../../api/client'
import type { MapJob } from '../../api/client'
import { formatBytes } from '../../api/format'
import type { StateLabel } from '../../api/labels'

/**
 * Turning map states and failures into sentences somebody can act on.
 *
 * Pure functions, in their own module, because this is the part of the map
 * manager worth testing without a browser: a reader who runs out of disk
 * deserves to be told what is needed and what is available, and a reader whose
 * download failed deserves to be told their existing map is fine.
 */

const STATE_LABELS: Record<string, StateLabel> = {
  queued: { text: 'Queued', hint: 'Waiting for the download to start', mark: '\u25CB', tone: 'neutral' },
  downloading: { text: 'Downloading', hint: 'Bytes are arriving', mark: '\u2193', tone: 'neutral' },
  validating: { text: 'Checking', hint: 'Reading the package before it is used', mark: '\u25CE', tone: 'neutral' },
  publishing: { text: 'Installing', hint: 'Switching to the new package', mark: '\u25CE', tone: 'neutral' },
  completed: { text: 'Installed', hint: 'Ready to draw', mark: '\u2713', tone: 'good' },
  failed: { text: 'Failed', hint: 'Nothing was changed; try again', mark: '\u2715', tone: 'bad' },
  interrupted: { text: 'Interrupted', hint: 'The application stopped mid-download', mark: '\u25A0', tone: 'warn' },
  cancelled: { text: 'Cancelled', hint: 'Stopped on request', mark: '\u25A0', tone: 'neutral' },
  installed: { text: 'Installed', hint: 'A verified package backs this region', mark: '\u2713', tone: 'good' },
  invalid: { text: 'Invalid', hint: 'The package file is missing or unreadable', mark: '\u2715', tone: 'bad' },
  not_installed: { text: 'Not installed', hint: 'Nothing downloaded yet', mark: '\u25CB', tone: 'neutral' },
  installing: { text: 'Installing', hint: 'A download is running', mark: '\u2193', tone: 'neutral' },
  updating: { text: 'Updating', hint: 'A newer package is being fetched', mark: '\u2193', tone: 'neutral' },
}

const UNKNOWN_STATE: StateLabel = {
  text: 'Unknown',
  hint: 'This build does not recognise the state the archive reported',
  mark: '?',
  tone: 'warn',
}

/**
 * Return a state as a word and a glyph, never a raw code.
 *
 * A glyph beside the word rather than colour alone: `installed` and `invalid`
 * are exactly the two a reader has to act on differently.
 */
export function stateLabel(state: string): StateLabel {
  return STATE_LABELS[state] ?? UNKNOWN_STATE
}

/**
 * Return one job's progress as text.
 *
 * Text first and a bar second. A progress element alone is invisible to a
 * screen reader and unquotable in a bug report, and when the provider declared
 * no size there is no percentage to draw at all -- so the byte count is the
 * primary statement and the percentage is added only when it is real.
 */
export function jobProgress(job: MapJob): string {
  if (job.state === 'queued') return 'Waiting to start'
  if (job.bytes_total === null) {
    return `${formatBytes(job.bytes_downloaded)} downloaded, total size unknown`
  }
  const percentage = job.percentage === null ? '' : ` · ${job.percentage} %`
  return `${formatBytes(job.bytes_downloaded)} of ${formatBytes(job.bytes_total)}${percentage}`
}

/**
 * Return what to tell a reader when an install, update or removal failed.
 *
 * Every message says what is still true, because that is the thing somebody
 * looking at a red box actually needs: the map they had is the map they still
 * have.
 */
export function installFailure(cause: unknown): string {
  if (!(cause instanceof ApiError)) {
    return 'The archive could not be reached. Nothing was changed.'
  }
  switch (cause.code) {
    case 'map_provider_unavailable':
      return 'The map provider could not be reached. Installed maps continue to work.'
    case 'map_insufficient_disk_space':
      return `Not enough free space (${cause.message}). The map you have is untouched.`
    case 'map_download_too_large':
      return 'That package is larger than this deployment allows. Nothing was downloaded.'
    case 'map_download_failed':
      return 'The download did not finish. Any map you already had is still installed — try again.'
    case 'map_download_redirect_refused':
      return 'The provider redirected somewhere unexpected, so the download was refused.'
    case 'map_package_schema_unsupported':
      return 'The provider served a package this version cannot draw. Your current map is unchanged.'
    case 'map_package_licence_missing':
      return 'The package states no licence or author, so it was not installed.'
    case 'map_package_invalid':
    case 'map_package_empty':
      return 'The downloaded package was not usable. Your current map is unchanged.'
    case 'map_mutation_in_progress':
      return 'That region is already being worked on. Wait for it to finish.'
    case 'map_package_unavailable':
      return 'The provider publishes no package for that region.'
    case 'map_package_not_installed':
      return 'That map is not installed.'
    case 'map_region_unknown':
      return 'The catalog holds no such region.'
    default:
      return `The archive answered ${cause.status} (${cause.code}).`
  }
}
