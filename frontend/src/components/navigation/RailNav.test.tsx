import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RailNav } from './RailNav'

describe('RailNav', () => {
  it('renders all primary navigation tabs including Agents', () => {
    const onSelectView = vi.fn()
    render(<RailNav activeView="graph" onSelectView={onSelectView} />)

    const expectedLabels = ['Graph', 'Tasks', 'Wiki', 'Research', 'Repositories', 'Agents', 'Activity']
    for (const label of expectedLabels) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    }
  })

  it('marks active tab with aria-current true and triggers selection', () => {
    const onSelectView = vi.fn()
    render(<RailNav activeView="agents" onSelectView={onSelectView} />)

    const agentsTab = screen.getByRole('button', { name: 'Agents' })
    expect(agentsTab).toHaveAttribute('aria-current', 'true')

    const graphTab = screen.getByRole('button', { name: 'Graph' })
    expect(graphTab).toHaveAttribute('aria-current', 'false')

    fireEvent.click(graphTab)
    expect(onSelectView).toHaveBeenCalledWith('graph')
  })

  it('calls onOpenSettings and onLock when clicked', () => {
    const onOpenSettings = vi.fn()
    const onLock = vi.fn()
    render(
      <RailNav
        activeView="tasks"
        onSelectView={vi.fn()}
        onOpenSettings={onOpenSettings}
        onLock={onLock}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Settings & Schema' }))
    expect(onOpenSettings).toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Workspace User' }))
    expect(onLock).toHaveBeenCalled()
  })
})
