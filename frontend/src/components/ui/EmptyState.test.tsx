import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { EmptyState } from './EmptyState'

describe('EmptyState', () => {
  it('renders title and hint text with role status', () => {
    render(<EmptyState title="No items found" hint="Try adjusting your search criteria." />)
    const container = screen.getByRole('status')
    expect(container).toBeInTheDocument()
    expect(screen.getByText('No items found')).toBeInTheDocument()
    expect(screen.getByText('Try adjusting your search criteria.')).toBeInTheDocument()
  })

  it('renders icon and action button when provided', () => {
    render(
      <EmptyState
        icon="🚀"
        title="Get started"
        action={<button type="button">Create item</button>}
      />,
    )
    expect(screen.getByText('🚀')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create item' })).toBeInTheDocument()
  })
})
