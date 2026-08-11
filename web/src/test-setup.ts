import '@testing-library/jest-dom/vitest'

// jsdom implements neither of these, and both are reached by code paths the
// component tests exercise. Stubbing them here keeps the stubs in one place
// rather than in every test that happens to render a chart.
class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/**
 * Nothing scrolls into view unless a test says so.
 *
 * The default is deliberately inert: a component that only works once it is
 * visible must ask for that state explicitly, so a test that never mentions
 * visibility does not silently start fetching what a reader cannot see.
 */
class IntersectionObserverStub implements IntersectionObserver {
  readonly root = null
  readonly rootMargin = ''
  readonly thresholds: readonly number[] = []
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): IntersectionObserverEntry[] {
    return []
  }
}

const environment = globalThis as unknown as Record<string, unknown>

environment['ResizeObserver'] ??= ResizeObserverStub
environment['IntersectionObserver'] ??= IntersectionObserverStub

environment['matchMedia'] ??= (query: string) => ({
  matches: false,
  media: query,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})
