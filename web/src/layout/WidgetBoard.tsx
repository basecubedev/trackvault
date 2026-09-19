import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react'
import {
  COLUMNS,
  defaultSize,
  draftOf,
  gridOf,
  limitsOf,
  resetGrid,
  sameDraft,
  toDocument,
  visibleTiles,
  widthClassOf,
  withGrid,
  type Catalog,
  type Draft,
  type StoredArrangement,
  type WidgetSpec,
  type WidthClass,
} from './document'
import { columnAt, columnsTo, measure, rowAt, rowsTo } from './geometry'
import {
  grow,
  hide,
  move,
  nudge,
  readingOrder,
  resize,
  sameGrid,
  show,
  type Grid,
  type Size,
  type Tile,
} from './grid'

/**
 * A page made of widgets its owner can move, resize, hide and bring back.
 *
 * Reading is the default and nothing moves by accident: arranging starts with
 * one button and ends with "Done" or "Cancel". While arranging, the widgets
 * stay visible but inert -- dragging a map's tile must not pan the map -- and
 * every change can be undone until it is saved.
 *
 * Three ways to do everything, because a layout editor that only answers a
 * mouse is one a keyboard or a phone cannot use:
 *
 * ```
 * pointer    drag a widget by its bar, resize it by its corner
 * keyboard   arrow keys move the focused widget, Shift + arrows resize it
 * menu       Small, Medium, Large, Full width and Hide, one click each
 * ```
 *
 * The content of each widget is handed in already rendered, and the board
 * never recreates it. Dragging re-renders the frames around the widgets and
 * nothing inside them -- a map or a chart redrawn sixty times a second to move
 * a dashed outline is the cascade the hover store exists to avoid.
 *
 * Nothing here knows what a widget is. The page passes the catalog, the
 * default arrangement, the stored one, and which widgets this particular
 * report has anything for.
 */

export interface WidgetBoardProps {
  /** What the region is called for a screen reader. */
  readonly label: string
  readonly catalog: Catalog
  /** The wide arrangement the page draws when nobody arranged anything. */
  readonly defaults: Grid
  /** The owner's stored arrangement, or `null` for the default. */
  readonly stored: StoredArrangement | null
  /** Each widget's content, by id. */
  readonly widgets: Readonly<Record<string, ReactNode>>
  /** Whether this report has anything to show in a widget. */
  readonly available: (widget: string) => boolean
  /** What an empty widget says while it is being arranged. */
  readonly unavailableHint?: (widget: string) => string
  /** Whether the owner may arrange the page here. */
  readonly editable: boolean
  /** Why arranging is not possible right now, if it is not. */
  readonly blocked?: string | null
  /** Something the owner should know about the stored arrangement. */
  readonly notice?: ReactNode
  /** Store an arrangement, or `null` to forget it. Rejects with a readable message. */
  readonly onSave: (document: StoredArrangement | null) => Promise<void>
}

interface Session {
  readonly start: Draft
  readonly draft: Draft
  readonly past: readonly Draft[]
  readonly future: readonly Draft[]
  /** The document order while arranging, fixed so focus never jumps as tiles move. */
  readonly order: readonly string[]
}

interface Box {
  readonly left: number
  readonly top: number
  readonly width: number
  readonly height: number
}

interface Gesture {
  readonly kind: 'move' | 'resize'
  readonly widget: string
  readonly pointer: number
  /**
   * The width class the gesture started in.
   *
   * A scrollbar appearing mid-drag can change it, and a preview measured in
   * twelve columns must never be committed into an eight-column arrangement.
   */
  readonly widthClass: WidthClass
  readonly base: Grid
  readonly preview: Grid
  /** Where in the tile the pointer took hold of it. */
  readonly grip: { readonly x: number; readonly y: number }
  /** The tile's box when the gesture started, relative to the grid. */
  readonly origin: Box
  /** The outline that follows the pointer. */
  readonly ghost: Box
}

const SCOPE: Readonly<Record<WidthClass, string>> = {
  wide: 'You are arranging the page for wide screens. Narrower screens follow this order until you arrange them too.',
  medium:
    'You are arranging the page for medium-width screens. Wide screens keep their own arrangement.',
  narrow: 'You are arranging the page for narrow screens. Wider screens keep their own arrangement.',
}

const HISTORY_LIMIT = 100
const SCROLL_EDGE = 48
const SCROLL_STEP = 14

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum)
}

function placement(tile: Tile): React.CSSProperties {
  return {
    gridColumn: `${String(tile.x + 1)} / span ${String(tile.width)}`,
    gridRow: `${String(tile.y + 1)} / span ${String(tile.height)}`,
  }
}

interface Preset {
  readonly label: string
  readonly size: Size
}

/** The one-click sizes a widget offers on this grid, without duplicates. */
function presetsOf(spec: WidgetSpec, widthClass: WidthClass, tile: Tile): Preset[] {
  const { min, max } = limitsOf(spec, widthClass)
  const normal = defaultSize(spec, widthClass)
  const columns = COLUMNS[widthClass]
  const options: Preset[] = [
    { label: 'Small', size: min },
    { label: 'Medium', size: normal },
    {
      label: 'Large',
      size: {
        width: clamp(Math.round(normal.width * 1.5), min.width, max.width),
        height: clamp(Math.round(normal.height * 1.5), min.height, max.height),
      },
    },
  ]
  if (max.width >= columns) {
    options.push({ label: 'Full width', size: { width: columns, height: tile.height } })
  }
  return options.filter(
    (option, index) =>
      options.findIndex(
        (other) =>
          other.size.width === option.size.width && other.size.height === option.size.height,
      ) === index,
  )
}

/**
 * The tiles in the order they had when arranging began, then any added since.
 *
 * Kept fixed while arranging: re-sorting the document on every move would move
 * the focused bar out from under the keyboard, and every drag would re-insert a
 * map's canvas somewhere else in the page.
 */
function sessionOrder(grid: Grid, order: readonly string[]): Tile[] {
  const placed = new Map(grid.tiles.map((tile) => [tile.widget, tile]))
  const ordered = order.flatMap((widget) => {
    const tile = placed.get(widget)
    return tile ? [tile] : []
  })
  const rest = readingOrder(grid.tiles).filter((tile) => !order.includes(tile.widget))
  return [...ordered, ...rest]
}

function describeSize(tile: Tile): string {
  const columns = tile.width === 1 ? '1 column' : `${String(tile.width)} columns`
  const rows = tile.height === 1 ? '1 row' : `${String(tile.height)} rows`
  return `${columns} wide, ${rows} tall`
}

/**
 * Ask before a click on a link or a reload throws away an unsaved arrangement.
 *
 * The application's router has no navigation blocker, so this watches link
 * clicks itself -- in the capture phase, before the router's own handler
 * decides to navigate.
 */
function useLeaveGuard(active: boolean): void {
  useEffect(() => {
    if (!active) return
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    const click = (event: MouseEvent) => {
      if (event.defaultPrevented || event.button !== 0) return
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
      const link = event.target instanceof Element ? event.target.closest('a[href]') : null
      if (!link || link.getAttribute('target') === '_blank') return
      if (!window.confirm('Leave without saving your changes to the layout?')) {
        event.preventDefault()
        event.stopPropagation()
      }
    }
    window.addEventListener('beforeunload', beforeUnload)
    document.addEventListener('click', click, true)
    return () => {
      window.removeEventListener('beforeunload', beforeUnload)
      document.removeEventListener('click', click, true)
    }
  }, [active])
}

export function WidgetBoard({
  label,
  catalog,
  defaults,
  stored,
  widgets,
  available,
  unavailableHint,
  editable,
  blocked = null,
  notice = null,
  onSave,
}: WidgetBoardProps) {
  const root = useRef<HTMLDivElement | null>(null)
  const gridElement = useRef<HTMLDivElement | null>(null)
  const handles = useRef(new Map<string, HTMLButtonElement>())
  const customize = useRef<HTMLButtonElement | null>(null)
  const addButton = useRef<HTMLButtonElement | null>(null)
  const instructions = useId()
  const trayId = useId()

  const [widthClass, setWidthClass] = useState<WidthClass>('wide')
  const [session, setSession] = useState<Session | null>(null)
  const sessionRef = useRef<Session | null>(null)
  sessionRef.current = session
  const [gesture, setGestureState] = useState<Gesture | null>(null)
  const gestureRef = useRef<Gesture | null>(null)
  const [trayOpen, setTrayOpen] = useState(false)
  const [menu, setMenu] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState('')
  const [focusTarget, setFocusTarget] = useState<string | null>(null)
  const pointer = useRef({ x: 0, y: 0 })
  const scroll = useRef<{ direction: number; frame: number | null }>({ direction: 0, frame: null })

  const specs = useMemo(() => new Map(catalog.map((spec) => [spec.id, spec])), [catalog])
  const saved = useMemo(() => draftOf(stored, defaults, catalog), [stored, defaults, catalog])
  const editing = session !== null
  const columns = COLUMNS[widthClass]
  const current = session
    ? gridOf(session.draft, widthClass, catalog)
    : gridOf(saved, widthClass, catalog)
  const shown = gesture?.preview ?? current

  // The width of the area the report is drawn in, measured before the first
  // paint so a phone never sees the wide arrangement flash past.
  useLayoutEffect(() => {
    const element = root.current
    if (!element) return
    const width = element.getBoundingClientRect().width
    if (width > 0) setWidthClass(widthClassOf(width))
    const observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.width ?? 0
      if (measured > 0) setWidthClass(widthClassOf(measured))
    })
    observer.observe(element)
    return () => {
      observer.disconnect()
    }
  }, [])

  // Recomputed when the draft changes rather than on every pointer move: a
  // ghost following the pointer does not change whether there is something to
  // lose, and comparing three arrangements per frame is how it would.
  const draft = session?.draft ?? null
  const start = session?.start ?? null
  const dirty = useMemo(
    () => draft !== null && start !== null && !sameDraft(draft, start, catalog),
    [draft, start, catalog],
  )
  useLeaveGuard(dirty)

  useEffect(() => {
    if (focusTarget === null) return
    const element =
      focusTarget === '@customize'
        ? customize.current
        : focusTarget === '@add'
          ? addButton.current
          : (handles.current.get(focusTarget) ?? null)
    element?.focus()
    setFocusTarget(null)
  }, [focusTarget, session])

  const setGesture = useCallback((next: Gesture | null) => {
    gestureRef.current = next
    setGestureState(next)
  }, [])

  const stopScrolling = useCallback(() => {
    scroll.current.direction = 0
    if (scroll.current.frame !== null) cancelAnimationFrame(scroll.current.frame)
    scroll.current.frame = null
  }, [])

  useEffect(() => stopScrolling, [stopScrolling])

  const commit = useCallback(
    (grid: Grid, message: string) => {
      setSession((previous) =>
        previous === null
          ? previous
          : {
              ...previous,
              past: [...previous.past, previous.draft].slice(-HISTORY_LIMIT),
              future: [],
              draft: withGrid(previous.draft, widthClass, grid),
            },
      )
      setAnnouncement(message)
    },
    [widthClass],
  )

  const undo = useCallback(() => {
    const previous = sessionRef.current
    const last = previous?.past.at(-1)
    if (!previous || !last) return
    setSession({
      ...previous,
      draft: last,
      past: previous.past.slice(0, -1),
      future: [previous.draft, ...previous.future],
    })
    setAnnouncement('Undone.')
  }, [])

  const redo = useCallback(() => {
    const previous = sessionRef.current
    const next = previous?.future[0]
    if (!previous || !next) return
    setSession({
      ...previous,
      draft: next,
      past: [...previous.past, previous.draft],
      future: previous.future.slice(1),
    })
    setAnnouncement('Redone.')
  }, [])

  useEffect(() => {
    if (!editing) return
    const onKey = (event: KeyboardEvent) => {
      // A drag is one gesture. Undoing underneath it would be undone again by
      // the drop, because the drop commits what was picked up.
      if (gestureRef.current) return
      if (!(event.ctrlKey || event.metaKey)) return
      if (event.target instanceof HTMLElement && event.target.closest('input, textarea, select')) {
        return
      }
      const key = event.key.toLowerCase()
      if (key === 'z' && !event.shiftKey) {
        event.preventDefault()
        undo()
      } else if ((key === 'z' && event.shiftKey) || key === 'y') {
        event.preventDefault()
        redo()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
    }
  }, [editing, undo, redo])

  // A gesture belongs to the arrangement it started in. If the width class
  // changes under it -- a scrollbar appearing, a tablet turned -- the preview
  // is measured in the wrong number of columns, so the gesture is dropped
  // rather than committed into a grid it was never drawn for.
  useEffect(() => {
    const active = gestureRef.current
    if (active && active.widthClass !== widthClass) {
      stopScrolling()
      setGesture(null)
      setAnnouncement('The page changed width, so that move was left where it was.')
    }
  }, [widthClass, stopScrolling, setGesture])

  // Escape abandons a drag wherever the focus happens to be.
  useEffect(() => {
    if (gesture === null) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      stopScrolling()
      setGesture(null)
      setAnnouncement('Left where it was.')
    }
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
    }
  }, [gesture, stopScrolling, setGesture])

  useEffect(() => {
    if (menu === null) return
    const close = (event: PointerEvent) => {
      if (event.target instanceof Element && event.target.closest('[data-tile-menu]')) return
      setMenu(null)
    }
    document.addEventListener('pointerdown', close)
    return () => {
      document.removeEventListener('pointerdown', close)
    }
  }, [menu])

  function begin() {
    setSession({
      start: saved,
      draft: saved,
      past: [],
      future: [],
      order: readingOrder(gridOf(saved, widthClass, catalog).tiles).map((tile) => tile.widget),
    })
    setError(null)
    setAnnouncement('Arranging the page. Press Done to keep your changes.')
    const first = readingOrder(gridOf(saved, widthClass, catalog).tiles)[0]
    if (first) setFocusTarget(first.widget)
  }

  function end(message: string) {
    stopScrolling()
    setGesture(null)
    setSession(null)
    setTrayOpen(false)
    setMenu(null)
    setError(null)
    setAnnouncement(message)
    setFocusTarget('@customize')
  }

  async function done() {
    if (!session) return
    if (sameDraft(session.draft, session.start, catalog)) {
      end('Nothing was changed.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSave(toDocument(session.draft, defaults, catalog))
      end('Layout saved.')
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : 'The layout could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  function labelOf(widget: string): string {
    return specs.get(widget)?.label ?? widget
  }

  function tileOf(grid: Grid, widget: string): Tile | undefined {
    return grid.tiles.find((tile) => tile.widget === widget)
  }

  // --- pointer ----------------------------------------------------------------

  function follow(clientX: number, clientY: number) {
    const active = gestureRef.current
    const element = gridElement.current
    if (!active || !element) return
    const grid = measure(element, columns)
    const tile = tileOf(active.base, active.widget)
    const spec = specs.get(active.widget)
    if (!tile || !spec) return
    if (active.kind === 'move') {
      const left = clientX - grid.left - active.grip.x
      const top = clientY - grid.top - active.grip.y
      const preview = move(active.base, active.widget, columnAt(grid, left), rowAt(grid, top))
      setGesture({ ...active, preview, ghost: { ...active.ghost, left, top } })
    } else {
      const right = clientX - grid.left
      const bottom = clientY - grid.top
      // The width first, then the height from wherever that left the tile.
      // Narrowing a tile out of the columns of the neighbour that was holding
      // it down lets it rise, and a height counted from its old top edge would
      // be rows short of the corner being dragged.
      const limits = limitsOf(spec, widthClass)
      const narrowed = resize(
        active.base,
        active.widget,
        columnsTo(grid, tile.x, right),
        tile.height,
        limits,
      )
      const anchor = tileOf(narrowed, active.widget) ?? tile
      const top = grid.rowStarts[anchor.y] ?? anchor.y * grid.rowPitch
      const left = anchor.x * grid.columnPitch
      const preview = resize(
        narrowed,
        active.widget,
        anchor.width,
        rowsTo(grid, anchor.y, bottom),
        limits,
      )
      setGesture({
        ...active,
        preview,
        ghost: {
          left,
          top,
          width: Math.max(24, right - left),
          height: Math.max(24, bottom - top),
        },
      })
    }
  }

  function autoscroll(clientY: number) {
    const direction =
      clientY < SCROLL_EDGE ? -1 : clientY > window.innerHeight - SCROLL_EDGE ? 1 : 0
    scroll.current.direction = direction
    if (direction === 0 || scroll.current.frame !== null) return
    const step = () => {
      if (scroll.current.direction === 0 || !gestureRef.current) {
        scroll.current.frame = null
        return
      }
      window.scrollBy(0, scroll.current.direction * SCROLL_STEP)
      follow(pointer.current.x, pointer.current.y)
      scroll.current.frame = requestAnimationFrame(step)
    }
    scroll.current.frame = requestAnimationFrame(step)
  }

  function startGesture(kind: Gesture['kind'], widget: string, event: ReactPointerEvent<HTMLElement>) {
    // One gesture at a time. A second finger landing on another tile would
    // otherwise take over the state the first one is still holding.
    if (event.button !== 0 || !session || gestureRef.current) return
    const element = gridElement.current
    const tileElement = event.currentTarget.closest('.tile')
    if (!element || !tileElement) return
    event.preventDefault()
    // Preventing the default also prevents the focus the click would have
    // given, and the keyboard needs the bar focused to cancel or carry on.
    event.currentTarget.focus()
    event.currentTarget.setPointerCapture(event.pointerId)
    setMenu(null)
    const grid = element.getBoundingClientRect()
    const box = tileElement.getBoundingClientRect()
    const origin: Box = {
      left: box.left - grid.left,
      top: box.top - grid.top,
      width: box.width,
      height: box.height,
    }
    pointer.current = { x: event.clientX, y: event.clientY }
    setGesture({
      kind,
      widget,
      pointer: event.pointerId,
      widthClass,
      base: current,
      preview: current,
      grip: { x: event.clientX - box.left, y: event.clientY - box.top },
      origin,
      ghost: origin,
    })
  }

  function onPointerMove(event: ReactPointerEvent<HTMLElement>) {
    const active = gestureRef.current
    if (!active || active.pointer !== event.pointerId) return
    pointer.current = { x: event.clientX, y: event.clientY }
    follow(event.clientX, event.clientY)
    autoscroll(event.clientY)
  }

  function finishGesture(event: ReactPointerEvent<HTMLElement>) {
    const active = gestureRef.current
    if (!active || active.pointer !== event.pointerId) return
    stopScrolling()
    setGesture(null)
    const tile = tileOf(active.preview, active.widget)
    if (!tile || sameGrid(active.preview, active.base)) return
    commit(
      active.preview,
      active.kind === 'move'
        ? `${labelOf(active.widget)} moved to column ${String(tile.x + 1)}, row ${String(tile.y + 1)}.`
        : `${labelOf(active.widget)} is now ${describeSize(tile)}.`,
    )
  }

  function cancelGesture() {
    stopScrolling()
    setGesture(null)
  }

  // --- keyboard ---------------------------------------------------------------

  function onHandleKey(widget: string, event: ReactKeyboardEvent<HTMLButtonElement>) {
    // The pointer is mid-gesture and will commit what it picked up; a move
    // squeezed in underneath it would be silently undone by the drop.
    if (gestureRef.current) return
    const directions: Record<string, [number, number]> = {
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0],
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
    }
    const direction = directions[event.key]
    const spec = specs.get(widget)
    if (!direction || !spec) return
    event.preventDefault()
    const [dx, dy] = direction
    const name = labelOf(widget)
    if (event.shiftKey) {
      const next = grow(current, widget, dx, dy, limitsOf(spec, widthClass))
      const tile = tileOf(next, widget)
      if (!tile || sameGrid(next, current)) {
        setAnnouncement(`${name} cannot be made any ${dx + dy > 0 ? 'larger' : 'smaller'} that way.`)
        return
      }
      commit(next, `${name} is now ${describeSize(tile)}.`)
      return
    }
    const next = nudge(current, widget, dx, dy)
    const tile = tileOf(next, widget)
    if (!tile || sameGrid(next, current)) {
      setAnnouncement(`${name} cannot move any further that way.`)
      return
    }
    commit(next, `${name} moved to column ${String(tile.x + 1)}, row ${String(tile.y + 1)}.`)
  }

  // --- menu and tray ------------------------------------------------------------

  function applyPreset(widget: string, size: Size) {
    const spec = specs.get(widget)
    const tile = tileOf(current, widget)
    if (!spec || !tile) return
    let next = current
    if (tile.x + size.width > columns) next = move(next, widget, columns - size.width, tile.y)
    next = resize(next, widget, size.width, size.height, limitsOf(spec, widthClass))
    setMenu(null)
    const placed = tileOf(next, widget)
    if (!placed || sameGrid(next, current)) {
      setAnnouncement(`${labelOf(widget)} already has that size.`)
      // The button that was clicked has just been unmounted with the menu, so
      // the focus goes back to the widget rather than to the document.
      setFocusTarget(widget)
      return
    }
    commit(next, `${labelOf(widget)} is now ${describeSize(placed)}.`)
    setFocusTarget(widget)
  }

  function hideWidget(widget: string) {
    setMenu(null)
    commit(hide(current, widget), `${labelOf(widget)} hidden. Add it back with “Add widget”.`)
    setFocusTarget('@add')
  }

  function showWidget(widget: string) {
    const spec = specs.get(widget)
    if (!spec) return
    commit(show(current, widget, defaultSize(spec, widthClass)), `${spec.label} added.`)
    setSession((previous) =>
      previous === null || previous.order.includes(widget)
        ? previous
        : { ...previous, order: [...previous.order, widget] },
    )
    setFocusTarget(widget)
  }

  function resetView() {
    setSession((previous) =>
      previous === null
        ? previous
        : {
            ...previous,
            past: [...previous.past, previous.draft].slice(-HISTORY_LIMIT),
            future: [],
            draft: resetGrid(previous.draft, widthClass, defaults, catalog),
          },
    )
    setAnnouncement('The default arrangement is back. Press Done to keep it.')
  }

  // --- drawing ------------------------------------------------------------------

  const tiles: Tile[] =
    session === null ? visibleTiles(current, available) : sessionOrder(shown, session.order)

  const hidden = shown.hidden.flatMap((widget) => {
    const spec = specs.get(widget)
    return spec ? [spec] : []
  })

  return (
    <div
      ref={root}
      className={`board${editing ? ' board--editing' : ''}`}
      role="region"
      aria-label={label}
      data-width-class={widthClass}
      data-testid="widget-board"
    >
      {editable && !editing && (
        <div className="board__toolbar">
          {blocked !== null && <p className="board__hint">{blocked}</p>}
          <button
            ref={customize}
            type="button"
            onClick={begin}
            disabled={blocked !== null}
            data-testid="customize-layout"
          >
            Customize layout
          </button>
        </div>
      )}
      {editable && !editing && notice !== null && <div className="board__notice">{notice}</div>}

      {session !== null && (
        <div className="board__toolbar board__toolbar--editing" data-testid="layout-toolbar">
          <div className="board__hint">
            <p>{SCOPE[widthClass]}</p>
            <p id={instructions}>
              Drag a widget by its bar, or its size by the corner. With the keyboard, focus a
              widget’s bar and use the arrow keys to move it; hold Shift to change its size.
            </p>
          </div>
          <div className="board__actions">
            <button
              ref={addButton}
              type="button"
              aria-expanded={trayOpen}
              aria-controls={trayId}
              onClick={() => {
                setTrayOpen((open) => !open)
              }}
              data-testid="add-widget"
            >
              Add widget{hidden.length > 0 ? ` (${String(hidden.length)})` : ''}
            </button>
            <button type="button" onClick={undo} disabled={session.past.length === 0}>
              Undo
            </button>
            <button type="button" onClick={redo} disabled={session.future.length === 0}>
              Redo
            </button>
            <button type="button" onClick={resetView} data-testid="reset-layout">
              Reset to default
            </button>
            <button
              type="button"
              onClick={() => {
                end('Changes discarded.')
              }}
              disabled={saving}
            >
              Cancel
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => {
                void done()
              }}
              disabled={saving}
              data-testid="save-layout"
            >
              {saving ? 'Saving…' : 'Done'}
            </button>
          </div>
          {error !== null && (
            <p className="board__error" role="alert">
              {error}
            </p>
          )}
          {trayOpen && (
            <div id={trayId} className="board__tray" data-testid="widget-tray">
              {hidden.length === 0 ? (
                <p className="muted">Every widget is on the page.</p>
              ) : (
                <ul>
                  {hidden.map((spec) => (
                    <li key={spec.id}>
                      <span>
                        <span className="board__tray-name">{spec.label}</span>
                        <span className="muted"> — {spec.description}</span>
                      </span>
                      <button
                        type="button"
                        onClick={() => {
                          showWidget(spec.id)
                        }}
                        aria-label={`Add ${spec.label}`}
                      >
                        Add
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}

      <div
        ref={gridElement}
        className={`board__grid${editing ? ' board__grid--editing' : ''}`}
        data-width-class={widthClass}
        style={{ '--columns': String(columns) } as React.CSSProperties}
      >
        {tiles.map((tile) => {
          const spec = specs.get(tile.widget)
          if (!spec) return null
          const has = available(tile.widget)
          const moving = gesture?.widget === tile.widget
          return (
            <div
              key={tile.widget}
              className={`tile${editing ? ' tile--editing' : ''}${moving ? ' tile--moving' : ''}${
                menu === tile.widget ? ' tile--menu-open' : ''
              }`}
              style={placement(tile)}
              data-widget={tile.widget}
              data-testid={`tile-${tile.widget}`}
            >
              {editing && (
                <div className="tile__bar" data-tile-menu>
                  <button
                    ref={(element) => {
                      if (element) handles.current.set(tile.widget, element)
                      else handles.current.delete(tile.widget)
                    }}
                    type="button"
                    className="tile__handle"
                    aria-label={`Move ${spec.label}`}
                    aria-describedby={instructions}
                    onPointerDown={(event) => {
                      startGesture('move', tile.widget, event)
                    }}
                    onPointerMove={onPointerMove}
                    onPointerUp={finishGesture}
                    onPointerCancel={cancelGesture}
                    onKeyDown={(event) => {
                      onHandleKey(tile.widget, event)
                    }}
                    data-testid={`move-${tile.widget}`}
                  >
                    <span aria-hidden="true" className="tile__grip">
                      ⠿
                    </span>
                    {spec.label}
                  </button>
                  <button
                    type="button"
                    className="tile__menu-button"
                    aria-expanded={menu === tile.widget}
                    aria-label={`Size and visibility of ${spec.label}`}
                    onClick={() => {
                      setMenu((open) => (open === tile.widget ? null : tile.widget))
                    }}
                    data-testid={`menu-${tile.widget}`}
                  >
                    ⋯
                  </button>
                  {menu === tile.widget && (
                    <div
                      className="tile__menu"
                      role="group"
                      aria-label={`Size and visibility of ${spec.label}`}
                      onKeyDown={(event) => {
                        if (event.key === 'Escape') {
                          setMenu(null)
                          setFocusTarget(tile.widget)
                        }
                      }}
                    >
                      {presetsOf(spec, widthClass, tile).map((preset) => (
                        <button
                          key={preset.label}
                          type="button"
                          aria-pressed={
                            preset.size.width === tile.width && preset.size.height === tile.height
                          }
                          onClick={() => {
                            applyPreset(tile.widget, preset.size)
                          }}
                        >
                          {preset.label}
                        </button>
                      ))}
                      <button
                        type="button"
                        onClick={() => {
                          hideWidget(tile.widget)
                        }}
                        data-testid={`hide-${tile.widget}`}
                      >
                        Hide
                      </button>
                    </div>
                  )}
                </div>
              )}
              <div className="tile__content" inert={editing}>
                {has ? (
                  widgets[tile.widget]
                ) : (
                  <div className="panel tile__placeholder">
                    <p className="muted">
                      {unavailableHint?.(tile.widget) ?? 'Nothing to show here for this track.'}
                    </p>
                  </div>
                )}
              </div>
              {editing && (
                <div
                  className="tile__resize"
                  aria-hidden="true"
                  onPointerDown={(event) => {
                    startGesture('resize', tile.widget, event)
                  }}
                  onPointerMove={onPointerMove}
                  onPointerUp={finishGesture}
                  onPointerCancel={cancelGesture}
                />
              )}
            </div>
          )
        })}
        {gesture && (
          <div
            className="board__ghost"
            aria-hidden="true"
            style={{
              left: gesture.ghost.left,
              top: gesture.ghost.top,
              width: gesture.ghost.width,
              height: gesture.ghost.height,
            }}
          >
            {labelOf(gesture.widget)}
          </div>
        )}
      </div>
      <p className="visually-hidden" aria-live="polite" data-testid="layout-announcement">
        {announcement}
      </p>
    </div>
  )
}
