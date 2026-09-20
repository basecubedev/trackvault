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

/**
 * What one finished request left behind, and which request that was.
 *
 * The answer and the question it answers are one value. Asked separately, they
 * drift for exactly one render: the dependencies change, the page renders the
 * previous answer as though it were settled, and only then does an effect run
 * to say it is loading again. Kept together, "this answer is not for the
 * request you are asking about" is something render can simply read.
 */
interface Outcome<T> {
  readonly data: T | null
  readonly error: string | null
  readonly request: readonly unknown[]
}

export function useRequest<T>(
  run: (signal: AbortSignal) => Promise<T>,
  dependencies: readonly unknown[],
): RequestState<T> {
  const [outcome, setOutcome] = useState<Outcome<T> | null>(null)
  const [attempt, setAttempt] = useState(0)

  const reload = useCallback(() => {
    setAttempt((value) => value + 1)
  }, [])

  const request = [...dependencies, attempt]
  const settled = outcome !== null && sameRequest(outcome.request, request)

  useEffect(() => {
    const controller = new AbortController()
    const asked = [...dependencies, attempt]
    run(controller.signal)
      .then((value) => {
        if (controller.signal.aborted) return
        setOutcome({ data: value, error: null, request: asked })
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return
        setOutcome((previous) => ({
          data: previous?.data ?? null,
          error: describe(cause),
          request: asked,
        }))
      })
    return () => {
      controller.abort()
    }
    // The caller states its own dependencies, because what a request depends on
    // is the query it describes rather than the identity of a closure. A lint
    // rule cannot see that, and inlining every request into its component to
    // satisfy one would be the wrong trade.
     
  }, [...dependencies, attempt])

  return {
    // A failed request keeps showing what was there before, which is what the
    // error notice is written to sit beside.
    data: outcome?.data ?? null,
    loading: !settled,
    error: settled ? outcome.error : null,
    reload,
  }
}

/** Whether two dependency lists describe the same question. */
function sameRequest(asked: readonly unknown[], wanted: readonly unknown[]): boolean {
  return (
    asked.length === wanted.length && asked.every((value, index) => Object.is(value, wanted[index]))
  )
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
