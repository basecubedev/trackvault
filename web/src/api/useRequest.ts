import { useCallback, useEffect, useState } from 'react'
import { ApiError } from './client'

/**
 * Running one archive request and rendering the three states it can be in.
 *
 * No state library. There is one server, every value it returns is
 * recomputable from it, and the browser holds nothing the backend cannot
 * produce again -- so a store would be a cache of the truth rather than the
 * truth, which is the frontend's version of a second authority.
 */

export interface RequestState<T> {
  readonly data: T | null
  readonly loading: boolean
  readonly error: string | null
  readonly reload: () => void
}

export function useRequest<T>(
  run: (signal: AbortSignal) => Promise<T>,
  dependencies: readonly unknown[],
): RequestState<T> {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  const reload = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    run(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) {
          setData(value)
          setLoading(false)
        }
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setError(describe(cause))
        setLoading(false)
      })
    return () => {
      controller.abort()
    }
    // The caller states its own dependencies, because what a request depends on
    // is the query it describes rather than the identity of a closure. A lint
    // rule cannot see that, and inlining every request into its component to
    // satisfy one would be the wrong trade.
     
  }, [...dependencies, attempt])

  return { data, loading, error, reload }
}

/** Project an error onto something a reader can act on. */
export function describe(cause: unknown): string {
  if (cause instanceof ApiError) {
    if (cause.status === 404) return 'Not found in this archive.'
    return `The archive answered ${cause.status} (${cause.code}).`
  }
  if (cause instanceof Error) return `The archive could not be reached: ${cause.message}`
  return 'The archive could not be reached.'
}
