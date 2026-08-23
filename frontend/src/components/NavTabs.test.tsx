import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { NavTabs } from './NavTabs'

describe('NavTabs', () => {
  it('marks the active view and calls onSelectView for the clicked tab', () => {
    const onSelectView = vi.fn()
    render(<NavTabs activeView="tasks" onSelectView={onSelectView} />)

    expect(screen.getByRole('button', { name: 'Tasks' })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-current', 'false')

    fireEvent.click(screen.getByRole('button', { name: 'Research' }))
    expect(onSelectView).toHaveBeenCalledWith('research')
  })

  it('renders the primary tabs (search is now in the global command palette)', () => {
    render(<NavTabs activeView="graph" onSelectView={vi.fn()} />)
    const labels = ['Graph', 'Tasks', 'Wiki', 'Research', 'Repositories', 'Agents', 'Activity']
    for (const label of labels) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
    for (const removed of ['Search', 'Canvas', 'Table', 'Kanban', 'Timeline', 'Discovery']) {
      expect(screen.queryByRole('button', { name: removed })).not.toBeInTheDocument()
    }
  })

  it('exposes each tab description as a native tooltip', () => {
    render(<NavTabs activeView="graph" onSelectView={vi.fn()} />)

    const graphTab = screen.getByRole('button', { name: 'Graph' })
    expect(graphTab.getAttribute('title')).toMatch(/visual graph canvas/i)
    expect(screen.queryByRole('button', { name: /about graph/i })).not.toBeInTheDocument()
  })

  it('keeps descriptions available on every tab without persistent info buttons', () => {
    render(<NavTabs activeView="tasks" onSelectView={vi.fn()} />)

    for (const label of ['Graph', 'Tasks', 'Wiki', 'Research', 'Repositories', 'Agents', 'Activity']) {
      expect(screen.getByRole('button', { name: label })).toHaveAttribute('title')
    }
  })
})
