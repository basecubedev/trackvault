import '@testing-library/jest-dom/vitest'

// jsdom implements neither of these, and both are reached by code paths the
// component tests exercise. Stubbing them here keeps the stubs in one place
// rather than in every test that happens to render a chart.
class ResizeObserverStub implements ResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

const environment = globalThis as unknown as Record<string, unknown>

environment['ResizeObserver'] ??= ResizeObserverStub

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
