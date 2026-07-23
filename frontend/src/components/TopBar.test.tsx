import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TopBar } from './TopBar'
import type { NodeType } from '../types'

const nodeTypes: NodeType[] = [
  { id: 'nt-note', name: 'Note', icon: 'note', color_hex: '#888', field_definitions: [], status_definitions: [] },
  { id: 'nt-task', name: 'Task', icon: 'task', color_hex: '#26c', field_definitions: [], status_definitions: [] },
]

describe('TopBar', () => {
  it('does not call onCapture for a blank title', () => {
    const onCapture = vi.fn()
    render(<TopBar captureNodeTypes={nodeTypes} onCapture={onCapture} isCapturing={false} onOpenSchemaEditor={vi.fn()} onExportWorkspace={vi.fn()} />)
    fireEvent.click(screen.getByRole('button', { name: /add/i }))
    expect(onCapture).not.toHaveBeenCalled()
  })

  it('captures the trimmed title with the selected node type on Add', () => {
    const onCapture = vi.fn()
    render(<TopBar captureNodeTypes={nodeTypes} onCapture={onCapture} isCapturing={false} onOpenSchemaEditor={vi.fn()} onExportWorkspace={vi.fn()} />)

    fireEvent.change(screen.getByLabelText(/capture type/i), { target: { value: 'nt-task' } })
    fireEvent.change(screen.getByLabelText(/quick capture/i), { target: { value: '  Write report  ' } })
    fireEvent.click(screen.getByRole('button', { name: /add/i }))

    expect(onCapture).toHaveBeenCalledWith('nt-task', 'Write report')
  })

  it('submits on Enter and clears the input', () => {
    const onCapture = vi.fn()
    render(<TopBar captureNodeTypes={nodeTypes} onCapture={onCapture} isCapturing={false} onOpenSchemaEditor={vi.fn()} onExportWorkspace={vi.fn()} />)

    const input = screen.getByLabelText(/quick capture/i)
    fireEvent.change(input, { target: { value: 'Read the spec' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(onCapture).toHaveBeenCalledWith('nt-note', 'Read the spec')
    expect(input).toHaveValue('')
  })

  it('disables Add while a capture is in flight', () => {
    render(<TopBar captureNodeTypes={nodeTypes} onCapture={vi.fn()} isCapturing={true} onOpenSchemaEditor={vi.fn()} onExportWorkspace={vi.fn()} />)
    expect(screen.getByRole('button', { name: /add/i })).toBeDisabled()
  })
})
