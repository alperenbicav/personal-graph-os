import { useState } from 'react'
import type { AppView } from '../NavTabs'
import type { Workspace } from '../../types'

export interface TopbarProps {
  workspace: Workspace | null
  activeView: AppView
  activeAgentCount: number
  isDockOpen: boolean
  onToggleDock: () => void
  onOpenCommandPalette: () => void
  onOpenSchemaEditor: () => void
  onExportWorkspace: () => Promise<void>
}

const VIEW_TITLES: Record<AppView, string> = {
  graph: 'Graph Canvas',
  tasks: 'Tasks & Projects',
  wiki: 'Wiki Knowledge',
  research: 'Research Hub',
  repositories: 'Repositories',
  agents: 'Agents Fleet',
  activity: 'Activity Log',
  search: 'Search',
}

export function Topbar({
  workspace,
  activeView,
  activeAgentCount,
  isDockOpen,
  onToggleDock,
  onOpenCommandPalette,
  onOpenSchemaEditor,
  onExportWorkspace,
}: TopbarProps) {
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

  const workspaceName = workspace?.name || 'Personal'
  const viewTitle = VIEW_TITLES[activeView] || activeView

  return (
    <header className="v2-topbar">
      <div className="v2-crumb">
        <span className="brand-name">Personal Graph OS</span>
        <span style={{ color: 'var(--v2-t3)' }}>/</span>
        <span>{workspaceName}</span>
        <span style={{ color: 'var(--v2-t3)' }}>/</span>
        <b>{viewTitle}</b>
      </div>

      <button
        type="button"
        className="v2-srch"
        onClick={onOpenCommandPalette}
        aria-label="Search or jump to node (Cmd+K)"
        title="Search or jump (Cmd+K)"
      >
        <span aria-hidden="true" style={{ fontSize: 13, opacity: 0.7 }}>
          🔍
        </span>
        <span>Search or jump…</span>
        <span className="v2-kbd">⌘K</span>
      </button>

      <button
        type="button"
        className={`v2-live-pill ${isDockOpen ? 'active' : ''}`}
        onClick={onToggleDock}
        title={isDockOpen ? 'Collapse Agent Dock' : 'Expand Agent Dock'}
        aria-label="Active agents indicator"
      >
        <span className="v2-pulse" />
        <span>{activeAgentCount} {activeAgentCount === 1 ? 'agent' : 'agents'} active</span>
      </button>

      <button
        type="button"
        className="v2-topbar-btn"
        onClick={onOpenSchemaEditor}
        title="Edit graph schema"
      >
        Schema
      </button>

      <button
        type="button"
        className="v2-topbar-btn"
        onClick={handleExport}
        disabled={isExporting}
        aria-label="Download export"
        title="Export workspace data"
      >
        {isExporting ? 'Exporting…' : 'Export'}
      </button>

      {exportError && (
        <span style={{ color: 'var(--v2-rd)', fontSize: '12px' }} role="alert">
          {exportError}
        </span>
      )}
    </header>
  )
}

