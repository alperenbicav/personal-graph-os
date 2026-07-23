import { useState } from 'react'
import type { NodeType } from '../types'

interface TopBarProps {
  captureNodeTypes: NodeType[]
  onCapture: (nodeTypeId: string, title: string) => void
  isCapturing: boolean
  onOpenSchemaEditor: () => void
  onExportWorkspace: () => Promise<void>
}

export function TopBar({
  captureNodeTypes,
  onCapture,
  isCapturing,
  onOpenSchemaEditor,
  onExportWorkspace,
}: TopBarProps) {
  const [nodeTypeId, setNodeTypeId] = useState(captureNodeTypes[0]?.id ?? '')
  const [title, setTitle] = useState('')
  const [isExporting, setIsExporting] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  async function handleExport() {
    setIsExporting(true)
    setExportError(null)
    try {
      await onExportWorkspace()
    } catch (error) {
      setExportError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsExporting(false)
    }
  }

  const activeNodeTypeId = nodeTypeId || captureNodeTypes[0]?.id || ''

  const submit = () => {
    const trimmed = title.trim()
    if (!trimmed || !activeNodeTypeId) return
    onCapture(activeNodeTypeId, trimmed)
    setTitle('')
  }

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-name">Personal Graph OS</span>
        <span className="brand-tag">Canvas</span>
      </div>

      <div className="capture">
        <label className="sr-only" htmlFor="capture-kind">
          Capture type
        </label>
        <select
          id="capture-kind"
          value={activeNodeTypeId}
          onChange={(event) => setNodeTypeId(event.target.value)}
        >
          {captureNodeTypes.map((nodeType) => (
            <option key={nodeType.id} value={nodeType.id}>
              {nodeType.name}
            </option>
          ))}
        </select>
        <label className="sr-only" htmlFor="capture-input">
          Quick capture
        </label>
        <input
          id="capture-input"
          type="text"
          placeholder="Capture a task, note, or link — title only, refine later…"
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') submit()
          }}
        />
        <button type="button" onClick={submit} disabled={isCapturing || !title.trim()}>
          Add
        </button>
      </div>

      <button type="button" className="schema-editor-button" onClick={onOpenSchemaEditor}>
        Schema
      </button>
      <button type="button" onClick={handleExport} disabled={isExporting}>
        {isExporting ? 'Exporting…' : 'Download export'}
      </button>
      {exportError && (
        <span className="view-error" role="alert">
          {exportError}
        </span>
      )}
    </header>
  )
}
