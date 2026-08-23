import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Skeleton, SkeletonRow, SkeletonBoard } from './Skeleton'

describe('Skeleton', () => {
  it('renders default text skeleton with shimmer class', () => {
    const { container } = render(<Skeleton />)
    const el = container.querySelector('.skeleton-shimmer')
    expect(el).toBeInTheDocument()
    expect(el).toHaveClass('skeleton-text')
  })

  it('renders circular and rectangular variants with custom dimensions', () => {
    render(
      <Skeleton variant="circular" width={32} height={32} data-testid="skel-circle" />,
    )
    const el = screen.getByTestId('skel-circle')
    expect(el).toHaveClass('skeleton-circular')
    expect(el).toHaveStyle({ width: '32px', height: '32px' })
  })

  it('renders multiple skeleton rows with aria-busy set', () => {
    render(<SkeletonRow count={3} />)
    const group = screen.getByLabelText(/loading content/i)
    expect(group).toHaveAttribute('aria-busy', 'true')
    expect(group.querySelectorAll('.skeleton-row')).toHaveLength(3)
  })

  it('renders skeleton board columns with cards', () => {
    render(<SkeletonBoard />)
    const board = screen.getByLabelText(/loading task board/i)
    expect(board).toHaveAttribute('aria-busy', 'true')
    expect(board.querySelectorAll('.skeleton-board-column')).toHaveLength(4)
  })
})
