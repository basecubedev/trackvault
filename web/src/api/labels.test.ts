import { describe, expect, it } from 'vitest'
import { analysisLabel, durationHeading, kindLabel, timingLabel } from './labels'

/**
 * Two states a reader has to act on differently must never be told apart by
 * colour alone, and a duration whose clock nothing vouches for must never be
 * labelled as time somebody spent.
 */
describe('every state carries a word and a glyph', () => {
  it.each(['current', 'outdated', 'missing', 'invalid'] as const)('analysis %s', (status) => {
    const label = analysisLabel(status)
    expect(label.text).not.toBe('')
    expect(label.mark).not.toBe('')
    expect(label.hint).not.toBe('')
  })

  it.each(['recorded', 'planned', 'unknown'] as const)('kind %s', (kind) => {
    expect(kindLabel(kind).mark).not.toBe('')
  })

  it.each(['observed', 'estimated', 'unknown'] as const)('timing %s', (basis) => {
    expect(timingLabel(basis).mark).not.toBe('')
  })

  it('gives the four analysis states four distinct glyphs', () => {
    const marks = (['current', 'outdated', 'missing', 'invalid'] as const).map(
      (status) => analysisLabel(status).mark,
    )
    expect(new Set(marks).size).toBe(4)
  })
})

describe('an unverified clock is never called time somebody spent', () => {
  it('qualifies the heading when the timing is not actual', () => {
    expect(durationHeading('Moving', false)).toBe('Moving (route timeline)')
  })

  it('leaves it alone when the timing was observed', () => {
    expect(durationHeading('Moving', true)).toBe('Moving')
  })

  it('names unknown timing rather than implying observed timing', () => {
    expect(timingLabel('unknown').text.toLowerCase()).toContain('unknown')
  })
})
