import { useEffect, useState } from 'react'
import * as api from '../api/client'
import { messageFor } from '../lib/errors'
import type { SavedView, ViewKind } from '../types'

interface SavedViewsPanelProps {
  workspaceId: string
  viewKind: ViewKind
}

/** Minimal saved-view surface for a projection view: list this view kind's saved views and
 * create one under the current name. Self-fetching, like `FilesPanel`/`SchemaEditor`. Saves
 * the empty default query only — there is no filter-builder UI yet (product decision: no
 * arbitrary filter expressions), so a saved view here names/preserves "this view kind, no
 * filter" rather than a bespoke query. */
export function SavedViewsPanel({ workspaceId, viewKind }: SavedViewsPanelProps) {
  const [savedViews, setSavedViews] = useState<SavedView[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    let isCurrent = true
    setIsLoading(true)
    setLoadError(null)
    api
      .listSavedViews(workspaceId)
      .then((views) => {
        if (isCurrent) setSavedViews(views.filter((view) => view.view_kind === viewKind))
      })
      .catch((error) => {
        if (isCurrent) setLoadError(messageFor(error))
      })
      .finally(() => {
        if (isCurrent) setIsLoading(false)
      })
    return () => {
      isCurrent = false
    }
  }, [workspaceId, viewKind])

  async function handleSave() {
    if (!name.trim()) return
    setIsSaving(true)
    setActionError(null)
    try {
      const created = await api.createSavedView(workspaceId, name.trim(), viewKind)
      setSavedViews((current) => [...current, created])
      setName('')
    } catch (error) {
      setActionError(messageFor(error))
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="saved-views-panel">
      <span className="field-label">Saved views</span>
      {loadError && (
        <p className="field-error" role="alert">
          {loadError}
        </p>
      )}
      {actionError && (
        <p className="field-error" role="alert">
          {actionError}
        </p>
      )}
      {!isLoading && savedViews.length === 0 && !loadError && (
        <p className="files-panel-status">No saved views yet.</p>
      )}
      <ul className="saved-views-list">
        {savedViews.map((view) => (
          <li key={view.id} className="saved-views-row">
            {view.name}
          </li>
        ))}
      </ul>
      <div className="saved-views-create-row">
        <label className="sr-only" htmlFor={`saved-view-name-${viewKind}`}>
          Saved view name
        </label>
        <input
          id={`saved-view-name-${viewKind}`}
          type="text"
          placeholder="Saved view name"
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
        <button type="button" disabled={!name.trim() || isSaving} onClick={handleSave}>
          Save view
        </button>
      </div>
    </div>
  )
}
