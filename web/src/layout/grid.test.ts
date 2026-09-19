import { describe, expect, it } from 'vitest'
import {
  compact,
  flow,
  grow,
  hide,
  move,
  nudge,
  overlaps,
  readingOrder,
  resize,
  sameGrid,
  show,
  type Grid,
  type Tile,
} from './grid'

function tile(widget: string, x: number, y: number, width: number, height: number): Tile {
  return { widget, x, y, width, height }
}

function grid(tiles: Tile[], columns = 12, hidden: string[] = []): Grid {
  return { columns, tiles, hidden }
}

function at(layout: Grid, widget: string): Tile {
  const found = layout.tiles.find((entry) => entry.widget === widget)
  if (!found) throw new Error(`${widget} is not placed`)
  return found
}

/** A small deterministic generator: the same seed is the same sequence on every machine. */
function random(seed: number): () => number {
  let state = seed >>> 0
  return () => {
    state = (state + 0x6d2b79f5) >>> 0
    let value = state
    value = Math.imul(value ^ (value >>> 15), value | 1)
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61)
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296
  }
}

function integer(next: () => number, below: number): number {
  return Math.floor(next() * below)
}

/** A valid arrangement of a dozen widgets, built the way the page builds one. */
function arrangement(next: () => number, columns = 12): Grid {
  const widgets = Array.from({ length: 12 }, (_, index) => ({
    widget: `w${String(index)}`,
    size: { width: 1 + integer(next, Math.min(columns, 8)), height: 1 + integer(next, 8) },
  }))
  return grid(flow(widgets, columns), columns)
}

type Step = (layout: Grid, next: () => number) => Grid

const STEPS: Step[] = [
  (layout, next) => {
    const chosen = layout.tiles[integer(next, layout.tiles.length)]
    return chosen
      ? move(layout, chosen.widget, integer(next, layout.columns), integer(next, 40))
      : layout
  },
  (layout, next) => {
    const chosen = layout.tiles[integer(next, layout.tiles.length)]
    return chosen
      ? resize(layout, chosen.widget, 1 + integer(next, layout.columns), 1 + integer(next, 10))
      : layout
  },
  (layout, next) => {
    const chosen = layout.tiles[integer(next, layout.tiles.length)]
    const directions = [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ] as const
    const [dx, dy] = directions[integer(next, 4)] ?? [0, 0]
    return chosen ? nudge(layout, chosen.widget, dx, dy) : layout
  },
  (layout, next) => {
    const chosen = layout.tiles[integer(next, layout.tiles.length)]
    return chosen ? grow(layout, chosen.widget, integer(next, 3) - 1, integer(next, 3) - 1) : layout
  },
  (layout, next) => {
    const chosen = layout.tiles[integer(next, layout.tiles.length)]
    return chosen && layout.tiles.length > 1 ? hide(layout, chosen.widget) : layout
  },
  (layout, next) => {
    const chosen = layout.hidden[integer(next, layout.hidden.length)]
    return chosen === undefined
      ? layout
      : show(layout, chosen, { width: 1 + integer(next, layout.columns), height: 2 })
  },
]

/** Everything wrong with an arrangement, so one failure names all of it. */
function problems(layout: Grid, widgets: string[]): string[] {
  const found: string[] = []
  for (const [index, entry] of layout.tiles.entries()) {
    if (entry.x < 0 || entry.y < 0 || entry.width < 1 || entry.height < 1) {
      found.push(`${entry.widget} is out of shape`)
    }
    if (entry.x + entry.width > layout.columns) found.push(`${entry.widget} leaves the grid`)
    for (const other of layout.tiles.slice(index + 1)) {
      if (overlaps(entry, other)) found.push(`${entry.widget} overlaps ${other.widget}`)
    }
  }
  const named = [...layout.tiles.map((entry) => entry.widget), ...layout.hidden].sort()
  if (named.join() !== [...widgets].sort().join()) found.push(`widgets are ${named.join()}`)
  if (JSON.stringify(compact(layout.tiles)) !== JSON.stringify(readingOrder(layout.tiles))) {
    found.push('a tile could still rise')
  }
  return found
}

function assertSound(layout: Grid, widgets: string[]): void {
  expect(problems(layout, widgets)).toEqual([])
}

describe('a grid, whatever is done to it', () => {
  it('never overlaps, never loses a widget and never leaves a gap to rise into', () => {
    for (let seed = 1; seed <= 60; seed += 1) {
      const next = random(seed)
      const columns = [12, 8, 2][seed % 3] ?? 12
      let layout = arrangement(next, columns)
      const widgets = layout.tiles.map((entry) => entry.widget)
      for (let step = 0; step < 40; step += 1) {
        const operation = STEPS[integer(next, STEPS.length)]
        if (operation) layout = operation(layout, next)
        assertSound(layout, widgets)
      }
    }
  })

  it('compacts to the same answer however often it is asked', () => {
    for (let seed = 100; seed < 130; seed += 1) {
      const tiles = arrangement(random(seed)).tiles
      expect(compact(compact(tiles))).toEqual(compact(tiles))
    }
  })

  it('does not care in which order the tiles were listed', () => {
    for (let seed = 200; seed < 230; seed += 1) {
      const tiles = arrangement(random(seed)).tiles
      expect(compact([...tiles].reverse())).toEqual(compact(tiles))
    }
  })

  it('only ever lifts a tile when compacting, never pushes one down', () => {
    for (let seed = 300; seed < 330; seed += 1) {
      const next = random(seed)
      const spread = arrangement(next).tiles.map((entry) => ({ ...entry, y: entry.y * 2 }))
      const compacted = compact(spread)
      for (const entry of spread) {
        const after = compacted.find((candidate) => candidate.widget === entry.widget)
        expect(after?.y).toBeLessThanOrEqual(entry.y)
      }
    }
  })

  it('changes nothing when a tile is moved to where it already is', () => {
    for (let seed = 400; seed < 420; seed += 1) {
      const layout = arrangement(random(seed))
      for (const entry of layout.tiles) {
        expect(sameGrid(move(layout, entry.widget, entry.x, entry.y), layout)).toBe(true)
      }
    }
  })
})

describe('moving a tile', () => {
  const stack = grid([tile('a', 0, 0, 6, 4), tile('b', 0, 4, 6, 3), tile('side', 6, 0, 6, 7)])

  it('lifts the tile below into the room a tile dragged past it left', () => {
    const moved = move(stack, 'a', 0, 3)

    expect(at(moved, 'b').y).toBe(0)
    expect(at(moved, 'a').y).toBe(3)
  })

  it('leaves the order alone when a tile is only nudged into its neighbour', () => {
    expect(sameGrid(move(stack, 'a', 0, 1), stack)).toBe(true)
  })

  it('pushes what it lands on down, below itself', () => {
    const moved = move(stack, 'b', 0, 1)

    expect(at(moved, 'b').y).toBe(0)
    expect(at(moved, 'a').y).toBe(3)
  })

  it('trades places with a tile of the same size it is dropped exactly onto', () => {
    const pair = grid([tile('map', 0, 0, 6, 9), tile('profile', 6, 0, 6, 9)])

    const swapped = move(pair, 'map', 6, 0)

    expect(at(swapped, 'map')).toMatchObject({ x: 6, y: 0 })
    expect(at(swapped, 'profile')).toMatchObject({ x: 0, y: 0 })
  })

  it('stays inside the columns however far right it is dragged', () => {
    expect(at(move(stack, 'a', 40, 0), 'a').x).toBe(6)
  })
})

describe('the keyboard', () => {
  it('trades places with an equal neighbour, and back again', () => {
    const row = grid([tile('one', 0, 0, 2, 2), tile('two', 2, 0, 2, 2), tile('three', 4, 0, 2, 2)])

    const right = nudge(row, 'one', 1, 0)
    expect(readingOrder(right.tiles).map((entry) => entry.widget)).toEqual(['two', 'one', 'three'])
    expect(sameGrid(nudge(right, 'one', -1, 0), row)).toBe(true)
  })

  it('moves a tile below its lower neighbour in one press, whatever their heights', () => {
    const stack = grid([tile('tall', 0, 0, 12, 9), tile('short', 0, 9, 12, 2)])

    const down = nudge(stack, 'tall', 0, 1)

    expect(readingOrder(down.tiles).map((entry) => entry.widget)).toEqual(['short', 'tall'])
    expect(sameGrid(nudge(down, 'tall', 0, -1), stack)).toBe(true)
  })

  it('does nothing at an edge rather than something surprising', () => {
    const single = grid([tile('only', 0, 0, 4, 2)])

    expect(sameGrid(nudge(single, 'only', -1, 0), single)).toBe(true)
    expect(sameGrid(nudge(single, 'only', 0, -1), single)).toBe(true)
    expect(sameGrid(nudge(single, 'only', 0, 1), single)).toBe(true)
  })

  it('grows and shrinks one cell at a time and never below one', () => {
    const single = grid([tile('only', 0, 0, 2, 2)])

    expect(at(grow(single, 'only', 1, 0), 'only').width).toBe(3)
    expect(at(grow(single, 'only', 0, -1), 'only').height).toBe(1)
    expect(at(grow(grow(single, 'only', 0, -1), 'only', 0, -1), 'only').height).toBe(1)
  })
})

describe('resizing a tile', () => {
  it('pushes a neighbour it grows into down, and lets tiles rise when it shrinks', () => {
    const layout = grid([tile('a', 0, 0, 6, 4), tile('b', 6, 0, 6, 4), tile('c', 0, 4, 12, 2)])

    const wider = resize(layout, 'a', 8, 4)
    expect(at(wider, 'b').y).toBeGreaterThanOrEqual(4)

    const shorter = resize(layout, 'a', 6, 2)
    expect(at(shorter, 'c').y).toBe(4)
    const both = resize(resize(layout, 'a', 6, 2), 'b', 6, 2)
    expect(at(both, 'c').y).toBe(2)
  })
})

describe('taking widgets off the page and back', () => {
  const layout = grid([tile('a', 0, 0, 6, 4), tile('b', 6, 0, 6, 4), tile('c', 0, 4, 12, 2)])

  it('lets what was below a hidden widget rise into its place', () => {
    const hidden = hide(layout, 'a')

    expect(hidden.hidden).toEqual(['a'])
    expect(hidden.tiles.map((entry) => entry.widget)).not.toContain('a')
    expect(at(hidden, 'c').y).toBe(4)
    expect(at(hide(hidden, 'b'), 'c').y).toBe(0)
  })

  it('puts a widget back in the first place it fits', () => {
    const shown = show(hide(layout, 'a'), 'a', { width: 6, height: 4 })

    expect(shown.hidden).toEqual([])
    expect(at(shown, 'a')).toMatchObject({ x: 0, y: 0 })
  })
})

describe('flowing widgets onto a grid', () => {
  it('fills rows left to right in the order it is given', () => {
    const placed = flow(
      [
        { widget: 'one', size: { width: 1, height: 2 } },
        { widget: 'two', size: { width: 1, height: 2 } },
        { widget: 'map', size: { width: 2, height: 8 } },
        { widget: 'three', size: { width: 1, height: 2 } },
      ],
      2,
    )

    expect(readingOrder(placed).map((entry) => [entry.widget, entry.x, entry.y])).toEqual([
      ['one', 0, 0],
      ['two', 1, 0],
      ['map', 0, 2],
      ['three', 0, 10],
    ])
  })

  it('keeps a widget wider than the grid inside it', () => {
    const [only] = flow([{ widget: 'wide', size: { width: 12, height: 2 } }], 2)

    expect(only).toMatchObject({ x: 0, width: 2 })
  })
})
