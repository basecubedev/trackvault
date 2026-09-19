import { act, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Catalog, WidgetSizes } from './document'
import type { Grid } from './grid'
import { WidgetBoard } from './WidgetBoard'

/**
 * Dragging, in the one environment that has no layout.
 *
 * `jsdom` measures every box as zero, so the arithmetic that turns a pointer
 * into a cell is replaced here by the same arithmetic with numbers -- it is
 * tested for real in `geometry.test.ts`. What this file is about is the state
 * machine around it: one gesture at a time, and everything that can interrupt
 * one leaves the arrangement as it was rather than half moved.
 */
vi.mock('./geometry', () => ({
  measure: () => ({
    left: 0,
    top: 0,
    columnPitch: 100,
    columnGap: 0,
    rowStarts: [],
    rowPitch: 64,
    rowGap: 0,
  }),
  columnAt: (_grid: unknown, offset: number) => Math.max(0, Math.round(offset / 100)),
  rowAt: (_grid: unknown, offset: number) => Math.max(0, Math.round(offset / 64)),
  columnsTo: (_grid: unknown, column: number, offset: number) =>
    Math.max(1, Math.round(offset / 100) - column),
  rowsTo: (_grid: unknown, row: number, offset: number) =>
    Math.max(1, Math.round(offset / 64) - row),
}))

function sizes(width: number, height: number): Record<'wide' | 'medium' | 'narrow', WidgetSizes> {
  const entry = (w: number): WidgetSizes => ({
    size: { width: w, height },
    min: { width: 1, height: 2 },
    max: { width: 12, height: 12 },
  })
  return { wide: entry(width), medium: entry(Math.min(width, 8)), narrow: entry(2) }
}

const CATALOG: Catalog = [
  { id: 'first', label: 'First', description: 'The first widget.', sizes: sizes(4, 4) },
  { id: 'second', label: 'Second', description: 'The second widget.', sizes: sizes(4, 4) },
]

const DEFAULTS: Grid = {
  columns: 12,
  tiles: [
    { widget: 'first', x: 0, y: 0, width: 4, height: 4 },
    { widget: 'second', x: 4, y: 0, width: 4, height: 4 },
  ],
  hidden: [],
}

let notify: ResizeObserverCallback = () => undefined

class Observer implements ResizeObserver {
  constructor(callback: ResizeObserverCallback) {
    notify = callback
  }
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

function board() {
  vi.stubGlobal('ResizeObserver', Observer)
  const onSave = vi.fn(() => Promise.resolve())
  render(
    <WidgetBoard
      label="Report"
      catalog={CATALOG}
      defaults={DEFAULTS}
      stored={null}
      widgets={{ first: <p>first</p>, second: <p>second</p> }}
      available={() => true}
      editable
      onSave={onSave}
    />,
  )
  return { onSave }
}

function placementOf(widget: string): string {
  return screen.getByTestId(`tile-${widget}`).style.gridColumn
}

function widen(width: number) {
  act(() => {
    notify([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver)
  })
}

async function arrange() {
  const user = userEvent.setup()
  board()
  widen(1200)
  await user.click(screen.getByTestId('customize-layout'))
  return user
}

/** Take hold of a widget's bar with one pointer. */
function grab(widget: string, pointerId = 1) {
  fireEvent.pointerDown(screen.getByTestId(`move-${widget}`), {
    pointerId,
    button: 0,
    clientX: 0,
    clientY: 0,
  })
}

function moveTo(widget: string, clientX: number, clientY: number, pointerId = 1) {
  fireEvent.pointerMove(screen.getByTestId(`move-${widget}`), { pointerId, clientX, clientY })
}

function release(widget: string, pointerId = 1) {
  fireEvent.pointerUp(screen.getByTestId(`move-${widget}`), { pointerId })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('resizing a widget', () => {
  /**
   * A tile that is held down by a *neighbour* rises the moment it stops
   * sharing that neighbour's columns -- which is exactly what dragging its
   * corner to the left does.
   */
  const HELD_DOWN: Grid = {
    columns: 12,
    tiles: [
      { widget: 'first', x: 0, y: 0, width: 4, height: 2 },
      { widget: 'second', x: 4, y: 0, width: 4, height: 6 },
      { widget: 'third', x: 0, y: 6, width: 8, height: 2 },
    ],
    hidden: [],
  }

  it('measures its height from where the tile actually is, not where it was', async () => {
    const user = userEvent.setup()
    vi.stubGlobal('ResizeObserver', Observer)
    render(
      <WidgetBoard
        label="Report"
        catalog={[...CATALOG, { id: 'third', label: 'Third', description: 'A third.', sizes: sizes(8, 2) }]}
        defaults={HELD_DOWN}
        stored={null}
        widgets={{ first: <p>first</p>, second: <p>second</p>, third: <p>third</p> }}
        available={() => true}
        editable
        onSave={vi.fn(() => Promise.resolve())}
      />,
    )
    widen(1200)
    await user.click(screen.getByTestId('customize-layout'))

    // Narrow enough to leave the second widget's columns -- the third rises
    // from row 6 to row 2 -- while the corner is dragged to the bottom of
    // row 8. That is six rows tall from where it now sits, not two from where
    // it used to.
    const corner = screen.getByTestId('tile-third').querySelector('.tile__resize')
    expect(corner).not.toBeNull()
    if (!corner) return
    fireEvent.pointerDown(corner, { pointerId: 9, button: 0, clientX: 800, clientY: 512 })
    fireEvent.pointerMove(corner, { pointerId: 9, clientX: 400, clientY: 512 })
    fireEvent.pointerUp(corner, { pointerId: 9 })

    const tile = screen.getByTestId('tile-third')
    expect(tile.style.gridColumn).toBe('1 / span 4')
    expect(tile.style.gridRow).toBe('3 / span 6')
  })
})

describe('dragging a widget', () => {
  it('leaves it where it was dropped', async () => {
    await arrange()

    grab('first')
    moveTo('first', 400, 0)
    expect(screen.getByText('First', { selector: '.board__ghost' })).toBeInTheDocument()
    release('first')

    expect(placementOf('first')).toBe('5 / span 4')
    expect(screen.getByTestId('layout-announcement')).toHaveTextContent(
      'First moved to column 5, row 1.',
    )
  })

  it('belongs to the pointer that started it', async () => {
    await arrange()
    grab('first')
    moveTo('first', 400, 0)

    // A second finger on another widget must not take the gesture over.
    grab('second', 2)
    expect(screen.getByText('First', { selector: '.board__ghost' })).toBeInTheDocument()

    release('first')
    expect(placementOf('first')).toBe('5 / span 4')
  })

  it('is abandoned when the page changes width underneath it', async () => {
    await arrange()
    grab('first')
    moveTo('first', 400, 0)

    // A scrollbar appears, the board is suddenly narrower: the preview was
    // measured in twelve columns and must not be committed into eight.
    widen(700)

    expect(screen.queryByText('First', { selector: '.board__ghost' })).toBeNull()
    release('first')
    expect(screen.getByTestId('layout-announcement')).toHaveTextContent('changed width')
  })

  it('is abandoned by Escape, wherever the focus is', async () => {
    const user = await arrange()
    await user.click(screen.getByTestId('add-widget'))
    grab('first')
    moveTo('first', 400, 0)

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.queryByText('First', { selector: '.board__ghost' })).toBeNull()
    release('first')
    expect(placementOf('first')).toBe('1 / span 4')
  })

  it('is the only thing that changes the arrangement while it lasts', async () => {
    const user = await arrange()
    screen.getByTestId('move-second').focus()
    grab('first')
    moveTo('first', 400, 0)

    // Both would be undone by the drop, so both are ignored while it is held.
    await user.keyboard('{ArrowRight}')
    await user.keyboard('{Control>}z{/Control}')

    release('first')
    expect(placementOf('first')).toBe('5 / span 4')
    // One step was taken, so one undo is all it takes to get back.
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(placementOf('first')).toBe('1 / span 4')
    expect(placementOf('second')).toBe('5 / span 4')
  })
})
