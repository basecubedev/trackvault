import { useCallback, useState } from 'react'
import { api, type LayoutReading, type PageLayout } from '../../api/client'
import { describe, useRequest } from '../../api/useRequest'
import type { StoredArrangement } from '../../layout/document'
import type { Grid } from '../../layout/grid'

/**
 * The owner's arrangement of the track report, as the archive holds it.
 *
 * Three answers come back and each is drawn differently: nothing stored is the
 * default, a stored arrangement is drawn as stored, and one this build cannot
 * read is drawn as the default *with a word about it* -- silently drawing the
 * default would let the next save overwrite the owner's work without anybody
 * having been told it was there.
 *
 * An arrangement that could not be loaded at all is not offered for editing.
 * Saving over a document nobody could read is how an outage turns into lost
 * work.
 */
export interface TrackLayout {
  /** Whether the archive has answered, one way or the other. */
  readonly settled: boolean
  readonly stored: StoredArrangement | null
  readonly unreadable: boolean
  /** Why the arrangement cannot be changed right now, if it cannot. */
  readonly blocked: string | null
  readonly save: (document: StoredArrangement | null) => Promise<void>
}

function toGrid(grid: Grid): NonNullable<PageLayout['wide']> {
  return {
    columns: grid.columns,
    tiles: grid.tiles.map(({ widget, x, y, width, height }) => ({ widget, x, y, width, height })),
    hidden: [...grid.hidden],
  }
}

function toDocument(arrangement: StoredArrangement): PageLayout {
  return {
    wide: arrangement.wide === null ? null : toGrid(arrangement.wide),
    medium: arrangement.medium === null ? null : toGrid(arrangement.medium),
    narrow: arrangement.narrow === null ? null : toGrid(arrangement.narrow),
  }
}

export function useTrackLayout(): TrackLayout {
  const request = useRequest((signal) => api.readTrackLayout(signal), [])
  const [latest, setLatest] = useState<LayoutReading | null>(null)
  const reading = latest ?? request.data

  const save = useCallback(async (document: StoredArrangement | null) => {
    try {
      setLatest(
        document === null
          ? await api.resetTrackLayout()
          : await api.saveTrackLayout(toDocument(document)),
      )
    } catch (cause: unknown) {
      throw new Error(`The layout could not be saved. ${describe(cause)}`)
    }
  }, [])

  return {
    settled: latest !== null || !request.loading,
    stored: reading?.state === 'custom' ? reading.layout : null,
    unreadable: reading?.state === 'unreadable',
    blocked:
      latest === null && request.error !== null
        ? 'The saved layout could not be loaded, so the page cannot be rearranged right now.'
        : null,
    save,
  }
}
