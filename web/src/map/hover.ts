/**
 * Which sample the pointer is on, when a track crosses itself.
 *
 * The naive answer -- the globally nearest latitude and longitude -- is wrong
 * in exactly one place, and it is the place people hover: a figure of eight, an
 * out-and-back, a lap. At the crossing, two positions from two different parts
 * of the ride are the same distance from the cursor, and "nearest" picks
 * whichever floating-point comparison happens to win. The marker jumps between
 * kilometre 3 and kilometre 11 as the mouse moves one pixel.
 *
 * A sample already has an identity -- `(segment_index, point_index)`, assigned
 * by the archive -- so the fix is not a better distance metric. It is to work
 * along the structure that is drawn:
 *
 * ```
 * 1  candidates   the samples actually under the pointer, in pixels
 * 2  continuity   of those, the ones near where the pointer already was
 * 3  determinism  and if that settles nothing, the lowest identity wins
 * ```
 *
 * Step 2 is what makes a crossing behave. A reader tracing the first loop has a
 * previous sample on the first loop, and the candidate continuing it is the one
 * they mean. Step 3 is the honest admission that a cold hover exactly on a
 * crossing is genuinely ambiguous: no interaction context exists, so the
 * behaviour is *defined* rather than guessed -- the lower identity, every time,
 * on every machine.
 *
 * Everything here is arithmetic on plain numbers. The map supplies the pointer
 * and the scale; nothing in this file knows what WebGL is, which is what makes
 * the interesting part testable without a browser.
 */

export interface SampleRef {
  readonly segment_index: number
  readonly point_index: number
}

export interface Positioned extends SampleRef {
  readonly latitude: number
  readonly longitude: number
}

export interface HoverCandidate {
  readonly sample: Positioned
  /** How far the sample sits from the pointer, in screen pixels. */
  readonly pixels: number
  /** Where the sample sits in the flattened series, for continuity. */
  readonly index: number
}

/** How far from the pointer a sample may sit and still count as under it. */
export const HIT_RADIUS_PIXELS = 12

/**
 * How much closer one candidate has to be to win on distance alone.
 *
 * Below this, two candidates are "as good as each other" and the tie is settled
 * by continuity rather than by a floating-point comparison nobody can see.
 */
export const TIE_PIXELS = 6

/**
 * How far along the series the pointer may have travelled between two frames.
 *
 * A pointer moving along a line advances a few samples per frame. A candidate
 * hundreds of samples away is a different part of the track that happens to
 * pass nearby, and following it is the jump this exists to prevent.
 */
export const CONTINUITY_SAMPLES = 40

export interface PointerScale {
  /** Degrees of longitude per screen pixel, at the current view. */
  readonly longitude: number
  /** Degrees of latitude per screen pixel. */
  readonly latitude: number
}

/**
 * The samples within the hit radius of a pointer, with their pixel distances.
 *
 * The scale converts degrees to pixels, which is what makes "near the pointer"
 * mean the same thing at every zoom level: a fixed tolerance in degrees is a
 * kilometre when zoomed out and a millimetre when zoomed in.
 */
export function candidatesUnder(
  samples: readonly Positioned[],
  pointer: { longitude: number; latitude: number },
  scale: PointerScale,
  radius: number = HIT_RADIUS_PIXELS,
): HoverCandidate[] {
  if (scale.longitude <= 0 || scale.latitude <= 0) return []
  const found: HoverCandidate[] = []
  samples.forEach((sample, index) => {
    const x = (sample.longitude - pointer.longitude) / scale.longitude
    const y = (sample.latitude - pointer.latitude) / scale.latitude
    const pixels = Math.sqrt(x * x + y * y)
    if (pixels <= radius) found.push({ sample, pixels, index })
  })
  return found
}

/**
 * Pick the sample a reader means, given where they were pointing before.
 *
 * Returns `null` when nothing is under the pointer -- which is a real answer,
 * and the reason the marker disappears when somebody moves off the line rather
 * than sticking to the last place it was.
 */
export function chooseHovered(
  candidates: readonly HoverCandidate[],
  previous: number | null,
): HoverCandidate | null {
  if (candidates.length === 0) return null

  const closest = Math.min(...candidates.map((candidate) => candidate.pixels))
  const contenders = candidates.filter((candidate) => candidate.pixels <= closest + TIE_PIXELS)

  if (previous !== null) {
    const continuing = contenders
      .filter((candidate) => Math.abs(candidate.index - previous) <= CONTINUITY_SAMPLES)
      .sort((a, b) => Math.abs(a.index - previous) - Math.abs(b.index - previous))
    if (continuing.length > 0) return continuing[0] ?? null
  }

  // Nothing to continue from, or the pointer jumped. Deterministic by identity,
  // so an ambiguous crossing behaves the same way twice.
  const ordered = [...contenders].sort(
    (a, b) =>
      a.pixels - b.pixels ||
      a.sample.segment_index - b.sample.segment_index ||
      a.sample.point_index - b.sample.point_index,
  )
  return ordered[0] ?? null
}

/**
 * The whole decision, from a pointer position to a sample index.
 *
 * Returns the index into `samples`, which is what a chart cursor and a marker
 * both address, or `null` when the pointer is not on the track.
 */
export function sampleUnderPointer(
  samples: readonly Positioned[],
  pointer: { longitude: number; latitude: number },
  scale: PointerScale,
  previous: number | null,
): number | null {
  return chooseHovered(candidatesUnder(samples, pointer, scale), previous)?.index ?? null
}
