import type { HTMLAttributes } from 'react'

export interface SkeletonProps extends HTMLAttributes<HTMLDivElement> {
  variant?: 'text' | 'rectangular' | 'circular'
  width?: string | number
  height?: string | number
}

export function Skeleton({
  variant = 'text',
  width,
  height,
  className = '',
  style,
  ...props
}: SkeletonProps) {
  const variantClass =
    variant === 'circular'
      ? 'skeleton-circular'
      : variant === 'rectangular'
        ? 'skeleton-rectangular'
        : 'skeleton-text'

  return (
    <div
      className={`skeleton-shimmer ${variantClass} ${className}`.trim()}
      style={{
        width: width !== undefined ? width : undefined,
        height: height !== undefined ? height : undefined,
        ...style,
      }}
      aria-hidden="true"
      {...props}
    />
  )
}

export function SkeletonRow({ count = 1 }: { count?: number }) {
  return (
    <div className="skeleton-row-group" aria-busy="true" aria-label="Loading content">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="skeleton-row">
          <Skeleton variant="circular" width={24} height={24} />
          <div className="skeleton-row-lines">
            <Skeleton variant="text" width="60%" height={14} />
            <Skeleton variant="text" width="35%" height={10} />
          </div>
        </div>
      ))}
    </div>
  )
}

export function SkeletonBoard() {
  return (
    <div className="skeleton-board" aria-busy="true" aria-label="Loading task board">
      {Array.from({ length: 4 }).map((_, colIndex) => (
        <div key={colIndex} className="skeleton-board-column">
          <div className="skeleton-column-header">
            <Skeleton variant="text" width="50%" height={12} />
            <Skeleton variant="rectangular" width={20} height={14} />
          </div>
          <div className="skeleton-column-cards">
            {Array.from({ length: 3 }).map((_, cardIndex) => (
              <div key={cardIndex} className="skeleton-card">
                <Skeleton variant="text" width="80%" height={14} />
                <div className="skeleton-card-meta">
                  <Skeleton variant="rectangular" width={50} height={16} />
                  <Skeleton variant="text" width={60} height={10} />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
