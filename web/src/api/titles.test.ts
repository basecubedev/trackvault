import { describe, expect, it } from 'vitest'
import { track } from '../test-fixtures'
import { displayTitle, isFallbackTitle } from './titles'

/**
 * What to call a track nobody named.
 *
 * A list of eleven rows reading `Untitled` is a list nobody can navigate, so
 * the fallback has to differentiate. It is built from what the archive already
 * knows and already shows -- never from the file it arrived as, which is
 * personal data as readily as the positions are.
 */

describe('the title a track is shown under', () => {
  it('uses the title the archive reports, correction or source', () => {
    expect(displayTitle(track())).toBe('Talaia')
    expect(isFallbackTitle(track())).toBe(false)
  })

  it('describes a nameless track by what it was and when', () => {
    const nameless = track({
      title: null,
      metadata: { title: null, note: null, source_title: null, is_overridden: false },
    })

    expect(displayTitle(nameless)).toBe('Walking, 20 Oct 2025')
    expect(isFallbackTitle(nameless)).toBe(true)
  })

  it('treats a title of whitespace as no title', () => {
    const blank = track({ title: '   ' })

    expect(isFallbackTitle(blank)).toBe(true)
    expect(displayTitle(blank)).not.toBe('   ')
  })

  it('never dates a track whose clock nothing vouches for', () => {
    const unverified = track({
      title: null,
      activity: 'cycling',
      timeline: {
        started_at: '2025-10-20T07:00:00Z',
        ended_at: null,
        basis: 'unknown',
        is_actual_calendar_time: false,
        is_actual_activity_timing: false,
      },
    })

    // A title is the one place a reader takes as fact. Printing October there
    // would put an unverified date into it.
    expect(displayTitle(unverified)).toBe('Cycling #7')
    expect(displayTitle(unverified)).not.toContain('Oct')
  })

  it('stays distinguishable when two tracks describe the same thing', () => {
    const first = track({ id: 7, title: null })
    const second = track({ id: 8, title: null, timeline: { ...track().timeline, started_at: null } })

    expect(displayTitle(first)).not.toBe(displayTitle(second))
    expect(displayTitle(second)).toContain('#8')
  })

  it('never carries a filename or a path', () => {
    const nameless = track({ title: null })

    const shown = displayTitle(nameless)

    expect(shown).not.toContain('.gpx')
    expect(shown).not.toContain('/')
  })
})
