import { describe, expect, it } from 'vitest'
import {
  CONTINUITY_SAMPLES,
  candidatesUnder,
  chooseHovered,
  sampleUnderPointer,
  type Positioned,
} from './hover'

/**
 * The self-crossing problem, and what "deterministic" means when it is real.
 *
 * A figure of eight passes through one position twice, an hour apart. Asking
 * which sample is nearest that position has two equally good answers, and the
 * old nearest-coordinate search returned whichever floating-point comparison
 * happened to win -- so the marker jumped between the two loops as the pointer
 * moved a single pixel.
 *
 * The fixture below is a figure of eight built from arithmetic, so the crossing
 * is exact rather than approximately at the same place.
 */

/** A figure of eight, sampled evenly, crossing itself exactly at the origin. */
function figureEight(steps = 200): Positioned[] {
  const samples: Positioned[] = []
  for (let index = 0; index < steps; index += 1) {
    const t = (index / steps) * 2 * Math.PI
    samples.push({
      segment_index: 0,
      point_index: index,
      // A lemniscate of Gerono: it passes through (0, 0) twice, at t = π/2 and
      // at t = 3π/2, which is exactly the ambiguity this has to survive.
      longitude: Math.cos(t) * 0.01,
      latitude: Math.sin(2 * t) * 0.005,
    })
  }
  return samples
}

const SCALE = { longitude: 0.0001, latitude: 0.0001 }

/** One sample of the fixture, by index. Fails loudly rather than silently. */
function at(samples: readonly Positioned[], index: number): Positioned {
  const sample = samples[index]
  if (sample === undefined) throw new Error(`no sample at ${index}`)
  return sample
}

describe('finding the sample under the pointer', () => {
  it('reports nothing when the pointer is nowhere near the track', () => {
    const samples = figureEight()

    expect(sampleUnderPointer(samples, { longitude: 5, latitude: 5 }, SCALE, null)).toBeNull()
  })

  it('follows the branch the pointer was already on, at a crossing', () => {
    const samples = figureEight()
    // Gerono's lemniscate crosses itself at the origin, which the curve reaches
    // at t = π/2 and t = 3π/2 -- a quarter and three quarters of the way round.
    const first = samples.length / 4
    const second = (samples.length * 3) / 4
    const crossing = {
      longitude: at(samples, second).longitude,
      latitude: at(samples, second).latitude,
    }

    const continuing = sampleUnderPointer(samples, crossing, SCALE, second - 2)
    const other = sampleUnderPointer(samples, crossing, SCALE, first + 2)

    expect(continuing).toBeGreaterThan(second - CONTINUITY_SAMPLES)
    expect(continuing).toBeLessThan(second + CONTINUITY_SAMPLES)
    expect(other).toBeGreaterThan(first - CONTINUITY_SAMPLES)
    expect(other).toBeLessThan(first + CONTINUITY_SAMPLES)
  })

  it('behaves the same way twice when nothing disambiguates a crossing', () => {
    const samples = figureEight()
    const crossing = { longitude: at(samples, 0).longitude, latitude: at(samples, 0).latitude }

    const first = sampleUnderPointer(samples, crossing, SCALE, null)
    const again = sampleUnderPointer(samples, crossing, SCALE, null)

    // A cold hover exactly on a crossing is genuinely ambiguous. What matters
    // is that it is *decided* -- by identity, lowest first -- rather than left
    // to whichever comparison wins.
    expect(first).toBe(again)
    expect(first).toBe(0)
  })

  it('jumps when the pointer really has moved somewhere else', () => {
    const samples = figureEight()
    const far = at(samples, 120)

    const index = sampleUnderPointer(
      samples,
      { longitude: far.longitude, latitude: far.latitude },
      SCALE,
      2,
    )

    // Continuity is a tie-break, not a magnet: nothing near sample 2 is under
    // the pointer, so the pointer wins.
    expect(index).toBe(120)
  })

  it('measures the hit radius in pixels, so zoom does not change what is hoverable', () => {
    const samples: Positioned[] = [
      { segment_index: 0, point_index: 0, latitude: 0, longitude: 0 },
      { segment_index: 0, point_index: 1, latitude: 0, longitude: 0.002 },
    ]
    const pointer = { longitude: 0.001, latitude: 0 }

    const zoomedIn = candidatesUnder(samples, pointer, { longitude: 0.00001, latitude: 0.00001 })
    const zoomedOut = candidatesUnder(samples, pointer, { longitude: 0.01, latitude: 0.01 })

    expect(zoomedIn).toHaveLength(0)
    expect(zoomedOut).toHaveLength(2)
  })

  it('prefers a clearly closer sample over a barely continuing one', () => {
    const close = {
      sample: { segment_index: 1, point_index: 4, latitude: 0, longitude: 0 },
      pixels: 0.5,
      index: 400,
    }
    const distant = {
      sample: { segment_index: 0, point_index: 9, latitude: 0, longitude: 0 },
      pixels: 11,
      index: 12,
    }

    expect(chooseHovered([close, distant], 10)?.index).toBe(400)
  })

  it('never reports a sample the archive does not identify', () => {
    const samples = figureEight()
    const index = sampleUnderPointer(
      samples,
      { longitude: at(samples, 30).longitude, latitude: at(samples, 30).latitude },
      SCALE,
      null,
    )

    expect(index).not.toBeNull()
    expect(at(samples, index ?? -1)).toMatchObject({ segment_index: 0 })
  })
})
