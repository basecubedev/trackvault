import { describe, expect, it } from 'vitest'
import {
  UNAVAILABLE,
  formatDistance,
  formatDuration,
  formatElevation,
  formatSpeed,
  monthName,
} from './format'

/**
 * The one rule this file exists for: absent is not zero.
 *
 * A metric the archive could not derive comes over as `null`. Rendering it as
 * `0.0 km` would turn "we could not tell" into "it was nothing", which is the
 * translation every layer underneath refuses to make.
 */
describe('null is never rendered as zero', () => {
  it.each([
    ['distance', formatDistance],
    ['elevation', formatElevation],
    ['duration', formatDuration],
    ['speed', formatSpeed],
  ])('%s', (_name, format) => {
    expect(format(null)).toBe(UNAVAILABLE)
    expect(format(undefined)).toBe(UNAVAILABLE)
    expect(format(0)).not.toBe(UNAVAILABLE)
  })
})

describe('a real zero is still a zero', () => {
  it('reports nothing travelled as nothing travelled', () => {
    expect(formatDistance(0)).toBe('0 m')
    expect(formatDuration(0)).toBe('0 s')
    expect(formatElevation(0)).toBe('0 m')
    expect(formatSpeed(0)).toBe('0.0 km/h')
  })
})

describe('units are converted only for display', () => {
  it('reads metres as kilometres once there are enough of them', () => {
    expect(formatDistance(940)).toBe('940 m')
    expect(formatDistance(9270.7)).toBe('9.27 km')
    expect(formatDistance(42_195)).toBe('42.2 km')
  })

  it('reads metres per second as kilometres per hour', () => {
    expect(formatSpeed(1)).toBe('3.6 km/h')
  })

  it('reads seconds as hours and minutes', () => {
    expect(formatDuration(45)).toBe('45 s')
    expect(formatDuration(600)).toBe('10 min')
    expect(formatDuration(10_296)).toBe('2 h 51 min')
  })
})

describe('months', () => {
  it('names every month of the calendar', () => {
    expect(monthName(1)).toBe('January')
    expect(monthName(12)).toBe('December')
  })
})
