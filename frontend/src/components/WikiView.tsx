import { useEffect, useMemo, useState } from 'react'
import type { CreateDocumentInput, UpdateDocumentMetadataPatch } from '../api/client'
import type { Collection, DocumentDetail, DocumentKind, DocumentVersion, Tag, WikiDocument } from '../types'
import { renderMarkdown } from '../lib/markdown'

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

type EditorMode = 'raw' | 'preview' | 'split'

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
  workspaceId: string
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
}: WikiViewProps) {
  const [documents, setDocuments] = useState<WikiDocument[]>([])
  const [collections, setCollections] = useState<Collection[]>([])
  const [tags, setTags] = useState<Tag[]>([])
  const [collectionFilter, setCollectionFilter] = useState<'all' | 'uncategorized' | string>('all')
  const [selectedDocumentId, setSelectedDocumentId] = useState<string | null>(null)
  const [detail, setDetail] = useState<DocumentDetail | null>(null)
  const [versions, setVersions] = useState<DocumentVersion[]>([])
  const [showVersions, setShowVersions] = useState(false)
  const [editorMode, setEditorMode] = useState<EditorMode>('split')
  const [bodyDraft, setBodyDraft] = useState('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [createDraft, setCreateDraft] = useState<CreatePageDraft>(EMPTY_DRAFT)
  const [createError, setCreateError] = useState<string | null>(null)
  const [linkTargetId, setLinkTargetId] = useState('')

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

  async function selectDocument(documentId: string) {
    setSelectedDocumentId(documentId)
    setShowVersions(false)
    setVersions([])
    try {
      const loaded = await onLoadDetail(documentId)
      setDetail(loaded)
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
    if (!selectedDocumentId) return
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

  async function loadVersionsOnce() {
    if (!selectedDocumentId) return
    setShowVersions((current) => !current)
    if (versions.length === 0) {
      const loaded = await onLoadVersions(selectedDocumentId)
      setVersions(loaded)
    }
  }

  async function saveMetadata(patch: UpdateDocumentMetadataPatch) {
    if (!selectedDocumentId) return
    try {
      await onUpdateMetadata(selectedDocumentId, patch)
      await Promise.all([refreshDocuments(), selectDocument(selectedDocumentId)])
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
    if (!selectedDocumentId || !linkTargetId.trim()) return
    try {
      await onAddDocumentLink(selectedDocumentId, linkTargetId.trim())
      setLinkTargetId('')
      await selectDocument(selectedDocumentId)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : String(error))
    }
  }

  return (
    <div className="wiki-view" aria-label="Wiki">
      <div className="wiki-tree">
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
            {visibleDocuments.map((document) => (
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
        )}
      </div>

      {!detail ? (
        <div className="wiki-editor view-empty">Select a page, or create a new one.</div>
      ) : (
        <div className="wiki-editor">
          <div className="wiki-editor-toolbar">
            <h2 className="wiki-document-title">{detail.document.title}</h2>
            <div className="wiki-editor-mode" role="group" aria-label="Editor mode">
              {(['raw', 'preview', 'split'] as EditorMode[]).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  className="node-row-label"
                  aria-current={editorMode === mode}
                  onClick={() => setEditorMode(mode)}
                >
                  {mode}
                </button>
              ))}
            </div>
            <button type="button" onClick={saveBody} disabled={!isDirty || isSaving}>
              {isSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
          <div className={`wiki-editor-panes wiki-editor-panes-${editorMode}`}>
            {editorMode !== 'preview' && (
              <textarea
                className="wiki-editor-raw"
                aria-label="Page body (Markdown)"
                value={bodyDraft}
                onChange={(event) => setBodyDraft(event.target.value)}
              />
            )}
            {editorMode !== 'raw' && (
              <div
                className="wiki-editor-preview"
                aria-label="Rendered preview"
                // Sanitized via DOMPurify immediately above; never rendered from unsanitized input.
                dangerouslySetInnerHTML={{ __html: renderMarkdown(bodyDraft) }}
              />
            )}
          </div>
        </div>
      )}

      {detail && (
        <div className="wiki-inspector">
          <label htmlFor="wiki-kind">Kind</label>
          <select
            id="wiki-kind"
            value={detail.document.kind}
            onChange={(event) => saveMetadata({ kind: event.target.value as DocumentKind })}
          >
            {KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>

          <label htmlFor="wiki-collection">Collection</label>
          <select
            id="wiki-collection"
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

          <label htmlFor="wiki-tags">Tags (comma-separated)</label>
          <input
            id="wiki-tags"
            type="text"
            list="wiki-known-tags"
            defaultValue={detail.tags.map((tag) => tag.name).join(', ')}
            onBlur={(event) => saveMetadata({ tag_names: parseTagNames(event.target.value) })}
          />

          <label>
            <input
              type="checkbox"
              checked={detail.document.is_archived}
              onChange={(event) => saveMetadata({ is_archived: event.target.checked })}
            />
            Archived
          </label>

          <button type="button" onClick={loadVersionsOnce}>
            {showVersions ? 'Hide versions' : `Versions (${detail.version_count})`}
          </button>
          {showVersions && (
            <ul className="wiki-version-list">
              {versions.map((version) => (
                <li key={version.id}>
                  v{version.version_number} · {new Date(version.created_at).toLocaleString()}
                </li>
              ))}
            </ul>
          )}

          <h3>Links</h3>
          <ul className="wiki-link-list">
            {detail.outbound_links.map((link) => (
              <li key={link.id}>
                {link.target_type}: {link.target_id}
              </li>
            ))}
          </ul>
          <div className="wiki-add-link">
            <input
              type="text"
              placeholder="Link to page id…"
              aria-label="Link to page id"
              value={linkTargetId}
              onChange={(event) => setLinkTargetId(event.target.value)}
            />
            <button type="button" onClick={submitLink} disabled={!linkTargetId.trim()}>
              Add link
            </button>
          </div>

          <h3>Backlinks</h3>
          {detail.backlinks.length === 0 ? (
            <p className="view-empty">No pages link here yet.</p>
          ) : (
            <ul className="wiki-link-list">
              {detail.backlinks.map((backlink) => (
                <li key={backlink.id}>
                  <button type="button" className="wiki-backlink" onClick={() => selectDocument(backlink.id)}>
                    {backlink.title}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {isCreateOpen && (
        <div className="wiki-create-modal" role="dialog" aria-label="Create page">
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
