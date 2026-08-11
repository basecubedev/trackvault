import type { AnalysisAvailability, TemporalEvidence, TrackKind } from './client'

/**
 * The words the interface uses for the archive's states, in one place.
 *
 * Every one of these is a statement the backend made, and every one of them is
 * shown with text and shape rather than with colour alone: recorded, planned
 * and unknown look different to somebody who cannot tell green from amber, and
 * so do current, outdated, missing and invalid.
 */

export interface StateLabel {
  readonly text: string
  readonly hint: string
  /** A short glyph, so the state is legible without relying on colour. */
  readonly mark: string
  readonly tone: 'neutral' | 'good' | 'warn' | 'bad'
}

const ANALYSIS: Record<AnalysisAvailability, StateLabel> = {
  current: {
    text: 'Current',
    hint: 'Derived by the algorithms this build installs, from the geometry shown.',
    mark: '✓',
    tone: 'good',
  },
  outdated: {
    text: 'Needs re-analysis',
    hint: 'Metrics exist but were derived by algorithms or geometry that have moved on.',
    mark: '↻',
    tone: 'warn',
  },
  missing: {
    text: 'Not analysed',
    hint: 'Nothing has been derived for this track yet.',
    mark: '·',
    tone: 'neutral',
  },
  invalid: {
    text: 'Invalid data',
    hint: 'A stored analysis exists and cannot be interpreted. Re-analysing repairs it.',
    mark: '!',
    tone: 'bad',
  },
}

export function analysisLabel(status: AnalysisAvailability): StateLabel {
  return ANALYSIS[status]
}

const KINDS: Record<TrackKind, StateLabel> = {
  recorded: { text: 'Recorded', hint: 'Something measured these positions.', mark: '●', tone: 'good' },
  planned: { text: 'Planned', hint: 'A route, not a record of a journey.', mark: '◇', tone: 'neutral' },
  unknown: {
    text: 'Unknown',
    hint: 'The evidence does not decide whether this was recorded or planned.',
    mark: '?',
    tone: 'neutral',
  },
}

export function kindLabel(kind: TrackKind): StateLabel {
  return KINDS[kind]
}

const TIMING: Record<TemporalEvidence, StateLabel> = {
  observed: {
    text: 'Observed timing',
    hint: 'A receiver reported how well it was measuring, so the instants are observations.',
    mark: '◷',
    tone: 'good',
  },
  estimated: {
    text: 'Estimated timing',
    hint: 'The instants come from a route computation. They are a schedule, not a journey.',
    mark: '◔',
    tone: 'warn',
  },
  unknown: {
    text: 'Timing basis: unknown',
    hint: 'Nothing shows these instants were measured. They are a route timeline.',
    mark: '◌',
    tone: 'warn',
  },
}

export function timingLabel(basis: TemporalEvidence): StateLabel {
  return TIMING[basis]
}

/**
 * What to call a duration whose basis is not observed.
 *
 * "Moving time" is a claim about somebody's afternoon. For a track whose clock
 * nothing vouches for, the honest heading is about the path rather than about
 * the person.
 */
export function durationHeading(label: string, isActualTiming: boolean): string {
  return isActualTiming ? label : `${label} (route timeline)`
}
