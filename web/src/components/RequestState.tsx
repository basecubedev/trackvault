import type { ReactNode } from 'react'
import type { RequestState as State } from '../api/useRequest'

/**
 * One place that renders "loading", "that failed" and "try again".
 *
 * Every surface reads from the archive and every one of them can be waiting,
 * empty or broken. Written per page, that becomes five slightly different
 * spellings of `fetch failed` and one page that forgot the retry button --
 * which is the page somebody hits when their laptop wakes up on a different
 * network.
 *
 * A failed *read* is always safe to repeat, so a retry is offered without
 * asking. Nothing here retries a write.
 */
export function RequestState<T>({
  request,
  label,
  children,
}: {
  request: State<T>
  /** What was being read, for the message and for a screen reader. */
  label: string
  children: (data: T) => ReactNode
}) {
  if (request.error !== null) {
    return (
      <div className="request-state" role="alert" data-testid="request-error">
        <p className="notice notice--error">{request.error}</p>
        <button type="button" onClick={request.reload} data-testid="retry">
          Try loading {label} again
        </button>
      </div>
    )
  }
  if (request.data === null) {
    return (
      <p className="request-state muted" role="status" data-testid="request-loading">
        Loading {label}…
      </p>
    )
  }
  return <>{children(request.data)}</>
}

/**
 * The same three states, without taking over the space around them.
 *
 * Used where a surface has content worth keeping on screen while one part of it
 * reloads -- a track's map while its analysis is refetched, for instance.
 */
export function InlineRequestState<T>({
  request,
  label,
}: {
  request: State<T>
  label: string
}) {
  if (request.error !== null) {
    return (
      <span className="muted" role="alert">
        {request.error}{' '}
        <button type="button" className="link" onClick={request.reload}>
          Retry
        </button>
      </span>
    )
  }
  return (
    <span className="muted" role="status">
      Loading {label}…
    </span>
  )
}
