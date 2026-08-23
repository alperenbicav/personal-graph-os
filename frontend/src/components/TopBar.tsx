import { useState } from 'react'

interface TopBarProps {
  onOpenSchemaEditor: () => void
  onExportWorkspace: () => Promise<void>
  onOpenCommandPalette?: () => void
}

export function TopBar({
  onOpenSchemaEditor,
  onExportWorkspace,
  onOpenCommandPalette,
}: TopBarProps) {
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

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-name">Personal Graph OS</span>
        <span className="brand-tag">Workspace</span>
      </div>

      <div className="topbar-actions">
        {onOpenCommandPalette && (
          <button
            type="button"
            className="topbar-search-button"
            onClick={onOpenCommandPalette}
            aria-label="Search or jump (Cmd+K)"
          >
            <span className="topbar-search-icon" aria-hidden>⌕</span>
            <span className="topbar-search-label">Search or jump…</span>
            <kbd className="topbar-search-kbd">⌘K</kbd>
          </button>
        )}
        <button type="button" className="schema-editor-button" onClick={onOpenSchemaEditor}>
          Schema
        </button>
        <button type="button" onClick={handleExport} disabled={isExporting}>
          {isExporting ? 'Exporting…' : 'Download export'}
        </button>
      </div>
      {exportError && (
        <span className="view-error" role="alert">
          {exportError}
        </span>
      )}
    </header>
  )
}
