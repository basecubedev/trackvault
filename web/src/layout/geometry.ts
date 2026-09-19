/**
 * Where a pointer is, in cells.
 *
 * The grid is drawn by CSS, so its measurements are read back from the browser
 * rather than assumed: the columns share whatever width the page has, and on a
 * narrow screen a row grows to fit its text. What is done with those numbers
 * is plain arithmetic, tested here without a browser.
 */

export interface GridMeasure {
  /** Where the grid's box starts, in client coordinates. */
  readonly left: number
  readonly top: number
  /** One column plus one gap -- columns are equal, so this is exact. */
  readonly columnPitch: number
  readonly columnGap: number
  /** Where each drawn row starts, from the grid's top edge. */
  readonly rowStarts: readonly number[]
  /** One row plus one gap, for rows below the last one drawn. */
  readonly rowPitch: number
  readonly rowGap: number
}

/** Read a computed track list such as `48px 48px 120px` as numbers. */
export function parseTracks(value: string): number[] {
  return value
    .trim()
    .split(/\s+/)
    .map((part) => (part.endsWith('px') ? Number.parseFloat(part) : Number.NaN))
    .filter((size) => Number.isFinite(size) && size >= 0)
}

/** Where each row starts, given the rows' sizes and the gap between them. */
export function rowStartsOf(rows: readonly number[], gap: number): number[] {
  const starts: number[] = []
  let offset = 0
  for (const size of rows) {
    starts.push(offset)
    offset += size + gap
  }
  return starts
}

export function measure(element: HTMLElement, columns: number): GridMeasure {
  const box = element.getBoundingClientRect()
  const style = getComputedStyle(element)
  const columnGap = Number.parseFloat(style.columnGap) || 0
  const rowGap = Number.parseFloat(style.rowGap) || 0
  const row = Number.parseFloat(style.getPropertyValue('--layout-row')) || 48
  return {
    left: box.left,
    top: box.top,
    columnPitch: (box.width + columnGap) / Math.max(1, columns),
    columnGap,
    rowStarts: rowStartsOf(parseTracks(style.gridTemplateRows), rowGap),
    rowPitch: row + rowGap,
    rowGap,
  }
}

/** The column whose left edge is nearest to an offset from the grid's left edge. */
export function columnAt(grid: GridMeasure, offset: number): number {
  return Math.max(0, Math.round(offset / grid.columnPitch))
}

/**
 * The row whose top edge is nearest to an offset from the grid's top edge.
 *
 * Below the last row the grid has drawn, rows are counted at the unit height:
 * that is where a tile dragged past the end of the page would go.
 */
export function rowAt(grid: GridMeasure, offset: number): number {
  if (offset <= 0) return 0
  const starts = grid.rowStarts
  const last = starts.length - 1
  const lastStart = starts[last]
  if (lastStart === undefined || offset > lastStart) {
    const from = lastStart ?? 0
    const index = Math.max(0, last)
    return index + Math.max(0, Math.round((offset - from) / grid.rowPitch))
  }
  let nearest = 0
  for (const [index, start] of starts.entries()) {
    const best = starts[nearest] ?? 0
    if (Math.abs(start - offset) < Math.abs(best - offset)) nearest = index
  }
  return nearest
}

/** How many columns a tile starting at `column` spans when its right edge is at `offset`. */
export function columnsTo(grid: GridMeasure, column: number, offset: number): number {
  return Math.max(1, Math.round((offset + grid.columnGap) / grid.columnPitch) - column)
}

/** How many rows a tile starting at `row` spans when its bottom edge is at `offset`. */
export function rowsTo(grid: GridMeasure, row: number, offset: number): number {
  return Math.max(1, rowAt(grid, offset + grid.rowGap) - row)
}
