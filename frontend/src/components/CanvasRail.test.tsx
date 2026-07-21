import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CanvasRail } from './CanvasRail'
import type { Canvas } from '../types'

const canvases: Canvas[] = [
  { id: 'c1', workspace_id: 'ws', name: 'Main', created_at: '2026-01-01T00:00:00Z' },
  { id: 'c2', workspace_id: 'ws', name: 'Research', created_at: '2026-01-01T00:00:00Z' },
]

describe('CanvasRail', () => {
  it('renders every canvas and marks the active one', () => {
    render(
      <CanvasRail canvases={canvases} activeCanvasId="c2" onSelectCanvas={vi.fn()} onCreateCanvas={vi.fn()} />,
    )
    expect(screen.getByRole('button', { name: /main/i })).toHaveAttribute('aria-current', 'false')
    expect(screen.getByRole('button', { name: /research/i })).toHaveAttribute('aria-current', 'true')
  })

  it('calls onSelectCanvas with the clicked canvas id', () => {
    const onSelectCanvas = vi.fn()
    render(
      <CanvasRail canvases={canvases} activeCanvasId="c1" onSelectCanvas={onSelectCanvas} onCreateCanvas={vi.fn()} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /research/i }))
    expect(onSelectCanvas).toHaveBeenCalledWith('c2')
  })

  it('calls onCreateCanvas when "+ New canvas" is clicked', () => {
    const onCreateCanvas = vi.fn()
    render(
      <CanvasRail canvases={canvases} activeCanvasId="c1" onSelectCanvas={vi.fn()} onCreateCanvas={onCreateCanvas} />,
    )
    fireEvent.click(screen.getByRole('button', { name: /new canvas/i }))
    expect(onCreateCanvas).toHaveBeenCalledTimes(1)
  })
})
