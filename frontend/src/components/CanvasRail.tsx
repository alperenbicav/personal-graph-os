import type { Canvas } from '../types'

interface CanvasRailProps {
  canvases: Canvas[]
  activeCanvasId: string | null
  onSelectCanvas: (canvasId: string) => void
  onCreateCanvas: () => void
}

export function CanvasRail({
  canvases,
  activeCanvasId,
  onSelectCanvas,
  onCreateCanvas,
}: CanvasRailProps) {
  return (
    <nav className="rail" aria-label="Canvases">
      <div className="rail-label">Canvases</div>
      {canvases.map((canvas) => (
        <button
          key={canvas.id}
          type="button"
          className="rail-item"
          aria-current={canvas.id === activeCanvasId}
          onClick={() => onSelectCanvas(canvas.id)}
        >
          <span className="dot" />
          {canvas.name}
        </button>
      ))}
      <button type="button" className="rail-new-canvas" onClick={onCreateCanvas}>
        + New canvas
      </button>
    </nav>
  )
}
