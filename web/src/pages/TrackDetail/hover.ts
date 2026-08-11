import { useMemo, useSyncExternalStore } from 'react'

/**
 * Where the pointer is on the track, kept outside React on purpose.
 *
 * The obvious implementation is `useState` on the detail page. It also
 * re-renders the whole page on every mouse move: the map, the chart, the metric
 * cards, the classification panel and the source table, sixty times a second,
 * so that one dot can move. On a long track that is where the interface stops
 * feeling immediate.
 *
 * So the hovered sample is a tiny store of its own. The map moves its marker
 * imperatively through MapLibre and never re-renders; the chart moves its
 * cursor through ECharts and never re-renders; the one line of text that has to
 * say what is under the pointer subscribes and re-renders alone.
 *
 * This is deliberately *not* a state library. There is one value, it is derived
 * from a pointer rather than from the archive, and nothing else in the
 * application may read it -- browser state is never business authority, and a
 * hover is the clearest case of state that belongs to a gesture rather than to
 * the data.
 */

export interface HoverStore {
  /** The hovered sample's index in the flattened series, or `null`. */
  read: () => number | null
  /** Move the hover. Notifies subscribers only when the value actually changes. */
  set: (index: number | null) => void
  subscribe: (listener: () => void) => () => void
}

export function createHoverStore(): HoverStore {
  let current: number | null = null
  const listeners = new Set<() => void>()
  return {
    read: () => current,
    set(index) {
      // A mouse move that lands on the same sample is not a change, and
      // notifying anyway is how a throttled interaction becomes an unthrottled
      // one.
      if (index === current) return
      current = index
      for (const listener of listeners) listener()
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => {
        listeners.delete(listener)
      }
    },
  }
}

/** Create one store per mounted track. */
export function useHoverStore(): HoverStore {
  return useMemo(() => createHoverStore(), [])
}

/**
 * Subscribe to the hovered sample.
 *
 * Only the component that calls this re-renders when the pointer moves, which
 * is the whole point of the store existing.
 */
export function useHovered(store: HoverStore): number | null {
  return useSyncExternalStore(store.subscribe, store.read, store.read)
}
