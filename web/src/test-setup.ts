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

/**
 * Read a blob's bytes, which `jsdom` can do and does not offer a method for.
 *
 * Every browser this application supports has had `Blob.prototype.arrayBuffer`
 * for years; `jsdom` still only exposes the `FileReader` route to the same
 * bytes. Bridging it here rather than in the code under test keeps a test
 * environment's gap out of a module that has no such gap in a browser.
 */
if (typeof Blob.prototype.arrayBuffer !== 'function') {
  Blob.prototype.arrayBuffer = function readBytes(this: Blob): Promise<ArrayBuffer> {
    return new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = () => {
        resolve(reader.result as ArrayBuffer)
      }
      reader.onerror = () => {
        reject(reader.error ?? new Error('the blob could not be read'))
      }
      reader.readAsArrayBuffer(this)
    })
  }
}

/**
 * Address a blob so an `<img>` can show it, which `jsdom` also does not do.
 *
 * Each address is distinct, so a component that shows a picture and a component
 * that shows a different one cannot pass a test by accident.
 */
let addresses = 0

if (typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = (): string => {
    addresses += 1
    return `blob:trackvault/${String(addresses)}`
  }
  URL.revokeObjectURL = (): void => {}
}

/**
 * Pointer capture, which `jsdom` does not implement.
 *
 * A drag needs it and a browser has had it for a decade; stubbing it here keeps
 * the gap in the test environment rather than in the code under test.
 */
if (typeof Element.prototype.setPointerCapture !== 'function') {
  Element.prototype.setPointerCapture = function capture(): void {}
  Element.prototype.releasePointerCapture = function release(): void {}
  Element.prototype.hasPointerCapture = function held(): boolean {
    return false
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
