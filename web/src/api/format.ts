/**
 * Turning the archive's numbers into text, and never inventing one.
 *
 * Two rules, and the second is the reason this file exists at all.
 *
 * **Units are converted only for display.** The archive stores and serves SI:
 * metres, seconds, metres per second. Kilometres and km/h are a reading
 * convenience and they never travel back.
 *
 * **Absent is not zero.** A metric the archive could not derive comes over as
 * `null`, and `null` renders as an em dash. Formatting it as `0.0 km` would
 * turn "we could not tell" into "it was nothing", which is the one translation
 * this application must not make.
 */

/** What an unavailable value looks like. Never `0`. */
export const UNAVAILABLE = '—'

export function formatDistance(metres: number | null | undefined): string {
  if (metres === null || metres === undefined) return UNAVAILABLE
  if (metres < 1000) return `${Math.round(metres)} m`
  return `${(metres / 1000).toFixed(metres < 10_000 ? 2 : 1)} km`
}

export function formatElevation(metres: number | null | undefined): string {
  if (metres === null || metres === undefined) return UNAVAILABLE
  return `${Math.round(metres)} m`
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return UNAVAILABLE
  const total = Math.round(seconds)
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, '0')} min`
  if (minutes > 0) return `${minutes} min`
  return `${total} s`
}

export function formatSpeed(metresPerSecond: number | null | undefined): string {
  if (metresPerSecond === null || metresPerSecond === undefined) return UNAVAILABLE
  return `${(metresPerSecond * 3.6).toFixed(1)} km/h`
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return UNAVAILABLE
  return String(value)
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export function monthName(month: number): string {
  return MONTHS[month - 1] ?? String(month)
}

export function shortMonthName(month: number): string {
  return monthName(month).slice(0, 3)
}

/**
 * Render a track's own instant.
 *
 * The instant is shown whatever its basis, because it is real data about the
 * file. What it is *worth* is a separate statement and is never folded in here:
 * see `timingLabel`.
 */
export function formatInstant(value: string | null | undefined): string {
  if (!value) return UNAVAILABLE
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return UNAVAILABLE
  return parsed.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' })
}

export function formatInstantWithTime(value: string | null | undefined): string {
  if (!value) return UNAVAILABLE
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return UNAVAILABLE
  return parsed.toLocaleString('en-GB', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

/**
 * Render a byte count the way somebody deciding whether to download it reads it.
 *
 * Decimal units, because that is what a download progress dialogue, a disk
 * vendor and every provider's own page say, and a self-hosted archive
 * disagreeing with all three about what "800 MB" means helps nobody.
 *
 * `null` stays unavailable. A provider that declared no size is a real state,
 * and inventing an estimate would make "Download size" a guess wearing the
 * clothes of a fact.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) {
    return UNAVAILABLE
  }
  if (bytes < 1000) return `${bytes} B`
  const units = ['kB', 'MB', 'GB', 'TB']
  let value = bytes / 1000
  let unit = 0
  while (value >= 1000 && unit < units.length - 1) {
    value /= 1000
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}
