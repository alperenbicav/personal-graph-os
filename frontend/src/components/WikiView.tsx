import { useEffect, useMemo, useState } from 'react'
import type { CreateDocumentInput, UpdateDocumentMetadataPatch } from '../api/client'
import type {
  Collection,
  DocumentDetail,
  DocumentKind,
  DocumentVersion,
  Tag,
  WikiDocument,
} from '../types'
import { renderMarkdown } from '../lib/markdown'
import { EmptyState, SegmentedControl, SlideOver } from './ui'

const REST_ACTOR_NAME = 'human/local-user/rest'

const KIND_OPTIONS: { value: DocumentKind; label: string }[] = [
  { value: 'note', label: 'Note' },
  { value: 'lesson', label: 'Lesson' },
  { value: 'documentation', label: 'Documentation' },
  { value: 'plan', label: 'Plan' },
]

type EditorMode = 'write' | 'preview'

function parseTagNames(input: string): string[] {
  return Array.from(
    new Set(
      input
        .split(',')
        .map((name) => name.trim())
        .filter((name) => name.length > 0),
    ),
  )
}

function timeAgo(isoDate: string): string {
  try {
    const diffMs = Date.now() - new Date(isoDate).getTime()
    const diffSec = Math.floor(diffMs / 1000)
    if (diffSec < 45) return 'just now'
    const diffMin = Math.floor(diffSec / 60)
    if (diffMin < 60) return `${diffMin}m ago`
    const diffHrs = Math.floor(diffMin / 60)
    if (diffHrs < 24) return `${diffHrs}h ago`
    return `${Math.floor(diffHrs / 24)}d ago`
  } catch {
    return 'recently'
  }
}

interface WikiViewProps {
  onLoadDocuments: (collectionId?: string) => Promise<WikiDocument[]>
  onLoadDetail: (documentId: string) => Promise<DocumentDetail>
  onLoadVersions: (documentId: string) => Promise<DocumentVersion[]>
  onCreateDocument: (input: Omit<CreateDocumentInput, 'workspace_id'>) => Promise<WikiDocument>
  onUpdateMetadata: (documentId: string, patch: UpdateDocumentMetadataPatch) => Promise<WikiDocument>
  onEditBody: (documentId: string, bodyMarkdown: string) => Promise<DocumentVersion>
  onLoadCollections: () => Promise<Collection[]>
  onCreateCollection: (name: string) => Promise<Collection>
  onLoadTags: () => Promise<Tag[]>
  onAddDocumentLink: (documentId: string, targetDocumentId: string) => Promise<void>
  selectedDocumentId?: string | null
  onSelectDocument?: (documentId: string | null) => void
}

export function WikiView({
  onLoadDocuments,
  onLoadDetail,
  onLoadVersions,
  onCreateDocument,
  onUpdateMetadata,
  onEditBody,
  onLoadCollections,
  onCreateCollection,
  onLoadTags,
  onAddDocumentLink,
  selectedDocumentId: externalSelectedDocId,
  onSelectDocument: onExternalSelectDoc,
}: WikiViewProps) {
  const [documents, setDocuments] = useState<WikiDocument[]>([])
  const [collections, setCollections] = useState<Collection[]>([])
  const [_tags, setTags] = useState<Tag[]>([])
  const [collectionFilter, setCollectionFilter] = useState<'all' | 'uncategorized' | string>('all')
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(externalSelectedDocId ?? null)
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [versions, setVersions] = useState<DocumentVersion[]>([])
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [editorMode, setEditorMode] = useState<EditorMode>('write')
  const [titleDraft, setTitleDraft] = useState('')
  const [bodyDraft, setBodyDraft] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [newCollectionName, setNewCollectionName] = useState('')
  const [newTagInput, setNewTagInput] = useState('')
  const [newLinkTargetId, setNewLinkTargetId] = useState('')
  const [searchQuery, setSearchQuery] = useState('')

  async function refreshTaxonomy() {
    const [loadedCollections, loadedTags] = await Promise.all([onLoadCollections(), onLoadTags()])
    setCollections(loadedCollections)
    setTags(loadedTags)
  }

  async function refreshDocuments() {
    setLoadError(null)
    try {
      const loaded = await onLoadDocuments()
      setDocuments(loaded)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  useEffect(() => {
    refreshDocuments()
    refreshTaxonomy()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (externalSelectedDocId && externalSelectedDocId !== selectedDocumentId) {
      void selectDocument(externalSelectedDocId)
    } else if (externalSelectedDocId === null && selectedDocumentId !== null) {
      setSelectedDocumentId(null)
      setDetail(null)
    }
  }, [externalSelectedDocId])

  async function selectDocument(documentId: string) {
    setSelectedDocumentId(documentId)
    onExternalSelectDoc?.(documentId)
    setDetailsOpen(false)
    setVersions([])
    try {
      const loaded = await onLoadDetail(documentId)
      setDetail(loaded)
      setTitleDraft(loaded.document.title)
      setBodyDraft(loaded.latest_version.body_markdown)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  // Instant creation without modal (Notion model)
  async function handleInstantCreatePage(customTitle?: string) {
    setLoadError(null)
    try {
      const activeColId =
        collectionFilter !== 'all' && collectionFilter !== 'uncategorized'
          ? collectionFilter
          : null
      const created = await onCreateDocument({
        title: customTitle || 'Untitled',
        kind: 'note',
        source: REST_ACTOR_NAME,
        body_markdown: '',
        collection_id: activeColId,
        tag_names: [],
      })
      await refreshDocuments()
      await refreshTaxonomy()
      await selectDocument(created.id)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function handleCreateCollectionSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = newCollectionName.trim()
    if (!trimmed) return
    try {
      const created = await onCreateCollection(trimmed)
      setNewCollectionName('')
      await refreshTaxonomy()
      setCollectionFilter(created.id)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  const visibleDocuments = useMemo(() => {
    let filtered = documents
    if (collectionFilter === 'uncategorized') {
      filtered = filtered.filter((d) => d.collection_id === null)
    } else if (collectionFilter !== 'all') {
      filtered = filtered.filter((d) => d.collection_id === collectionFilter)
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase()
      filtered = filtered.filter((d) => d.title.toLowerCase().includes(q))
    }
    return filtered
  }, [documents, collectionFilter, searchQuery])

  const isDirty = detail !== null && bodyDraft !== detail.latest_version.body_markdown

  async function saveBody() {
    if (!selectedDocumentId || !isDirty) return
    setIsSaving(true)
    try {
      await onEditBody(selectedDocumentId, bodyDraft)
      await selectDocument(selectedDocumentId)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsSaving(false)
    }
  }

  // Cmd/Ctrl+S saves the body like a native document editor.
  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        if (!detail || !isDirty) return
        event.preventDefault()
        void saveBody()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail, isDirty, bodyDraft, selectedDocumentId])

  async function commitTitle() {
    if (!detail) return
    const trimmed = titleDraft.trim()
    if (!trimmed || trimmed === detail.document.title) return
    await saveMetadata({ title: trimmed })
  }

  async function saveMetadata(patch: UpdateDocumentMetadataPatch) {
    if (!selectedDocumentId) return
    try {
      await onUpdateMetadata(selectedDocumentId, patch)
      await refreshDocuments()
      const loaded = await onLoadDetail(selectedDocumentId)
      setDetail(loaded)
      setTitleDraft(loaded.document.title)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  function handleAddTag() {
    if (!detail) return
    const parsed = parseTagNames(newTagInput)
    if (parsed.length === 0) return
    const existing = detail.tags.map((t) => t.name)
    const combined = Array.from(new Set([...existing, ...parsed]))
    setNewTagInput('')
    void saveMetadata({ tag_names: combined })
  }

  function handleRemoveTag(name: string) {
    if (!detail) return
    void saveMetadata({ tag_names: detail.tags.map((tag) => tag.name).filter((n) => n !== name) })
  }

  async function loadVersionsForDetails() {
    if (!selectedDocumentId) return
    try {
      const loaded = await onLoadVersions(selectedDocumentId)
      setVersions(loaded)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  async function submitLink() {
    if (!selectedDocumentId || !newLinkTargetId.trim()) return
    try {
      await onAddDocumentLink(selectedDocumentId, newLinkTargetId.trim())
      setNewLinkTargetId('')
      const loaded = await onLoadDetail(selectedDocumentId)
      setDetail(loaded)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  const wordCount = useMemo(() => {
    if (!bodyDraft) return 0
    return bodyDraft.trim().split(/\s+/).filter(Boolean).length
  }, [bodyDraft])

  return (
    <div className="v2-wiki-layout" aria-label="Wiki Workspace">
      {/* Wiki Left Sidebar: Collections & Pages */}
      <aside className="v2-wiki-sidebar" aria-label="Wiki Navigation">
        <div className="v2-wiki-sidebar-header">
          <div className="v2-wiki-title-group">
            <span className="v2-wiki-icon" aria-hidden="true">▤</span>
            <h2 className="v2-wiki-heading">Wiki</h2>
            <span className="v2-badge">{documents.length}</span>
          </div>
          <button
            type="button"
            className="v2-chipbtn prime"
            onClick={() => handleInstantCreatePage()}
            aria-label="+ New page"
            title="Create new page (instant full-page editor)"
          >
            + New page
          </button>
        </div>

        <div className="v2-wiki-search-box">
          <input
            type="text"
            className="v2-wiki-search-input"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filter pages…"
            aria-label="Filter pages"
          />
        </div>

        {/* Collections Filter Tree */}
        <div className="v2-wiki-section-header">COLLECTIONS</div>
        <div className="v2-wiki-collections-list" role="navigation" aria-label="Collections Filter">
          <button
            type="button"
            className={`v2-wiki-collection-btn ${collectionFilter === 'all' ? 'active' : ''}`}
            onClick={() => setCollectionFilter('all')}
          >
            <span>📁 All Documents</span>
            <span className="v2-count-pill">{documents.length}</span>
          </button>
          <button
            type="button"
            className={`v2-wiki-collection-btn ${collectionFilter === 'uncategorized' ? 'active' : ''}`}
            onClick={() => setCollectionFilter('uncategorized')}
          >
            <span>📄 Uncategorized</span>
            <span className="v2-count-pill">
              {documents.filter((d) => d.collection_id === null).length}
            </span>
          </button>
          {collections.map((col) => {
            const count = documents.filter((d) => d.collection_id === col.id).length
            return (
              <button
                key={col.id}
                type="button"
                className={`v2-wiki-collection-btn ${collectionFilter === col.id ? 'active' : ''}`}
                onClick={() => setCollectionFilter(col.id)}
              >
                <span>📘 {col.name}</span>
                <span className="v2-count-pill">{count}</span>
              </button>
            )
          })}
        </div>

        <form onSubmit={handleCreateCollectionSubmit} className="v2-wiki-add-col-form">
          <input
            type="text"
            className="v2-wiki-add-col-input"
            placeholder="+ New collection…"
            value={newCollectionName}
            onChange={(e) => setNewCollectionName(e.target.value)}
            aria-label="New collection name"
          />
        </form>

        {/* Pages in Active Collection */}
        <div className="v2-wiki-section-header" style={{ marginTop: 12 }}>
          PAGES ({visibleDocuments.length})
        </div>
        <div className="v2-wiki-pages-list" aria-label="Document list">
          {visibleDocuments.length === 0 ? (
            <div className="v2-wiki-sidebar-empty">No pages found</div>
          ) : (
            visibleDocuments.map((doc) => {
              const isSelected = selectedDocumentId === doc.id
              return (
                <button
                  key={doc.id}
                  type="button"
                  className={`v2-wiki-page-item ${isSelected ? 'active' : ''}`}
                  onClick={() => selectDocument(doc.id)}
                >
                  <span className="v2-page-icon">
                    {doc.kind === 'plan' ? '🗺' : doc.kind === 'lesson' ? '💡' : doc.kind === 'documentation' ? '📐' : '📄'}
                  </span>
                  <span className="v2-page-title">{doc.title || 'Untitled'}</span>
                </button>
              )
            })
          )}
        </div>
      </aside>

      {/* Main Full-Page Canvas Area */}
      <section className="v2-wiki-canvas" aria-label="Document Canvas">
        {loadError && (
          <div className="v2-wiki-error-banner" role="alert">
            {loadError}
          </div>
        )}

        {!detail ? (
          <div className="v2-wiki-empty-canvas">
            <EmptyState
              title="No page selected"
              hint="Select a page from the sidebar or create a new untitled page to start writing."
              action={
                <button
                  type="button"
                  className="v2-chipbtn prime"
                  onClick={() => handleInstantCreatePage()}
                >
                  + New page
                </button>
              }
            />
          </div>
        ) : (
          <div className="v2-wiki-doc-container">
            {/* Metadata Pill Banner */}
            <div className="v2-wiki-by-bar">
              <span className="v2-wiki-by-pill">
                <i className="v2-pulse-dot" />
                <span>Last updated: {timeAgo(detail.document.updated_at)}</span>
                <span>·</span>
                <span>{wordCount} words</span>
                <span>·</span>
                <span>v{detail.latest_version.version_number}</span>
              </span>

              <div className="v2-wiki-header-actions">
                <SegmentedControl
                  value={editorMode}
                  onChange={(val) => setEditorMode(val as EditorMode)}
                  options={[
                    { value: 'write', label: 'Write' },
                    { value: 'preview', label: 'Preview' },
                  ]}
                  ariaLabel="Editor display mode"
                />

                <button
                  type="button"
                  className={`v2-chipbtn ${isDirty ? 'prime' : ''}`}
                  onClick={saveBody}
                  disabled={!isDirty || isSaving}
                  title="Save document body (⌘S)"
                >
                  {isSaving ? 'Saving…' : isDirty ? 'Save (⌘S)' : 'Saved ✓'}
                </button>

                <button
                  type="button"
                  className="v2-chipbtn"
                  onClick={() => {
                    setDetailsOpen(true)
                    void loadVersionsForDetails()
                  }}
                  title="View version history and connected graph links"
                >
                  Details & Links
                </button>
              </div>
            </div>

            {/* Notion-Style Big Inline Title */}
            <div className="v2-wiki-title-row">
              <input
                type="text"
                className="v2-wiki-title-input"
                value={titleDraft}
                onChange={(e) => setTitleDraft(e.target.value)}
                onBlur={commitTitle}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.currentTarget.blur()
                  }
                }}
                placeholder="Untitled"
                aria-label="Page title"
              />
            </div>

            {/* Metadata Chips Bar: Collection, Kind, Tags */}
            <div className="v2-wiki-meta-row">
              <div className="v2-meta-chip">
                <span className="v2-meta-label">Collection:</span>
                <select
                  className="v2-meta-select"
                  value={detail.document.collection_id || ''}
                  onChange={(e) => saveMetadata({ collection_id: e.target.value || null })}
                  aria-label="Collection"
                >
                  <option value="">(Uncategorized)</option>
                  {collections.map((col) => (
                    <option key={col.id} value={col.id}>
                      📘 {col.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="v2-meta-chip">
                <span className="v2-meta-label">Kind:</span>
                <select
                  className="v2-meta-select"
                  value={detail.document.kind}
                  onChange={(e) => saveMetadata({ kind: e.target.value as DocumentKind })}
                  aria-label="Kind"
                >
                  {KIND_OPTIONS.map((k) => (
                    <option key={k.value} value={k.value}>
                      {k.label}
                    </option>
                  ))}
                </select>
              </div>

              {/* Tags inline chips */}
              <div className="v2-wiki-tags-cluster">
                {detail.tags.map((t) => (
                  <span key={t.id} className="v2-wiki-tag-pill">
                    #{t.name}
                    <button
                      type="button"
                      className="v2-tag-remove-btn"
                      onClick={() => handleRemoveTag(t.name)}
                      aria-label={`Remove tag ${t.name}`}
                    >
                      ×
                    </button>
                  </span>
                ))}
                <input
                  type="text"
                  className="v2-wiki-inline-tag-input"
                  placeholder="+ Tag…"
                  value={newTagInput}
                  onChange={(e) => setNewTagInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault()
                      handleAddTag()
                    }
                  }}
                  onBlur={handleAddTag}
                  aria-label="Add tag"
                />
              </div>
            </div>

            {/* Document Body Editor / Preview */}
            <div className="v2-wiki-body-frame">
              {editorMode === 'write' ? (
                <textarea
                  className="v2-wiki-body-textarea"
                  value={bodyDraft}
                  onChange={(e) => setBodyDraft(e.target.value)}
                  placeholder="Start writing in Markdown, paste research takeaways, or press ⌘S to save…"
                  aria-label="Page body"
                />
              ) : (
                <div
                  className="v2-wiki-preview-content markdown-body"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(bodyDraft || '*Empty document*') }}
                />
              )}
            </div>
          </div>
        )}
      </section>

      {/* SlideOver for Document Details, Links & Version History */}
      {detailsOpen && (
        <SlideOver
          onClose={() => setDetailsOpen(false)}
          title={detail ? `Details: ${detail.document.title}` : 'Page Details'}
        >
          {detail && (
          <div className="v2-wiki-slideover-content">
            <div className="v2-slideover-section">
              <h4>METADATA</h4>
              <div className="v2-meta-grid">
                <div><strong>ID:</strong> <code>{detail.document.id}</code></div>
                <div><strong>Kind:</strong> {detail.document.kind}</div>
                <div><strong>Created:</strong> {new Date(detail.document.created_at).toLocaleString()}</div>
                <div><strong>Updated:</strong> {new Date(detail.document.updated_at).toLocaleString()}</div>
              </div>
            </div>

            <div className="v2-slideover-section">
              <h4>GRAPH CONNECTIONS ({detail.outbound_links.length + detail.backlinks.length})</h4>
              <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
                <select
                  className="v2-meta-select"
                  value={newLinkTargetId}
                  onChange={(e) => setNewLinkTargetId(e.target.value)}
                  aria-label="Target document for link"
                >
                  <option value="">Connect another document…</option>
                  {documents
                    .filter((d) => d.id !== selectedDocumentId)
                    .map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.title}
                      </option>
                    ))}
                </select>
                <button
                  type="button"
                  className="v2-chipbtn prime"
                  onClick={submitLink}
                  disabled={!newLinkTargetId}
                >
                  Link
                </button>
              </div>

              {detail.outbound_links.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <span style={{ fontSize: 11, color: 'var(--v2-t3)' }}>Outbound links:</span>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
                    {detail.outbound_links.map((link) => (
                      <button
                        key={link.id}
                        type="button"
                        className="v2-chipbtn"
                        onClick={() => selectDocument(link.target_id)}
                      >
                        → {link.target_id}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {detail.backlinks.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  <span style={{ fontSize: 11, color: 'var(--v2-t3)' }}>Referenced by:</span>
                  <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 4 }}>
                    {detail.backlinks.map((link) => (
                      <button
                        key={link.id}
                        type="button"
                        className="v2-chipbtn"
                        onClick={() => selectDocument(link.id)}
                      >
                        ← {link.title || link.id}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="v2-slideover-section">
              <h4>VERSION HISTORY ({versions.length})</h4>
              <div className="v2-versions-list">
                {versions.map((ver) => (
                  <div key={ver.id} className="v2-version-item">
                    <div style={{ fontWeight: 600, color: 'var(--v2-t1)' }}>
                      Version {ver.version_number}
                    </div>
                    <div style={{ fontSize: 11.5, color: 'var(--v2-t3)' }}>
                      {new Date(ver.created_at).toLocaleString()} · {ver.created_by}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
        </SlideOver>
      )}
    </div>
  )
}
