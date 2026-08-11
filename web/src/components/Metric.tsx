import { UNAVAILABLE } from '../api/format'

/**
 * One headline number, or an honest admission that there is not one.
 *
 * A value the archive could not derive is rendered as a dash and marked as
 * unavailable to a screen reader. Rendering it as `0` would be the application
 * inventing a measurement, which is the one thing every layer below this
 * refuses to do.
 */
export function Metric({
  label,
  value,
  note,
  testId,
}: {
  label: string
  value: string
  note?: string
  testId?: string
}) {
  const unavailable = value === UNAVAILABLE
  return (
    <div className="card" {...(testId === undefined ? {} : { 'data-testid': testId })}>
      <div className="card__label">{label}</div>
      <div className={`card__value${unavailable ? ' card__value--unavailable' : ''}`}>
        {value}
        {unavailable && <span className="visually-hidden"> unavailable</span>}
      </div>
      {note !== undefined && <div className="card__note">{note}</div>}
    </div>
  )
}
