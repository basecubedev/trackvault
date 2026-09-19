import { describe, expect, it } from 'vitest'
import { columnAt, columnsTo, parseTracks, rowAt, rowStartsOf, rowsTo, type GridMeasure } from './geometry'

function grid(rows: number[] = [], columnPitch = 100): GridMeasure {
  return {
    left: 0,
    top: 0,
    columnPitch,
    columnGap: 16,
    rowStarts: rowStartsOf(rows, 16),
    rowPitch: 64,
    rowGap: 16,
  }
}

describe('reading the grid the browser drew', () => {
  it('reads the computed rows, and ignores what is not a length', () => {
    expect(parseTracks('48px 48px 120.5px')).toEqual([48, 48, 120.5])
    expect(parseTracks('none')).toEqual([])
  })

  it('snaps a position to the nearest column and row', () => {
    const uniform = grid([48, 48, 48, 48])

    expect(columnAt(uniform, 140)).toBe(1)
    expect(columnAt(uniform, -30)).toBe(0)
    expect(rowAt(uniform, 70)).toBe(1)
    expect(rowAt(uniform, -5)).toBe(0)
  })

  it('counts a row that grew to fit its text as one row', () => {
    const grown = grid([48, 300, 48])

    expect(rowAt(grown, 200)).toBe(1)
    expect(rowAt(grown, 360)).toBe(2)
  })

  it('keeps counting below the last row drawn', () => {
    expect(rowAt(grid([48, 48]), 64 + 64 * 3)).toBe(4)
    expect(rowAt(grid(), 64 * 5)).toBe(5)
  })

  it('turns a dragged corner into a span', () => {
    const uniform = grid([48, 48, 48, 48, 48])

    expect(columnsTo(uniform, 0, 284)).toBe(3)
    expect(rowsTo(uniform, 1, 64 * 3 - 16)).toBe(2)
    expect(columnsTo(uniform, 2, 10)).toBe(1)
  })
})
