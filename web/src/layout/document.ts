/**
 * An arrangement as the archive stores it, reconciled with the widgets a page has.
 *
 * The archive vouches for the shape of what it stores -- a grid, no overlaps,
 * every widget once -- and knows nothing about what a widget is. This is where
 * the page's own vocabulary meets it:
 *
 * ```
 * a widget this page no longer has     forgotten
 * a widget a later release added       shown, below everything arranged
 * a size a widget cannot be read at    brought back inside its limits
 * a width class nobody arranged        derived from the wide arrangement
 * ```
 *
 * A widget a later release adds is *shown*, never silently hidden: the stored
 * arrangement records what the owner hid on purpose, and a widget they have
 * never seen is not one of those.
 */
import { compact, flow, readingOrder, sameGrid, type Grid, type Limits, type Size, type Tile } from './grid'

export type WidthClass = 'wide' | 'medium' | 'narrow'

export const WIDTH_CLASSES: readonly WidthClass[] = ['wide', 'medium', 'narrow']

/** How many columns each width class divides the page into. */
export const COLUMNS: Readonly<Record<WidthClass, number>> = { wide: 12, medium: 8, narrow: 2 }

/**
 * The width, in CSS pixels, of the area the arrangement is drawn in -- not of
 * the window. The same report is a page of its own and a row in the track list,
 * and the row is narrower than the window it sits in.
 */
const WIDE_FROM = 960
const MEDIUM_FROM = 600

export function widthClassOf(width: number): WidthClass {
  if (width >= WIDE_FROM) return 'wide'
  if (width >= MEDIUM_FROM) return 'medium'
  return 'narrow'
}

export interface WidgetSizes {
  /** What the widget is drawn at when nobody chose. */
  readonly size: Size
  readonly min: Size
  readonly max: Size
}

/** One widget a page offers, as the arrangement needs to know it. */
export interface WidgetSpec {
  readonly id: string
  /** Its name in the editing bar and in the list of widgets to add. */
  readonly label: string
  /** One sentence in the list of widgets to add, so nobody adds one to find out. */
  readonly description: string
  readonly sizes: Readonly<Record<WidthClass, WidgetSizes>>
}

export type Catalog = readonly WidgetSpec[]

/** Everything being arranged right now: the widest is always resolved to a grid. */
export interface Draft {
  readonly wide: Grid
  readonly medium: Grid | null
  readonly narrow: Grid | null
}

/**
 * An arrangement as the archive stores it, where every class may be absent.
 *
 * `null` means "nobody arranged this one" -- the page draws its own default, or
 * derives the class from one that was arranged. Somebody who only rearranges
 * their phone stores a narrow arrangement and *no* wide one, so a later
 * improvement to the wide default still reaches them.
 */
export interface StoredArrangement {
  readonly wide: Grid | null
  readonly medium: Grid | null
  readonly narrow: Grid | null
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum)
}

function specs(catalog: Catalog): Map<string, WidgetSpec> {
  return new Map(catalog.map((spec) => [spec.id, spec]))
}

/** The smallest and largest one widget may be on a grid of this class. */
export function limitsOf(spec: WidgetSpec, widthClass: WidthClass): Limits {
  const columns = COLUMNS[widthClass]
  const { min, max } = spec.sizes[widthClass]
  const minWidth = clamp(min.width, 1, columns)
  return {
    min: { width: minWidth, height: Math.max(1, min.height) },
    max: {
      width: clamp(max.width, minWidth, columns),
      height: Math.max(min.height, max.height),
    },
  }
}

/** A widget's default size on a grid of this class, inside its limits. */
export function defaultSize(spec: WidgetSpec, widthClass: WidthClass): Size {
  const { min, max } = limitsOf(spec, widthClass)
  const { size } = spec.sizes[widthClass]
  return {
    width: clamp(size.width, min.width, max.width),
    height: clamp(size.height, min.height, max.height),
  }
}

/**
 * Lay out a class nobody arranged, from an arrangement somebody did.
 *
 * The order is kept and every widget takes its own default size for the
 * narrower grid. Shrinking the wide sizes proportionally would give a phone a
 * map two columns of a twelfth wide.
 */
export function derive(source: Grid, catalog: Catalog, widthClass: WidthClass): Grid {
  const known = specs(catalog)
  const seen = new Set<string>()
  const order: WidgetSpec[] = []
  for (const tile of readingOrder(source.tiles)) {
    const spec = known.get(tile.widget)
    if (spec && !seen.has(spec.id)) {
      seen.add(spec.id)
      order.push(spec)
    }
  }
  const hidden = unique(source.hidden.filter((id) => known.has(id) && !seen.has(id)))
  for (const id of hidden) seen.add(id)
  for (const spec of catalog) if (!seen.has(spec.id)) order.push(spec)
  return {
    columns: COLUMNS[widthClass],
    tiles: flow(
      order.map((spec) => ({ widget: spec.id, size: defaultSize(spec, widthClass) })),
      COLUMNS[widthClass],
    ),
    hidden,
  }
}

function unique(values: readonly string[]): string[] {
  return [...new Set(values)]
}

/**
 * Bring a stored grid into a shape this page can draw.
 *
 * Idempotent: an arrangement that went through here once comes out of it
 * unchanged, so reading, drawing and saving it again never drifts.
 */
export function normalize(stored: Grid, catalog: Catalog, widthClass: WidthClass): Grid {
  const columns = COLUMNS[widthClass]
  if (stored.columns !== columns) return derive(stored, catalog, widthClass)
  const known = specs(catalog)
  const seen = new Set<string>()
  const tiles: Tile[] = []
  for (const tile of readingOrder(stored.tiles)) {
    const spec = known.get(tile.widget)
    if (!spec || seen.has(spec.id)) continue
    seen.add(spec.id)
    const { min, max } = limitsOf(spec, widthClass)
    const width = clamp(tile.width, min.width, max.width)
    const height = clamp(tile.height, min.height, max.height)
    tiles.push({
      widget: spec.id,
      x: clamp(tile.x, 0, columns - width),
      y: Math.max(0, tile.y),
      width,
      height,
    })
  }
  const hidden = unique(stored.hidden.filter((id) => known.has(id) && !seen.has(id)))
  for (const id of hidden) seen.add(id)
  const arranged = compact(tiles)
  const bottom = arranged.reduce((lowest, tile) => Math.max(lowest, tile.y + tile.height), 0)
  // Below everything the owner arranged, side by side the way the page's own
  // default would put them -- six small cards stacked in one column would read
  // as a list of something else.
  const added = flow(
    catalog
      .filter((spec) => !seen.has(spec.id))
      .map((spec) => ({ widget: spec.id, size: defaultSize(spec, widthClass) })),
    columns,
  ).map((tile) => ({ ...tile, y: tile.y + bottom }))
  return { columns, tiles: compact([...arranged, ...added]), hidden }
}

/** Everything the owner arranged, read out of a stored document -- or the default. */
export function draftOf(
  stored: StoredArrangement | null,
  defaults: Grid,
  catalog: Catalog,
): Draft {
  return {
    wide: normalize(stored?.wide ?? defaults, catalog, 'wide'),
    medium: stored?.medium ? normalize(stored.medium, catalog, 'medium') : null,
    narrow: stored?.narrow ? normalize(stored.narrow, catalog, 'narrow') : null,
  }
}

/** The grid one width class draws: its own arrangement, or one derived from the wide one. */
export function gridOf(draft: Draft, widthClass: WidthClass, catalog: Catalog): Grid {
  if (widthClass === 'wide') return draft.wide
  return draft[widthClass] ?? derive(draft.wide, catalog, widthClass)
}

export function withGrid(draft: Draft, widthClass: WidthClass, grid: Grid): Draft {
  return { ...draft, [widthClass]: grid }
}

/** Put the arrangement of one width class back to what the page draws unasked. */
export function resetGrid(
  draft: Draft,
  widthClass: WidthClass,
  defaults: Grid,
  catalog: Catalog,
): Draft {
  if (widthClass === 'wide') return { ...draft, wide: normalize(defaults, catalog, 'wide') }
  return { ...draft, [widthClass]: null }
}

/**
 * What to store for a draft, or `null` for nothing at all.
 *
 * Every class that is what the page would draw anyway is stored as `null`, one
 * by one: a narrower arrangement identical to the derived one keeps following
 * the wide one, and a wide arrangement identical to the default is not stored
 * even when a narrower class *is*. A stored copy of today's default would hide
 * every later improvement to it behind a layout the owner never made -- which
 * is exactly what somebody who only rearranged their phone would get.
 */
export function toDocument(
  draft: Draft,
  defaults: Grid,
  catalog: Catalog,
): StoredArrangement | null {
  const own = (widthClass: 'medium' | 'narrow'): Grid | null => {
    const grid = draft[widthClass]
    return grid === null || sameGrid(grid, derive(draft.wide, catalog, widthClass)) ? null : grid
  }
  const wide = sameGrid(draft.wide, normalize(defaults, catalog, 'wide')) ? null : draft.wide
  const document: StoredArrangement = { wide, medium: own('medium'), narrow: own('narrow') }
  const untouched =
    document.wide === null && document.medium === null && document.narrow === null
  return untouched ? null : document
}

/** Report whether two drafts would draw the same page at every width. */
export function sameDraft(a: Draft, b: Draft, catalog: Catalog): boolean {
  return WIDTH_CLASSES.every((widthClass) =>
    sameGrid(gridOf(a, widthClass, catalog), gridOf(b, widthClass, catalog)),
  )
}

/**
 * The tiles a reader sees, once the widgets this track has nothing for are gone.
 *
 * A heart-rate chart on a track without a heart rate would be a page claiming
 * data it does not have, so the tile goes -- and what was below it rises, rather
 * than leaving a hole where a chart would have been.
 */
export function visibleTiles(grid: Grid, available: (widget: string) => boolean): Tile[] {
  return compact(grid.tiles.filter((tile) => available(tile.widget)))
}
