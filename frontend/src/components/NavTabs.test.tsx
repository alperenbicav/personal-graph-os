import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NavTabs } from './NavTabs'

describe('NavTabs', () => {
  it('marks the active view and calls onSelectView for the clicked tab', () => {
    const onSelectView = vi.fn()
    render(<NavTabs activeView="table" onSelectView={onSelectView} />)

    expect(screen.getByRole('button', { name: 'Table' })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: 'Canvas' })).toHaveAttribute('aria-current', 'false')

    fireEvent.click(screen.getByRole('button', { name: 'Research' }))
    expect(onSelectView).toHaveBeenCalledWith('research')
  })

  it('renders every expected tab', () => {
    render(<NavTabs activeView="canvas" onSelectView={vi.fn()} />)
    for (const label of ['Canvas', 'Table', 'Kanban', 'Timeline', 'Search', 'Research']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })
})
