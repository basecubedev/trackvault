import type { Totals } from '../../api/client'

/**
 * What a period leaves out, in words somebody can act on.
 *
 * The archive reports four availability counters that partition a period
 * exactly. A dashboard that only showed the total would present eight tracks'
 * worth of distance as if it were ten tracks' worth, which is the failure the
 * counters exist against.
 */

export interface CoverageWarning {
  readonly key: string
  readonly text: string
  /**
   * The analysis state this shortfall is, or `null` when there is no filter
   * for it.
   *
   * A failed newest attempt is orthogonal to the four availability states -- a
   * track can hold current metrics and a failed retry at once -- so it is
   * reported without a link rather than linked to a filter that would select
   * the wrong set.
   */
  readonly state: 'outdated' | 'invalid' | 'missing' | null
}

const SHORTFALLS: readonly {
  key: string
  state: 'outdated' | 'invalid' | 'missing' | null
  count: (totals: Totals) => number
  one: string
  many: string
}[] = [
  {
    key: 'outdated',
    state: 'outdated',
    count: (totals) => totals.tracks_with_outdated_analysis,
    one: 'needs re-analysis',
    many: 'need re-analysis',
  },
  {
    key: 'invalid',
    state: 'invalid',
    count: (totals) => totals.tracks_with_invalid_analysis,
    one: 'has invalid derived data',
    many: 'have invalid derived data',
  },
  {
    key: 'missing',
    state: 'missing',
    count: (totals) => totals.tracks_without_analysis,
    one: 'has not been analysed',
    many: 'have not been analysed',
  },
  {
    key: 'failed',
    state: null,
    count: (totals) => totals.tracks_with_failed_analysis,
    one: 'failed its newest analysis attempt',
    many: 'failed their newest analysis attempt',
  },
]

export function coverageWarnings(totals: Totals): CoverageWarning[] {
  const warnings: CoverageWarning[] = []
  for (const shortfall of SHORTFALLS) {
    const count = shortfall.count(totals)
    if (count === 0) continue
    warnings.push({
      key: shortfall.key,
      state: shortfall.state,
      text: `${count} ${plural(count)} ${count === 1 ? shortfall.one : shortfall.many}`,
    })
  }
  return warnings
}

/** "8 of 10 tracks current", or nothing at all when the period is complete. */
export function coverageSummary(totals: Totals): string | null {
  if (totals.track_count === 0) return null
  if (totals.analysed_track_count === totals.track_count) return null
  return `${totals.analysed_track_count} of ${totals.track_count} tracks current`
}

function plural(count: number): string {
  return count === 1 ? 'track' : 'tracks'
}
