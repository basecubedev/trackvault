import type { Activity, AggregationScope, AnalysisAvailability, TrackOrder } from './client'

/**
 * The closed vocabularies the interface offers, taken from the API's own enums.
 *
 * There is deliberately no second activity list here. The generated types are
 * the contract, so a value the backend adds shows up as a type error rather
 * than as a filter nobody can select.
 */

export const ACTIVITIES: readonly Activity[] = [
  'walking',
  'hiking',
  'cycling',
  'running',
  'scooter',
  'motorcycle',
  'other',
  'unknown',
]

export const SCOPES: readonly { value: AggregationScope; label: string; hint: string }[] = [
  {
    value: 'recorded',
    label: 'Recorded',
    hint: 'What actually happened: tracks something measured.',
  },
  {
    value: 'planned',
    label: 'Planned',
    hint: 'Routes somebody drew. Never added to recorded totals.',
  },
  {
    value: 'unknown',
    label: 'Unknown',
    hint: 'Tracks whose kind the evidence does not decide. Their own category.',
  },
]

export const ANALYSIS_STATES: readonly AnalysisAvailability[] = [
  'current',
  'outdated',
  'missing',
  'invalid',
]

export const ORDERS: readonly { value: TrackOrder; label: string }[] = [
  { value: 'imported_newest_first', label: 'Import newest' },
  { value: 'activity_newest_first', label: 'Activity newest' },
  { value: 'activity_oldest_first', label: 'Activity oldest' },
  { value: 'longest_first', label: 'Longest' },
]
