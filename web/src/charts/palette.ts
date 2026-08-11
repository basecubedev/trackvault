import type { Activity } from '../api/client'
import { ACTIVITIES } from '../api/vocabulary'

/**
 * One colour per activity, fixed for good.
 *
 * **Colour follows the entity, never its rank.** An activity takes its slot
 * from the taxonomy's own order, so cycling is the same colour in a year of
 * eight activities and in a year of two, and filtering the chart never
 * repaints the series that survive the filter. A palette assigned by size
 * would make the legend a different chart every time somebody imported a file.
 *
 * **Two modes, both chosen.** The dark column is the same eight hues stepped
 * for a dark surface rather than the light column brightened: three of the
 * light steps sit below 3:1 on a dark background and would read as smudges.
 *
 * Both sets were run through the palette validator against this application's
 * own surfaces (`#ffffff` light, `#1c2024` dark) rather than eyeballed:
 *
 * ```
 * light   lightness band PASS · chroma PASS · CVD ΔE 9.1 PASS · normal ΔE 19.6 PASS
 *         contrast WARN for three slots -> relief: the table beside the chart
 * dark    lightness band PASS · chroma PASS · CVD ΔE 8.4 PASS · normal ΔE 19.3 PASS
 *         contrast PASS
 * ```
 *
 * The light-mode contrast warning is why the monthly table carries a column per
 * activity: where a fill is faint against white, the number is still readable
 * as text. That is the obligation the warning creates, not an optional extra.
 */

interface Slot {
  readonly light: string
  readonly dark: string
}

/** Eight slots, ordered so that neighbouring pairs stay apart under CVD. */
const SLOTS: readonly Slot[] = [
  { light: '#2a78d6', dark: '#3987e5' },
  { light: '#eb6834', dark: '#d95926' },
  { light: '#1baf7a', dark: '#199e70' },
  { light: '#eda100', dark: '#c98500' },
  { light: '#e87ba4', dark: '#d55181' },
  { light: '#008300', dark: '#008300' },
  { light: '#4a3aa7', dark: '#9085e9' },
  { light: '#e34948', dark: '#e66767' },
]

const FALLBACK: Slot = { light: '#5c636b', dark: '#a4acb4' }

/**
 * Return the colour a chart with a single, unnamed series is drawn in.
 *
 * The first slot: a lone series is not "activity number one", it is the whole
 * of what is being shown, and it takes the palette's leading colour rather than
 * whichever hue an activity happens to sit on.
 */
export function seriesColor(dark: boolean): string {
  const slot = SLOTS[0] ?? FALLBACK
  return dark ? slot.dark : slot.light
}

/**
 * Return the colour one activity is drawn in.
 *
 * An activity the vocabulary does not hold is drawn in the muted grey rather
 * than in a generated hue: a ninth colour nobody validated would be the one
 * that fails a colour-vision check, and it would arrive without anybody
 * deciding to add it.
 */
export function activityColor(activity: Activity, dark: boolean): string {
  const slot = SLOTS[ACTIVITIES.indexOf(activity)] ?? FALLBACK
  return dark ? slot.dark : slot.light
}

/**
 * Return the colour one sensor's readings are drawn in.
 *
 * Slots from the same validated set, asked for by what they mean rather than
 * borrowed from an activity that happens to sit at the right index. Red for a
 * heart rate is the one place in this palette where a hue carries a meaning
 * somebody already has, and the pair was validated on its own: adjacent CVD ΔE
 * 22.7 light / 19.5 dark, well clear of the floor.
 */
export function sensorColor(sensor: 'heart' | 'cadence', dark: boolean): string {
  const slot = SLOTS[sensor === 'heart' ? 7 : 6] ?? FALLBACK
  return dark ? slot.dark : slot.light
}
