import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TopBar } from './TopBar'

describe('TopBar', () => {
  it('renders the schema and export actions without a global quick-capture', () => {
    render(<TopBar onOpenSchemaEditor={vi.fn()} onExportWorkspace={vi.fn()} />)

    expect(screen.getByRole('button', { name: /schema/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /download export/i })).toBeInTheDocument()
    expect(screen.queryByLabelText(/quick capture/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/capture type/i)).not.toBeInTheDocument()
  })

  it('calls onExportWorkspace and reports a failure', async () => {
    const onExportWorkspace = vi.fn().mockRejectedValue(new Error('boom'))
    render(<TopBar onOpenSchemaEditor={vi.fn()} onExportWorkspace={onExportWorkspace} />)

    fireEvent.click(screen.getByRole('button', { name: /download export/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent('boom')
  })

  it('opens the schema editor', () => {
    const onOpenSchemaEditor = vi.fn()
    render(<TopBar onOpenSchemaEditor={onOpenSchemaEditor} onExportWorkspace={vi.fn()} />)

    fireEvent.click(screen.getByRole('button', { name: /schema/i }))
    expect(onOpenSchemaEditor).toHaveBeenCalled()
  })

  it('triggers onOpenCommandPalette when search button is clicked', () => {
    const onOpenCommandPalette = vi.fn()
    render(
      <TopBar
        onOpenSchemaEditor={vi.fn()}
        onExportWorkspace={vi.fn()}
        onOpenCommandPalette={onOpenCommandPalette}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /search or jump/i }))
    expect(onOpenCommandPalette).toHaveBeenCalled()
  })
})
