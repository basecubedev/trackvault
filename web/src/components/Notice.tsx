/**
 * A visible, unalarming statement about the archive's own state.
 *
 * A `div` rather than a paragraph: a notice about incomplete data carries the
 * instruction that fixes it, and that instruction is a `<details>` a reader can
 * ignore. Nesting one inside a `<p>` is invalid markup that browsers silently
 * reshape, which moves the disclosure out of the box it belongs to.
 */
export function Notice({
  children,
  tone = 'warn',
}: {
  children: React.ReactNode
  tone?: 'warn' | 'error'
}) {
  return (
    <div className={`notice${tone === 'error' ? ' notice--error' : ''}`} role="status">
      {children}
    </div>
  )
}
