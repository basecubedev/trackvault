import { describe, expect, it } from 'vitest'
import type { MonthlyStatistics, Totals } from '../../api/client'
import { coverageSummary, coverageWarnings } from './coverage'
import { monthlySeries } from './monthly'

function totals(overrides: Partial<Totals> = {}): Totals {
  return {
    track_count: 0,
    analysed_track_count: 0,
    tracks_without_analysis: 0,
    tracks_with_outdated_analysis: 0,
    tracks_with_invalid_analysis: 0,
    tracks_with_failed_analysis: 0,
    tracks_without_observed_timing: 0,
    distance_m: 0,
    elapsed_duration_s: 0,
    moving_duration_s: 0,
    elevation_gain_m: 0,
    ...overrides,
  }
}

function statistics(months: Partial<Totals>[]): MonthlyStatistics {
  return {
    year: 2025,
    scope: 'recorded',
    activity: null,
    timezone: 'UTC',
    months: months.map((overrides, index) => ({
      month: index + 1,
      totals: totals(overrides),
    })),
  }
}

describe('the monthly series', () => {
  it('always has twelve months, including the empty ones', () => {
    const series = monthlySeries(statistics(Array.from({ length: 12 }, () => ({}))), 'distance')

    expect(series).toHaveLength(12)
    expect(series.map((point) => point.label)[0]).toBe('Jan')
    expect(series.map((point) => point.label)[11]).toBe('Dec')
  })

  it('keeps an unavailable metric absent rather than plotting it as zero', () => {
    const series = monthlySeries(
      statistics([{ track_count: 3, distance_m: null }, ...Array.from({ length: 11 }, () => ({}))]),
      'distance',
    )

    expect(series[0]?.value).toBeNull()
    expect(series[0]?.trackCount).toBe(3)
  })

  it('converts metres to kilometres for the axis and nowhere else', () => {
    const series = monthlySeries(
      statistics([{ distance_m: 9270.7 }, ...Array.from({ length: 11 }, () => ({}))]),
      'distance',
    )

    expect(series[0]?.value).toBeCloseTo(9.2707, 4)
  })

  it('carries the coverage a tooltip needs', () => {
    const series = monthlySeries(
      statistics([
        { track_count: 5, analysed_track_count: 3 },
        ...Array.from({ length: 11 }, () => ({})),
      ]),
      'tracks',
    )

    expect(series[0]?.trackCount).toBe(5)
    expect(series[0]?.analysedTrackCount).toBe(3)
  })
})

describe('coverage', () => {
  it('says nothing when a period is complete', () => {
    expect(coverageSummary(totals({ track_count: 4, analysed_track_count: 4 }))).toBeNull()
    expect(coverageWarnings(totals({ track_count: 4, analysed_track_count: 4 }))).toEqual([])
  })

  it('says how much of a period a total covers when it is partial', () => {
    expect(coverageSummary(totals({ track_count: 49, analysed_track_count: 47 }))).toBe(
      '47 of 49 tracks current',
    )
  })

  it('separates the shortfalls, because they need different actions', () => {
    const warnings = coverageWarnings(
      totals({
        track_count: 4,
        analysed_track_count: 1,
        tracks_with_outdated_analysis: 2,
        tracks_with_invalid_analysis: 1,
      }),
    )

    expect(warnings.map((warning) => warning.key)).toEqual(['outdated', 'invalid'])
    expect(warnings[0]?.text).toBe('2 tracks need re-analysis')
    expect(warnings[1]?.text).toBe('1 track has invalid derived data')
  })
})
