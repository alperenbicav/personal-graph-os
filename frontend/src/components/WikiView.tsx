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

// Every REST mutation from this local single-user client is attributed to the same fixed
// actor the backend's `MutationContext.rest()` already assumes (`REST_ACTOR_NAME`); there is
// no per-user identity to collect in this app.
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

interface CreatePageDraft {
  title: string
  kind: DocumentKind
  collectionId: string
  tagNames: string
  bodyMarkdown: string
}

const EMPTY_DRAFT: CreatePageDraft = {
  title: '',
  kind: 'note',
  collectionId: '',
  tagNames: '',
  bodyMarkdown: '',
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
  const [tags, setTags] = useState<Tag[]>([])
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
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [createDraft, setCreateDraft] = useState<CreatePageDraft>(EMPTY_DRAFT)
  const [createError, setCreateError] = useState<string | null>(null)
  const [newLinkTargetId, setNewLinkTargetId] = useState('')
  const [isTagEditorOpen, setIsTagEditorOpen] = useState(false)
  const [collapsedCollections, setCollapsedCollections] = useState<Set<string>>(new Set())

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
    setIsTagEditorOpen(false)
    try {
      const loaded = await onLoadDetail(documentId)
      setDetail(loaded)
      setTitleDraft(loaded.document.title)
      setBodyDraft(loaded.latest_version.body_markdown)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  const visibleDocuments = useMemo(() => {
    if (collectionFilter === 'all') return documents
    if (collectionFilter === 'uncategorized') {
      return documents.filter((document) => document.collection_id === null)
    }
    return documents.filter((document) => document.collection_id === collectionFilter)
  }, [documents, collectionFilter])

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

  // Cmd/Ctrl+S saves the body like a document editor would.
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

  function removeTag(name: string) {
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

  async function submitCreate() {
    if (!createDraft.title.trim()) return
    setCreateError(null)
    try {
      const created = await onCreateDocument({
        title: createDraft.title.trim(),
        kind: createDraft.kind,
        source: REST_ACTOR_NAME,
        body_markdown: createDraft.bodyMarkdown,
        collection_id: createDraft.collectionId || null,
        tag_names: parseTagNames(createDraft.tagNames),
      })
      setIsCreateOpen(false)
      setCreateDraft(EMPTY_DRAFT)
      await refreshDocuments()
      await refreshTaxonomy()
      await selectDocument(created.id)
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : String(error))
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

  return (
    <div className="wiki-view" aria-label="Wiki">
      <aside className="wiki-tree" aria-label="Pages">
        <button type="button" className="wiki-new-page" onClick={() => setIsCreateOpen(true)}>
          + New page
        </button>
        <div className="wiki-collection-filters">
          <button
            type="button"
            className="node-row-label"
            aria-current={collectionFilter === 'all'}
            onClick={() => setCollectionFilter('all')}
          >
            All pages
          </button>
          <button
            type="button"
            className="node-row-label"
            aria-current={collectionFilter === 'uncategorized'}
            onClick={() => setCollectionFilter('uncategorized')}
          >
            Uncategorized
          </button>
          {collections.map((collection) => (
            <button
              key={collection.id}
              type="button"
              className="node-row-label"
              aria-current={collectionFilter === collection.id}
              onClick={() => setCollectionFilter(collection.id)}
            >
              {collection.name}
            </button>
          ))}
        </div>

        {loadError && (
          <p className="view-error" role="alert">
            {loadError}
          </p>
        )}
        {visibleDocuments.length === 0 ? (
          <p className="view-empty">No pages here yet.</p>
        ) : (
          <div className="list-view">
            {(() => {
              // Notion-style grouping (ST-10 review P3): pages live under their collection as a
              // collapsible section; 'all' shows every group, 'uncategorized' its own.
              type Group = { key: string; label: string; pages: typeof visibleDocuments }
              const groups: Group[] =
                collectionFilter === 'all'
                  ? [
                      ...collections.map((collection) => ({
                        key: `c-${collection.id}`,
                        label: collection.name,
                        pages: visibleDocuments.filter(
                          (document) => document.collection_id === collection.id,
                        ),
                      })),
                      {
                        key: 'c-uncategorized',
                        label: 'Uncategorized',
                        pages: visibleDocuments.filter(
                          (document) => document.collection_id === null,
                        ),
                      },
                    ].filter((group) => group.pages.length > 0)
                  : [{ key: collectionFilter, label: '', pages: visibleDocuments }]

              const toggleCollapsed = (key: string) =>
                setCollapsedCollections((current) => {
                  const next = new Set(current)
                  if (next.has(key)) next.delete(key)
                  else next.add(key)
                  return next
                })

              return groups.map((group) => {
                const isCollapsed = collapsedCollections.has(group.key)
                return (
                  <div key={group.key} className="wiki-page-group">
                    {group.label && (
                      <button
                        type="button"
                        className="wiki-group-header"
                        aria-expanded={!isCollapsed}
                        onClick={() => toggleCollapsed(group.key)}
                      >
                        <span className={`wiki-group-chevron${isCollapsed ? ' wiki-group-chevron-collapsed' : ''}`} aria-hidden>
                          ▾
                        </span>
                        {group.label}
                        <span className="wiki-group-count">{group.pages.length}</span>
                      </button>
                    )}
                    {!isCollapsed &&
                      group.pages.map((document) => (
                        <button
                          key={document.id}
                          type="button"
                          className="node-row wiki-page-row"
                          aria-current={document.id === selectedDocumentId}
                          onClick={() => selectDocument(document.id)}
                        >
                          <span className="node-row-title">{document.title}</span>
                          <span className="node-row-meta">
                            <span className="node-row-type">{document.kind}</span>
                          </span>
                        </button>
                      ))}
                  </div>
                )
              })
            })()}
          </div>
        )}
      </aside>

      <main className="wiki-page">
        {!detail ? (
          <div className="wiki-page-scroll">
            <EmptyState
              title="Nothing open"
              hint="Pick a page from the sidebar, or create a new one to start writing."
              action={
                <button type="button" className="wiki-new-page" onClick={() => setIsCreateOpen(true)}>
                  + New page
                </button>
              }
            />
          </div>
        ) : (
          <div className="wiki-page-scroll">
            <div className="reading-column wiki-document">
              <input
                className="wiki-title-input"
                aria-label="Page title"
                value={titleDraft}
                onChange={(event) => setTitleDraft(event.target.value)}
                onBlur={commitTitle}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') event.currentTarget.blur()
                }}
                placeholder="Untitled"
              />

              <div className="wiki-meta-row">
                <select
                  aria-label="Kind"
                  className="wiki-chip-select"
                  value={detail.document.kind}
                  onChange={(event) =>
                    saveMetadata({ kind: event.target.value as DocumentKind })
                  }
                >
                  {KIND_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>

                <select
                  aria-label="Collection"
                  className="wiki-chip-select"
                  value={detail.document.collection_id ?? ''}
                  onChange={(event) =>
                    event.target.value
                      ? saveMetadata({ collection_id: event.target.value })
                      : saveMetadata({ clear_collection: true })
                  }
                >
                  <option value="">Uncategorized</option>
                  {collections.map((collection) => (
                    <option key={collection.id} value={collection.id}>
                      {collection.name}
                    </option>
                  ))}
                </select>

                {detail.tags.map((tag) => (
                  <span key={tag.id} className="wiki-tag-pill">
                    {tag.name}
                    <button
                      type="button"
                      className="wiki-tag-remove"
                      aria-label={`Remove tag ${tag.name}`}
                      onClick={() => removeTag(tag.name)}
                    >
                      ×
                    </button>
                  </span>
                ))}

                {isTagEditorOpen ? (
                  <input
                    className="wiki-tag-add-input"
                    type="text"
                    list="wiki-known-tags"
                    autoFocus
                    aria-label="Tags (comma-separated)"
                    defaultValue={detail.tags.map((tag) => tag.name).join(', ')}
                    onBlur={(event) => {
                      void saveMetadata({ tag_names: parseTagNames(event.target.value) })
                      setIsTagEditorOpen(false)
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') event.currentTarget.blur()
                      if (event.key === 'Escape') setIsTagEditorOpen(false)
                    }}
                  />
                ) : (
                  <button
                    type="button"
                    className="wiki-tag-pill"
                    style={{ cursor: 'pointer' }}
                    onClick={() => setIsTagEditorOpen(true)}
                  >
                    + tags
                  </button>
                )}
                <datalist id="wiki-known-tags">
                  {tags.map((tag) => (
                    <option key={tag.id} value={tag.name} />
                  ))}
                </datalist>

                {detail.document.is_archived && (
                  <span className="pill pill-coral">Archived</span>
                )}

                <span className="wiki-updated">
                  updated{' '}
                  {new Date(detail.latest_version.created_at).toLocaleDateString(undefined, {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                  })}
                </span>
              </div>

              <div className="wiki-editor-bar">
                <SegmentedControl
                  ariaLabel="Editor mode"
                  options={[
                    { value: 'write', label: 'Write' },
                    { value: 'preview', label: 'Preview' },
                  ]}
                  value={editorMode}
                  onChange={setEditorMode}
                />
                {isDirty && <span className="wiki-dirty-note">unsaved</span>}
                <button type="button" onClick={saveBody} disabled={!isDirty || isSaving}>
                  {isSaving ? 'Saving…' : 'Save'}
                </button>
                <button
                  type="button"
                  className="wiki-options-button"
                  aria-label="Page details"
                  title="Versions, links, backlinks"
                  onClick={() => {
                    setDetailsOpen(true)
                    void loadVersionsForDetails()
                  }}
                >
                  ⋯
                </button>
              </div>

              {editorMode === 'write' ? (
                <textarea
                  className="wiki-body-editor"
                  aria-label="Page body (Markdown)"
                  value={bodyDraft}
                  onChange={(event) => setBodyDraft(event.target.value)}
                  placeholder="Start writing…"
                />
              ) : (
                <div
                  className="wiki-preview"
                  aria-label="Rendered preview"
                  // Sanitized by renderMarkdown (DOMPurify) before it ever reaches the DOM.
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(bodyDraft) }}
                />
              )}
            </div>
          </div>
        )}
      </main>

      {detailsOpen && detail && (
        <SlideOver title="Page details" onClose={() => setDetailsOpen(false)}>
          <section>
            <h3 className="slideover-section-title">Properties</h3>
            <label>
              <input
                type="checkbox"
                checked={detail.document.is_archived}
                onChange={(event) => saveMetadata({ is_archived: event.target.checked })}
              />{' '}
              Archived
            </label>
          </section>

          <section>
            <h3 className="slideover-section-title">Versions ({detail.version_count})</h3>
            {versions.length === 0 ? (
              <p className="view-empty">Loading versions…</p>
            ) : (
              <ul className="wiki-version-list">
                {versions.map((version) => (
                  <li key={version.id}>
                    v{version.version_number} · {new Date(version.created_at).toLocaleString()}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section>
            <h3 className="slideover-section-title">Outgoing links</h3>
            {detail.outbound_links.length === 0 ? (
              <p className="view-empty">No links yet.</p>
            ) : (
              <ul className="wiki-link-list">
                {detail.outbound_links.map((link) => (
                  <li key={link.id}>
                    {link.target_type}: {link.target_id}
                  </li>
                ))}
              </ul>
            )}
            <div className="wiki-add-link">
              <select
                aria-label="Link a page"
                value={newLinkTargetId}
                onChange={(event) => setNewLinkTargetId(event.target.value)}
              >
                <option value="">Link a page…</option>
                {documents
                  .filter((document) => document.id !== selectedDocumentId)
                  .map((document) => (
                    <option key={document.id} value={document.id}>
                      {document.title}
                    </option>
                  ))}
              </select>
              <button type="button" onClick={submitLink} disabled={!newLinkTargetId.trim()}>
                Add link
              </button>
            </div>
          </section>

          <section>
            <h3 className="slideover-section-title">Backlinks</h3>
            {detail.backlinks.length === 0 ? (
              <p className="view-empty">No pages link here yet.</p>
            ) : (
              <ul className="wiki-link-list">
                {detail.backlinks.map((backlink) => (
                  <li key={backlink.id}>
                    <button
                      type="button"
                      className="wiki-backlink"
                      onClick={() => selectDocument(backlink.id)}
                    >
                      {backlink.title}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </SlideOver>
      )}

      {isCreateOpen && (
        <div className="wiki-create-modal-backdrop" onClick={() => setIsCreateOpen(false)}>
        <div
          className="wiki-create-modal"
          role="dialog"
          aria-label="Create page"
          onClick={(event) => event.stopPropagation()}
        >
          <h2>Create page</h2>
          <label htmlFor="wiki-create-title">Title</label>
          <input
            id="wiki-create-title"
            type="text"
            value={createDraft.title}
            onChange={(event) => setCreateDraft((current) => ({ ...current, title: event.target.value }))}
          />

          <label htmlFor="wiki-create-kind">Kind</label>
          <select
            id="wiki-create-kind"
            value={createDraft.kind}
            onChange={(event) =>
              setCreateDraft((current) => ({ ...current, kind: event.target.value as DocumentKind }))
            }
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <label htmlFor="wiki-create-collection">Collection</label>
          <select
            id="wiki-create-collection"
            value={createDraft.collectionId}
            onChange={(event) =>
              setCreateDraft((current) => ({ ...current, collectionId: event.target.value }))
            }
          >
            <option value="">Uncategorized</option>
            {collections.map((collection) => (
              <option key={collection.id} value={collection.id}>
                {collection.name}
              </option>
            ))}
          </select>
          <NewCollectionButton onCreate={onCreateCollection} onCreated={refreshTaxonomy} />

          <label htmlFor="wiki-create-tags">Tags (comma-separated)</label>
          <input
            id="wiki-create-tags"
            type="text"
            list="wiki-known-tags"
            value={createDraft.tagNames}
            onChange={(event) =>
              setCreateDraft((current) => ({ ...current, tagNames: event.target.value }))
            }
          />
          <datalist id="wiki-known-tags">
            {tags.map((tag) => (
              <option key={tag.id} value={tag.name} />
            ))}
          </datalist>

          <label htmlFor="wiki-create-body">Body (Markdown)</label>
          <textarea
            id="wiki-create-body"
            value={createDraft.bodyMarkdown}
            onChange={(event) =>
              setCreateDraft((current) => ({ ...current, bodyMarkdown: event.target.value }))
            }
          />

          {createError && (
            <p className="view-error" role="alert">
              {createError}
            </p>
          )}
          <div className="wiki-create-actions">
            <button type="button" onClick={submitCreate} disabled={!createDraft.title.trim()}>
              Create
            </button>
            <button
              type="button"
              onClick={() => {
                setIsCreateOpen(false)
                setCreateDraft(EMPTY_DRAFT)
                setCreateError(null)
              }}
            >
              Cancel
            </button>
          </div>
        </div>
        </div>
      )}
    </div>
  )
}

function NewCollectionButton({
  onCreate,
  onCreated,
}: {
  onCreate: (name: string) => Promise<Collection>
  onCreated: () => Promise<void>
}) {
  const [isOpen, setIsOpen] = useState(false)
  const [name, setName] = useState('')

  if (!isOpen) {
    return (
      <button type="button" onClick={() => setIsOpen(true)}>
        + New collection
      </button>
    )
  }

  return (
    <div className="wiki-new-collection">
      <input
        type="text"
        aria-label="New collection name"
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <button
        type="button"
        disabled={!name.trim()}
        onClick={async () => {
          await onCreate(name.trim())
          setName('')
          setIsOpen(false)
          await onCreated()
        }}
      >
        Save
      </button>
    </div>
  )
}
