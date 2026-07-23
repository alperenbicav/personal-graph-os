import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UpdatePrompt } from './UpdatePrompt'

const updateServiceWorker = vi.fn()
const setNeedRefresh = vi.fn()
let needRefresh = false

vi.mock('virtual:pwa-register/react', () => ({
  useRegisterSW: () => ({
    needRefresh: [needRefresh, setNeedRefresh],
    offlineReady: [false, vi.fn()],
    updateServiceWorker,
  }),
}))

afterEach(() => {
  vi.clearAllMocks()
})

describe('UpdatePrompt', () => {
  it('renders nothing while no update is ready', () => {
    needRefresh = false
    const { container } = render(<UpdatePrompt />)
    expect(container).toBeEmptyDOMElement()
  })

  it('offers a reload once a new service worker is waiting, and never auto-applies it', () => {
    needRefresh = true
    render(<UpdatePrompt />)

    expect(screen.getByText(/new version is available/i)).toBeInTheDocument()
    expect(updateServiceWorker).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /reload/i }))
    expect(updateServiceWorker).toHaveBeenCalledWith(true)
  })

  it('can be dismissed without reloading', () => {
    needRefresh = true
    render(<UpdatePrompt />)

    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))

    expect(updateServiceWorker).not.toHaveBeenCalled()
    expect(setNeedRefresh).toHaveBeenCalledWith(false)
  })
})
