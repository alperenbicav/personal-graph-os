import '@testing-library/jest-dom/vitest'

// React Flow measures nodes via ResizeObserver, which jsdom does not implement.
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
