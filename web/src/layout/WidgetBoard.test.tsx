import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Catalog, StoredArrangement, WidgetSizes } from './document'
import type { Grid } from './grid'
import { WidgetBoard } from './WidgetBoard'

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
  { id: 'third', label: 'Third', description: 'Only on some reports.', sizes: sizes(4, 4) },
]

const DEFAULTS: Grid = {
  columns: 12,
  tiles: [
    { widget: 'first', x: 0, y: 0, width: 4, height: 4 },
    { widget: 'second', x: 4, y: 0, width: 4, height: 4 },
    { widget: 'third', x: 8, y: 0, width: 4, height: 4 },
  ],
  hidden: [],
}

const CONTENT = {
  first: <p>first content</p>,
  second: <p>second content</p>,
  third: <p>third content</p>,
}

function board({
  stored = null,
  available = () => true,
  editable = true,
  onSave = vi.fn(() => Promise.resolve()),
  blocked = null,
}: {
  stored?: StoredArrangement | null
  available?: (widget: string) => boolean
  editable?: boolean
  onSave?: (document: StoredArrangement | null) => Promise<void>
  blocked?: string | null
} = {}) {
  render(
    <WidgetBoard
      label="Report"
      catalog={CATALOG}
      defaults={DEFAULTS}
      stored={stored}
      widgets={CONTENT}
      available={available}
      unavailableHint={() => 'Nothing here for this one.'}
      editable={editable}
      blocked={blocked}
      onSave={onSave}
    />,
  )
  return { onSave }
}

function drawnOrder(): string[] {
  return Array.from(document.querySelectorAll<HTMLElement>('.tile')).map(
    (tile) => tile.dataset['widget'] ?? '',
  )
}

function placementOf(widget: string): string {
  const tile = screen.getByTestId(`tile-${widget}`)
  return `${tile.style.gridColumn} | ${tile.style.gridRow}`
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('reading a board', () => {
  it('draws the widgets in reading order, where the arrangement puts them', () => {
    board({
      stored: {
        wide: {
          columns: 12,
          tiles: [
            { widget: 'second', x: 0, y: 0, width: 4, height: 4 },
            { widget: 'first', x: 4, y: 0, width: 8, height: 4 },
            { widget: 'third', x: 0, y: 4, width: 12, height: 4 },
          ],
          hidden: [],
        },
        medium: null,
        narrow: null,
      },
    })

    expect(drawnOrder()).toEqual(['second', 'first', 'third'])
    expect(placementOf('first')).toBe('5 / span 8 | 1 / span 4')
  })

  it('leaves out a widget this report has nothing for, and nothing is inert', () => {
    board({ available: (widget) => widget !== 'third' })

    expect(drawnOrder()).toEqual(['first', 'second'])
    expect(screen.getByText('first content').closest('[inert]')).toBeNull()
  })

  it('offers arranging only where the owner may arrange', () => {
    board({ editable: false })

    expect(screen.queryByTestId('customize-layout')).toBeNull()
  })

  it('says why arranging is not possible rather than offering a button that cannot work', () => {
    board({ blocked: 'The saved layout could not be loaded.' })

    expect(screen.getByTestId('customize-layout')).toBeDisabled()
    expect(screen.getByText('The saved layout could not be loaded.')).toBeInTheDocument()
  })
})

describe('arranging a board', () => {
  it('keeps the widgets visible but out of reach while they are arranged', async () => {
    const user = userEvent.setup()
    board({ available: (widget) => widget !== 'third' })

    await user.click(screen.getByTestId('customize-layout'))

    expect(screen.getByTestId('layout-toolbar')).toBeInTheDocument()
    expect(screen.getByText('first content').closest('[inert]')).not.toBeNull()
    expect(screen.getByText('Nothing here for this one.')).toBeInTheDocument()
    expect(document.activeElement).toBe(screen.getByTestId('move-first'))
  })

  it('moves the focused widget with the arrow keys and says where it went', async () => {
    const user = userEvent.setup()
    board()
    await user.click(screen.getByTestId('customize-layout'))

    screen.getByTestId('move-first').focus()
    await user.keyboard('{ArrowRight}')

    expect(placementOf('first')).toBe('5 / span 4 | 1 / span 4')
    expect(placementOf('second')).toBe('1 / span 4 | 1 / span 4')
    expect(screen.getByTestId('layout-announcement').textContent).toBe(
      'First moved to column 5, row 1.',
    )
    expect(document.activeElement).toBe(screen.getByTestId('move-first'))
  })

  it('resizes with Shift and the arrow keys, inside the widget’s limits', async () => {
    const user = userEvent.setup()
    board()
    await user.click(screen.getByTestId('customize-layout'))

    screen.getByTestId('move-first').focus()
    await user.keyboard('{Shift>}{ArrowDown}{/Shift}')
    expect(placementOf('first')).toBe('1 / span 4 | 1 / span 5')

    await user.keyboard('{Shift>}{ArrowUp}{ArrowUp}{ArrowUp}{ArrowUp}{/Shift}')
    expect(placementOf('first')).toBe('1 / span 4 | 1 / span 2')
    expect(screen.getByTestId('layout-announcement').textContent).toContain('cannot be made any')
  })

  it('hides a widget from its menu and brings it back from the list of widgets', async () => {
    const user = userEvent.setup()
    board()
    await user.click(screen.getByTestId('customize-layout'))

    await user.click(screen.getByTestId('menu-second'))
    await user.click(screen.getByTestId('hide-second'))

    expect(screen.queryByTestId('tile-second')).toBeNull()
    expect(screen.getByTestId('add-widget')).toHaveTextContent('Add widget (1)')
    expect(document.activeElement).toBe(screen.getByTestId('add-widget'))

    await user.click(screen.getByTestId('add-widget'))
    const tray = screen.getByTestId('widget-tray')
    expect(tray).toHaveTextContent('The second widget.')
    await user.click(within(tray).getByRole('button', { name: 'Add Second' }))

    expect(screen.getByTestId('tile-second')).toBeInTheDocument()
    expect(document.activeElement).toBe(screen.getByTestId('move-second'))
  })

  it('offers one-click sizes', async () => {
    const user = userEvent.setup()
    board()
    await user.click(screen.getByTestId('customize-layout'))

    await user.click(screen.getByTestId('menu-first'))
    await user.click(screen.getByRole('button', { name: 'Full width' }))

    expect(placementOf('first')).toBe('1 / span 12 | 1 / span 4')
  })

  it('undoes and redoes, from the buttons and from the keyboard', async () => {
    const user = userEvent.setup()
    board()
    await user.click(screen.getByTestId('customize-layout'))
    screen.getByTestId('move-first').focus()
    await user.keyboard('{ArrowRight}')

    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(placementOf('first')).toBe('1 / span 4 | 1 / span 4')

    await user.click(screen.getByRole('button', { name: 'Redo' }))
    expect(placementOf('first')).toBe('5 / span 4 | 1 / span 4')

    await user.keyboard('{Control>}z{/Control}')
    expect(placementOf('first')).toBe('1 / span 4 | 1 / span 4')
  })
})

describe('keeping an arrangement', () => {
  it('saves what was arranged and returns to reading', async () => {
    const user = userEvent.setup()
    const { onSave } = board()
    await user.click(screen.getByTestId('customize-layout'))
    screen.getByTestId('move-first').focus()
    await user.keyboard('{ArrowRight}')

    await user.click(screen.getByTestId('save-layout'))

    expect(onSave).toHaveBeenCalledTimes(1)
    const saved = vi.mocked(onSave).mock.calls[0]?.[0]
    expect(saved?.wide?.tiles.find((tile) => tile.widget === 'first')).toMatchObject({ x: 4 })
    expect(saved?.medium).toBeNull()
    await waitFor(() => {
      expect(screen.queryByTestId('layout-toolbar')).toBeNull()
    })
    expect(document.activeElement).toBe(screen.getByTestId('customize-layout'))
  })

  it('saves nothing when nothing changed', async () => {
    const user = userEvent.setup()
    const { onSave } = board()
    await user.click(screen.getByTestId('customize-layout'))

    await user.click(screen.getByTestId('save-layout'))

    expect(onSave).not.toHaveBeenCalled()
    expect(screen.queryByTestId('layout-toolbar')).toBeNull()
  })

  it('forgets the stored arrangement when the owner puts the default back', async () => {
    const user = userEvent.setup()
    const { onSave } = board({
      stored: {
        wide: { ...DEFAULTS, tiles: [...DEFAULTS.tiles].reverse().map((tile, index) => ({ ...tile, x: index * 4 })) },
        medium: null,
        narrow: null,
      },
    })
    await user.click(screen.getByTestId('customize-layout'))

    await user.click(screen.getByTestId('reset-layout'))
    await user.click(screen.getByTestId('save-layout'))

    expect(onSave).toHaveBeenCalledWith(null)
  })

  it('stores nothing for a width class it did not arrange', async () => {
    const user = userEvent.setup()
    let notify: ResizeObserverCallback = () => undefined
    class Observer implements ResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        notify = callback
      }
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    vi.stubGlobal('ResizeObserver', Observer)
    const { onSave } = board()
    act(() => {
      notify([{ contentRect: { width: 400 } } as ResizeObserverEntry], {} as ResizeObserver)
    })

    await user.click(screen.getByTestId('customize-layout'))
    screen.getByTestId('move-first').focus()
    await user.keyboard('{ArrowDown}')
    await user.click(screen.getByTestId('save-layout'))

    const saved = vi.mocked(onSave).mock.calls[0]?.[0]
    expect(saved?.narrow).not.toBeNull()
    // The wide page is untouched, so a later improvement to its default still
    // reaches this owner.
    expect(saved?.wide).toBeNull()
    expect(saved?.medium).toBeNull()
  })

  it('discards the changes on Cancel', async () => {
    const user = userEvent.setup()
    const { onSave } = board()
    await user.click(screen.getByTestId('customize-layout'))
    screen.getByTestId('move-first').focus()
    await user.keyboard('{ArrowRight}')

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onSave).not.toHaveBeenCalled()
    expect(placementOf('first')).toBe('1 / span 4 | 1 / span 4')
  })

  it('stays open and says so when the arrangement could not be saved', async () => {
    const user = userEvent.setup()
    board({ onSave: () => Promise.reject(new Error('The layout could not be saved. Offline.')) })
    await user.click(screen.getByTestId('customize-layout'))
    screen.getByTestId('move-first').focus()
    await user.keyboard('{ArrowRight}')

    await user.click(screen.getByTestId('save-layout'))

    expect(await screen.findByRole('alert')).toHaveTextContent('Offline.')
    expect(screen.getByTestId('layout-toolbar')).toBeInTheDocument()
  })

  it('asks before a link leaves unsaved changes behind', async () => {
    const user = userEvent.setup()
    const confirm = vi.fn(() => false)
    vi.stubGlobal('confirm', confirm)
    render(
      <>
        <a href="#elsewhere">Elsewhere</a>
        <WidgetBoard
          label="Report"
          catalog={CATALOG}
          defaults={DEFAULTS}
          stored={null}
          widgets={CONTENT}
          available={() => true}
          editable
          onSave={() => Promise.resolve()}
        />
      </>,
    )
    await user.click(screen.getByTestId('customize-layout'))
    screen.getByTestId('move-first').focus()
    await user.keyboard('{ArrowRight}')

    await user.click(screen.getByText('Elsewhere'))

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(window.location.hash).not.toBe('#elsewhere')
  })
})

describe('the width the board is drawn at', () => {
  it('arranges a narrow board in two columns, derived from the wide arrangement', () => {
    let notify: ResizeObserverCallback = () => undefined
    class Observer implements ResizeObserver {
      constructor(callback: ResizeObserverCallback) {
        notify = callback
      }
      observe(): void {}
      unobserve(): void {}
      disconnect(): void {}
    }
    vi.stubGlobal('ResizeObserver', Observer)
    board()

    act(() => {
      notify([{ contentRect: { width: 400 } } as ResizeObserverEntry], {} as ResizeObserver)
    })

    expect(screen.getByTestId('widget-board').dataset['widthClass']).toBe('narrow')
    expect(placementOf('first')).toBe('1 / span 2 | 1 / span 4')
    expect(placementOf('second')).toBe('1 / span 2 | 5 / span 4')
  })
})
