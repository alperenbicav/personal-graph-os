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

  it('toggles a tab\'s info popover open and closed', () => {
    render(<NavTabs activeView="canvas" onSelectView={vi.fn()} />)
    expect(screen.queryByRole('note')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'About Search' }))
    expect(screen.getByRole('note')).toHaveTextContent(/full-text search/i)

    fireEvent.click(screen.getByRole('button', { name: 'About Search' }))
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('shows only one info popover at a time and closes it when a tab is selected', () => {
    const onSelectView = vi.fn()
    render(<NavTabs activeView="canvas" onSelectView={onSelectView} />)

    fireEvent.click(screen.getByRole('button', { name: 'About Search' }))
    fireEvent.click(screen.getByRole('button', { name: 'About Research' }))
    expect(screen.getAllByRole('note')).toHaveLength(1)
    expect(screen.getByRole('note')).toHaveTextContent(/research resources/i)

    fireEvent.click(screen.getByRole('button', { name: 'Table' }))
    expect(onSelectView).toHaveBeenCalledWith('table')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })
})
