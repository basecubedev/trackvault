import type { StateLabel } from '../api/labels'

/**
 * A state, shown as a glyph and a word.
 *
 * The glyph is not decoration. Colour alone would leave "current" and "invalid"
 * indistinguishable to a reader who cannot tell green from red, and these are
 * exactly the two states somebody has to act on differently.
 */
export function Badge({ label }: { label: StateLabel }) {
  return (
    <span className={`badge badge--${label.tone}`} title={label.hint}>
      <span className="badge__mark" aria-hidden="true">
        {label.mark}
      </span>
      {label.text}
    </span>
  )
}
