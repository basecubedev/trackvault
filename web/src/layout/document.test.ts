import { describe, expect, it } from 'vitest'
import {
  COLUMNS,
  derive,
  draftOf,
  gridOf,
  normalize,
  resetGrid,
  toDocument,
  visibleTiles,
  widthClassOf,
  withGrid,
  type Catalog,
  type WidgetSpec,
} from './document'
import { move, readingOrder, sameGrid, type Grid, type Size } from './grid'

function spec(id: string, wide: Size, small: Size = { width: 1, height: wide.height }): WidgetSpec {
  const sizes = { size: wide, min: { width: 1, height: 1 }, max: { width: 12, height: 20 } }
  return {
    id,
    label: id,
    description: `The ${id}`,
    sizes: {
      wide: sizes,
      medium: { ...sizes, size: { width: Math.min(wide.width, 8), height: wide.height } },
      narrow: { ...sizes, size: small, max: { width: 2, height: 20 } },
    },
  }
}

const CATALOG: Catalog = [
  { ...spec('metric', { width: 2, height: 2 }), sizes: {
    wide: { size: { width: 2, height: 2 }, min: { width: 2, height: 2 }, max: { width: 6, height: 4 } },
    medium: { size: { width: 2, height: 2 }, min: { width: 2, height: 2 }, max: { width: 8, height: 4 } },
    narrow: { size: { width: 1, height: 2 }, min: { width: 1, height: 2 }, max: { width: 2, height: 4 } },
  } },
  spec('map', { width: 6, height: 9 }, { width: 2, height: 8 }),
  spec('profile', { width: 6, height: 9 }, { width: 2, height: 8 }),
  spec('source', { width: 12, height: 6 }, { width: 2, height: 6 }),
]

const DEFAULT: Grid = {
  columns: 12,
  tiles: [
    { widget: 'metric', x: 0, y: 0, width: 2, height: 2 },
    { widget: 'map', x: 2, y: 0, width: 6, height: 9 },
    { widget: 'profile', x: 8, y: 0, width: 4, height: 9 },
    { widget: 'source', x: 0, y: 9, width: 12, height: 6 },
  ],
  hidden: [],
}

function order(grid: Grid): string[] {
  return readingOrder(grid.tiles).map((tile) => tile.widget)
}

describe('the width classes', () => {
  it('follow the width the page is drawn at, not the window', () => {
    expect(widthClassOf(1400)).toBe('wide')
    expect(widthClassOf(960)).toBe('wide')
    expect(widthClassOf(959)).toBe('medium')
    expect(widthClassOf(600)).toBe('medium')
    expect(widthClassOf(599)).toBe('narrow')
    expect(widthClassOf(0)).toBe('narrow')
  })
})

describe('an arrangement read back from the archive', () => {
  it('draws the default when nobody arranged anything', () => {
    const draft = draftOf(null, DEFAULT, CATALOG)

    expect(sameGrid(gridOf(draft, 'wide', CATALOG), normalize(DEFAULT, CATALOG, 'wide'))).toBe(true)
    expect(draft.medium).toBeNull()
    expect(draft.narrow).toBeNull()
  })

  it('forgets a widget this page no longer has', () => {
    const stored: Grid = {
      ...DEFAULT,
      tiles: [...DEFAULT.tiles, { widget: 'retired', x: 0, y: 17, width: 4, height: 2 }],
      hidden: ['also-retired'],
    }

    const grid = normalize(stored, CATALOG, 'wide')

    expect(order(grid)).not.toContain('retired')
    expect(grid.hidden).toEqual([])
  })

  it('shows a widget a later release added, below everything the owner arranged', () => {
    const stored: Grid = {
      ...DEFAULT,
      tiles: DEFAULT.tiles.filter((tile) => tile.widget !== 'source'),
    }

    const grid = normalize(stored, CATALOG, 'wide')

    expect(order(grid).at(-1)).toBe('source')
    expect(grid.hidden).toEqual([])
  })

  it('puts several added widgets side by side, not in a column of their own', () => {
    const catalog: Catalog = [...CATALOG, spec('extra', { width: 2, height: 2 })]
    const stored: Grid = {
      ...DEFAULT,
      tiles: DEFAULT.tiles.filter((tile) => tile.widget !== 'metric'),
    }

    const grid = normalize(stored, catalog, 'wide')

    const metric = grid.tiles.find((tile) => tile.widget === 'metric')
    const extra = grid.tiles.find((tile) => tile.widget === 'extra')
    expect(metric?.y).toBe(extra?.y)
    expect(metric?.y).toBe(15)
  })

  it('keeps a widget the owner hid hidden', () => {
    const stored: Grid = {
      ...DEFAULT,
      tiles: DEFAULT.tiles.filter((tile) => tile.widget !== 'profile'),
      hidden: ['profile'],
    }

    expect(normalize(stored, CATALOG, 'wide').hidden).toEqual(['profile'])
    expect(derive(normalize(stored, CATALOG, 'wide'), CATALOG, 'narrow').hidden).toEqual([
      'profile',
    ])
  })

  it('brings a size the page cannot draw back inside what the widget allows', () => {
    const stored: Grid = {
      ...DEFAULT,
      tiles: DEFAULT.tiles.map((tile) =>
        tile.widget === 'metric' ? { ...tile, x: 11, width: 1, height: 9 } : tile,
      ),
    }

    const metric = normalize(stored, CATALOG, 'wide').tiles.find((tile) => tile.widget === 'metric')

    expect(metric).toMatchObject({ width: 2, height: 4, x: 10 })
  })

  it('reads an arrangement once and for all -- normalizing again changes nothing', () => {
    const stored: Grid = {
      columns: 12,
      tiles: [
        { widget: 'map', x: 3, y: 5, width: 6, height: 9 },
        { widget: 'profile', x: 4, y: 7, width: 6, height: 9 },
        { widget: 'nonsense', x: 0, y: 0, width: 1, height: 1 },
      ],
      hidden: ['source', 'source'],
    }

    const once = normalize(stored, CATALOG, 'wide')

    expect(sameGrid(normalize(once, CATALOG, 'wide'), once)).toBe(true)
  })

  it('re-flows a grid stored with another number of columns, keeping its order', () => {
    const foreign: Grid = {
      columns: 4,
      tiles: [
        { widget: 'source', x: 0, y: 0, width: 4, height: 2 },
        { widget: 'map', x: 0, y: 2, width: 2, height: 4 },
        { widget: 'metric', x: 2, y: 2, width: 2, height: 2 },
        { widget: 'profile', x: 0, y: 6, width: 4, height: 4 },
      ],
      hidden: [],
    }

    const grid = normalize(foreign, CATALOG, 'wide')

    expect(grid.columns).toBe(COLUMNS.wide)
    expect(order(grid)).toEqual(['source', 'map', 'metric', 'profile'])
  })
})

describe('a narrower arrangement nobody made', () => {
  it('keeps the order of the wide one and uses each widget’s own size for the width', () => {
    const wide = normalize(DEFAULT, CATALOG, 'wide')

    const narrow = derive(wide, CATALOG, 'narrow')

    expect(narrow.columns).toBe(COLUMNS.narrow)
    expect(order(narrow)).toEqual(['metric', 'map', 'profile', 'source'])
    expect(narrow.tiles.find((tile) => tile.widget === 'map')?.width).toBe(2)
  })

  it('follows the wide arrangement when that one changes', () => {
    const draft = draftOf(null, DEFAULT, CATALOG)
    const raised = move(draft.wide, 'source', 0, 0)

    const medium = gridOf(withGrid(draft, 'wide', raised), 'medium', CATALOG)

    expect(order(medium)[0]).toBe('source')
  })
})

describe('what is saved', () => {
  it('is nothing at all when the arrangement is the default', () => {
    expect(toDocument(draftOf(null, DEFAULT, CATALOG), DEFAULT, CATALOG)).toBeNull()
  })

  it('is the arrangement the owner made, and nothing derived', () => {
    const draft = draftOf(null, DEFAULT, CATALOG)
    const changed = withGrid(draft, 'wide', move(draft.wide, 'map', 6, 2))

    const document = toDocument(changed, DEFAULT, CATALOG)

    expect(document?.medium).toBeNull()
    expect(document?.narrow).toBeNull()
    expect(document?.wide?.tiles.find((tile) => tile.widget === 'map')).toMatchObject({ x: 6 })
  })

  it('stores a narrower arrangement only while it differs from what would be derived', () => {
    const draft = draftOf(null, DEFAULT, CATALOG)
    const narrow = gridOf(draft, 'narrow', CATALOG)

    expect(toDocument(withGrid(draft, 'narrow', narrow), DEFAULT, CATALOG)).toBeNull()
    const reordered = withGrid(draft, 'narrow', move(narrow, 'source', 0, 0))
    expect(toDocument(reordered, DEFAULT, CATALOG)?.narrow).not.toBeNull()
  })

  it('puts back what the owner sees when they reset it, and only that', () => {
    const draft = draftOf(null, DEFAULT, CATALOG)
    const changed = withGrid(
      withGrid(draft, 'wide', move(draft.wide, 'map', 6, 2)),
      'narrow',
      move(gridOf(draft, 'narrow', CATALOG), 'source', 0, 0),
    )

    const reset = resetGrid(changed, 'narrow', DEFAULT, CATALOG)

    expect(reset.narrow).toBeNull()
    expect(sameGrid(reset.wide, changed.wide)).toBe(true)
    expect(toDocument(resetGrid(reset, 'wide', DEFAULT, CATALOG), DEFAULT, CATALOG)).toBeNull()
  })
})

describe('a widget this track has nothing for', () => {
  it('leaves no hole: what was below it rises', () => {
    const grid = normalize(DEFAULT, CATALOG, 'wide')

    const withoutMap = visibleTiles(grid, (widget) => widget !== 'map')
    const withoutEither = visibleTiles(grid, (widget) => widget !== 'map' && widget !== 'profile')

    expect(withoutMap.map((tile) => tile.widget)).toEqual(['metric', 'profile', 'source'])
    expect(withoutMap.find((tile) => tile.widget === 'source')?.y).toBe(9)
    expect(withoutEither.find((tile) => tile.widget === 'source')?.y).toBe(2)
    expect(grid.tiles.find((tile) => tile.widget === 'map')).toBeDefined()
  })
})
