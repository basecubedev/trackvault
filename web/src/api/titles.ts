import type { Track } from './client'
import { formatInstant, monthName } from './format'

/**
 * What to call a track the archive has no name for.
 *
 * Plenty of exports title nothing, and a list of eleven rows reading `Untitled`
 * is a list nobody can navigate: the fallback has to *differentiate*, not just
 * fill a gap. So it is built from what the archive already knows and already
 * shows -- the activity and the date -- rather than from the file it came from.
 *
 * The original filename is deliberately **not** used. It is personal data as
 * readily as the positions are (`hike-with-anna-to-her-house.gpx`), it is
 * display metadata rather than identity, and the archive's own privacy rules
 * keep it out of anything it does not have to be in. A server path never
 * appears anywhere.
 *
 * The identity is the last resort and is always present, so two otherwise
 * identical tracks never render as the same line.
 */
export function displayTitle(track: Track): string {
  if (track.title !== null && track.title.trim() !== '') return track.title
  return describe(track)
}

/** Whether a row's title is the archive's own words rather than the file's. */
export function isFallbackTitle(track: Track): boolean {
  return track.title === null || track.title.trim() === ''
}

function describe(track: Track): string {
  const activity = track.activity === 'unknown' ? 'Track' : capitalise(track.activity)
  const when = occasion(track)
  return when === null ? `${activity} #${track.id}` : `${activity}, ${when}`
}

/**
 * When the track happened, in words, or `null` when nothing vouches for a date.
 *
 * A timeline the archive cannot place in a calendar is still real data about
 * the file, but it is not a date and must not be printed as one. Naming the
 * month it *claims* would be putting an unverified date into the one place a
 * reader takes as fact -- the title.
 */
function occasion(track: Track): string | null {
  if (!track.timeline.is_actual_calendar_time) return null
  const started = track.timeline.started_at
  if (started === null) return null
  return formatInstant(started)
}

function capitalise(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

/** A month and year in words, for a heading. */
export function periodLabel(year: number, month: number | null): string {
  return month === null ? String(year) : `${monthName(month)} ${year}`
}
