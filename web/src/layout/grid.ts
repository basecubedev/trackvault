/**
 * Arranging tiles on a grid, as plain arithmetic.
 *
 * Everything a drag, a resize or an arrow key decides is decided here, on
 * numbers, so it is tested without a browser -- the same arrangement the map's
 * hover logic and the chart's option builders already follow.
 *
 * Three rules make an arrangement one the page can draw and a reader can
 * predict:
 *
 * ```
 * no overlap    two tiles never share a cell
 * no gap        every tile sits as high as the tiles above it allow
 * one place     every widget is placed or hidden, exactly once
 * ```
 *
 * Every operation below returns an arrangement that keeps all three, whatever
 * it was asked. What the page may *not* do -- a map narrower than a map can be
 * read at -- is a limit the caller passes in; this module knows cells, not
 * widgets.
 */

export interface Tile {
  readonly widget: string
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export interface Grid {
  readonly columns: number
  readonly tiles: readonly Tile[]
  readonly hidden: readonly string[]
}

export interface Size {
  readonly width: number
  readonly height: number
}

/** The smallest and largest a widget may be drawn at on one grid. */
export interface Limits {
  readonly min: Size
  readonly max: Size
}

export function overlaps(a: Tile, b: Tile): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height
}

function sharesColumns(a: Tile, b: Tile): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width
}

function bottomOf(tiles: readonly Tile[]): number {
  return tiles.reduce((lowest, tile) => Math.max(lowest, tile.y + tile.height), 0)
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum)
}

/**
 * The tiles in the order somebody reads them: row by row, left to right.
 *
 * This is also the order the page puts them in the document, so the tab order
 * and a screen reader follow what is on screen.
 */
export function readingOrder(tiles: readonly Tile[]): Tile[] {
  return [...tiles].sort(
    (a, b) => a.y - b.y || a.x - b.x || (a.widget < b.widget ? -1 : a.widget > b.widget ? 1 : 0),
  )
}

/**
 * Lift every tile as high as the tiles above it allow.
 *
 * Each tile, in reading order, comes to rest directly below the lowest tile
 * already placed in any of its columns. On an arrangement without overlaps that
 * only ever lifts a tile; on one with overlaps it also pushes the later tile
 * clear, which is why the operations below can hand it their intermediate
 * states.
 */
export function compact(tiles: readonly Tile[]): Tile[] {
  const placed: Tile[] = []
  for (const tile of readingOrder(tiles)) {
    const y = placed
      .filter((other) => sharesColumns(other, tile))
      .reduce((lowest, other) => Math.max(lowest, other.y + other.height), 0)
    placed.push(y === tile.y ? tile : { ...tile, y })
  }
  return readingOrder(placed)
}

/**
 * Settle an arrangement around one tile that was just put somewhere.
 *
 * The anchor stays where it was put. Every other tile it now covers is pushed
 * below it, and whatever those land on is pushed further, in reading order.
 * Then everything rises into the room that is left -- the anchor included, so
 * a tile dropped into empty space under the page's last row comes to rest
 * against it rather than floating.
 */
function settle(others: readonly Tile[], anchor: Tile): Tile[] {
  const placed: Tile[] = [anchor]
  for (const tile of readingOrder(others)) {
    let current = tile
    for (;;) {
      const blockers = placed.filter((other) => overlaps(other, current))
      if (blockers.length === 0) break
      current = { ...current, y: bottomOf(blockers) }
    }
    placed.push(current)
  }
  return compact(placed)
}

function find(grid: Grid, widget: string): Tile | undefined {
  return grid.tiles.find((tile) => tile.widget === widget)
}

/** Report whether two arrangements draw the same page. Listing order is not a difference. */
export function sameGrid(a: Grid, b: Grid): boolean {
  if (a.columns !== b.columns || a.tiles.length !== b.tiles.length) return false
  const left = readingOrder(a.tiles)
  const right = readingOrder(b.tiles)
  const tilesMatch = left.every((tile, index) => {
    const other = right[index]
    return (
      other !== undefined &&
      tile.widget === other.widget &&
      tile.x === other.x &&
      tile.y === other.y &&
      tile.width === other.width &&
      tile.height === other.height
    )
  })
  return tilesMatch && [...a.hidden].sort().join('\n') === [...b.hidden].sort().join('\n')
}

/**
 * Put one tile at a new position.
 *
 * Three things happen to what it lands on, in this order of preference:
 *
 * ```
 * same size, same cell   the two trade places
 * dragged downwards      a tile it passes hops above it, into the room it left
 * anything else          the covered tile is pushed below it
 * ```
 *
 * Without the second rule a tile could never be dragged below its neighbour:
 * the neighbour would be pushed down ahead of it and everything would rise
 * back to where it started.
 */
export function move(grid: Grid, widget: string, x: number, y: number): Grid {
  const tile = find(grid, widget)
  if (!tile) return grid
  const target: Tile = {
    ...tile,
    x: clamp(Math.round(x), 0, grid.columns - tile.width),
    y: Math.max(0, Math.round(y)),
  }
  if (target.x === tile.x && target.y === tile.y) return grid
  let others = grid.tiles.filter((other) => other.widget !== widget)

  const twin = others.find(
    (other) =>
      other.x === target.x &&
      other.y === target.y &&
      other.width === target.width &&
      other.height === target.height,
  )
  if (twin) {
    others = others.map((other) => (other === twin ? { ...other, x: tile.x, y: tile.y } : other))
  } else if (target.y > tile.y) {
    for (const covered of readingOrder(others.filter((other) => overlaps(other, target)))) {
      const above: Tile = { ...covered, y: target.y - covered.height }
      const free =
        above.y >= 0 &&
        !overlaps(above, target) &&
        others.every((other) => other.widget === covered.widget || !overlaps(other, above))
      if (free) others = others.map((other) => (other.widget === covered.widget ? above : other))
    }
  }
  return { ...grid, tiles: settle(others, target) }
}

function limited(size: Size, columns: number, x: number, limits?: Limits): Size {
  const width = limits ? clamp(size.width, limits.min.width, limits.max.width) : size.width
  const height = limits ? clamp(size.height, limits.min.height, limits.max.height) : size.height
  return {
    width: clamp(Math.round(width), 1, columns - x),
    height: Math.max(1, Math.round(height)),
  }
}

/** Give one tile a new size, keeping its top-left corner where it is. */
export function resize(
  grid: Grid,
  widget: string,
  width: number,
  height: number,
  limits?: Limits,
): Grid {
  const tile = find(grid, widget)
  if (!tile) return grid
  const size = limited({ width, height }, grid.columns, tile.x, limits)
  if (size.width === tile.width && size.height === tile.height) return grid
  const others = grid.tiles.filter((other) => other.widget !== widget)
  return { ...grid, tiles: settle(others, { ...tile, ...size }) }
}

/** Grow or shrink one tile by whole cells -- what an arrow key does with Shift held. */
export function grow(grid: Grid, widget: string, dx: number, dy: number, limits?: Limits): Grid {
  const tile = find(grid, widget)
  if (!tile) return grid
  return resize(grid, widget, tile.width + dx, tile.height + dy, limits)
}

/**
 * Move one tile a step in a direction -- what an arrow key does.
 *
 * A neighbour of the same size directly in that direction trades places with
 * it, so a row of equal cards can be reordered one press at a time. Otherwise
 * the tile moves by the smallest step that changes the arrangement at all: a
 * press that visibly did nothing would teach a keyboard user that the key is
 * broken.
 */
export function nudge(grid: Grid, widget: string, dx: number, dy: number): Grid {
  const tile = find(grid, widget)
  if (!tile || (dx === 0 && dy === 0)) return grid
  const neighbour = grid.tiles.find(
    (other) =>
      other.widget !== widget &&
      other.width === tile.width &&
      other.height === tile.height &&
      (dx !== 0
        ? other.y === tile.y && other.x === tile.x + (dx > 0 ? tile.width : -other.width)
        : other.x === tile.x && other.y === tile.y + (dy > 0 ? tile.height : -other.height)),
  )
  if (neighbour) return move(grid, widget, neighbour.x, neighbour.y)
  const reach = dx !== 0 ? grid.columns : bottomOf(grid.tiles) + 1
  for (let step = 1; step <= reach; step += 1) {
    const moved = move(grid, widget, tile.x + dx * step, tile.y + dy * step)
    if (!sameGrid(moved, grid)) return moved
  }
  return grid
}

/** Take a widget off the page. What was below it rises into its place. */
export function hide(grid: Grid, widget: string): Grid {
  if (!find(grid, widget)) return grid
  return {
    ...grid,
    tiles: compact(grid.tiles.filter((tile) => tile.widget !== widget)),
    hidden: [...grid.hidden.filter((name) => name !== widget), widget],
  }
}

/** Find the first cell, row by row, where a tile of this size fits. */
export function firstFree(tiles: readonly Tile[], size: Size, columns: number): { x: number; y: number } {
  const width = clamp(size.width, 1, columns)
  const lowest = bottomOf(tiles)
  for (let y = 0; y <= lowest; y += 1) {
    for (let x = 0; x + width <= columns; x += 1) {
      const candidate: Tile = { widget: '', x, y, width, height: size.height }
      if (tiles.every((tile) => !overlaps(tile, candidate))) return { x, y }
    }
  }
  return { x: 0, y: lowest }
}

/** Put a hidden widget back, in the first place it fits. */
export function show(grid: Grid, widget: string, size: Size): Grid {
  if (find(grid, widget)) return grid
  const width = clamp(size.width, 1, grid.columns)
  const height = Math.max(1, size.height)
  const { x, y } = firstFree(grid.tiles, { width, height }, grid.columns)
  return {
    ...grid,
    tiles: compact([...grid.tiles, { widget, x, y, width, height }]),
    hidden: grid.hidden.filter((name) => name !== widget),
  }
}

/**
 * Place widgets one after another, each in the first cell it fits.
 *
 * How a narrower screen gets an arrangement nobody made for it: the order is
 * kept and the rows fill the way text does.
 */
export function flow(
  widgets: readonly { readonly widget: string; readonly size: Size }[],
  columns: number,
): Tile[] {
  const placed: Tile[] = []
  for (const { widget, size } of widgets) {
    const width = clamp(size.width, 1, columns)
    const height = Math.max(1, size.height)
    const { x, y } = firstFree(placed, { width, height }, columns)
    placed.push({ widget, x, y, width, height })
  }
  return compact(placed)
}
