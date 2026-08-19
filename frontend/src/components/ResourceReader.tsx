import { useEffect, useMemo, useState, type ReactNode } from 'react'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import type { UpdateResourcePatch } from '../api/client'
import type {
  Attachment,
  RelatedNode,
  Resource,
  ResourceContent,
  ResourceDetail,
} from '../types'
import { ResearchDetailPanel } from './ResearchDetailPanel'

function renderMarkdown(bodyMarkdown: string): string {
  return DOMPurify.sanitize(marked.parse(bodyMarkdown, { async: false }) as string)
}

function formatDate(value: string | null | undefined): string | null {
  if (!value) return null
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value.slice(0, 10)
  return parsed.toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
}

function kindLabel(kind: Resource['kind']): string {
  if (kind === 'github_repository') return 'Repository'
  if (kind === 'paper') return 'Paper'
  if (kind === 'article') return 'Article'
  return kind
}

function lifecycleLabel(status: Resource['lifecycle_status']): string {
  return status.replaceAll('_', ' ')
}

function payloadString(payload: unknown, key: string): string | null {
  if (!payload || typeof payload !== 'object') return null
  const value = (payload as Record<string, unknown>)[key]
  return typeof value === 'string' && value.trim() ? value : null
}

function payloadList(payload: unknown, key: string): string[] {
  if (!payload || typeof payload !== 'object') return []
  const value = (payload as Record<string, unknown>)[key]
  return Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : []
}

function ReaderSection({
  title,
  children,
}: {
  title: string
  children: ReactNode
}) {
  return (
    <section className="reader-section">
      <h2 className="reader-section-title">{title}</h2>
      {children}
    </section>
  )
}

interface ResourceReaderProps {
  resource: Resource
  onClose: () => void
  onUpdate: (resourceId: string, patch: UpdateResourcePatch) => Promise<boolean>
  onLoadDetail: (resourceId: string) => Promise<ResourceDetail>
  onGoToRelation: (relation: RelatedNode) => void
  onLoadContent: (resourceId: string) => Promise<ResourceContent | null>
  onRefreshContent: (resourceId: string) => Promise<ResourceContent>
  onLoadAttachments: (nodeId: string) => Promise<Attachment[]>
  onUploadAttachment: (nodeId: string, file: File) => Promise<Attachment>
  onOpenAttachment: (attachmentId: string) => Promise<string>
  onArchive: (resourceId: string) => void
}

export function ResourceReader({
  resource,
  onClose,
  onUpdate,
  onLoadDetail,
  onGoToRelation,
  onLoadContent,
  onRefreshContent,
  onLoadAttachments,
  onUploadAttachment,
  onOpenAttachment,
  onArchive,
}: ResourceReaderProps) {
  const [detail, setDetail] = useState<ResourceDetail | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [content, setContent] = useState<ResourceContent | null>(null)
  const [contentError, setContentError] = useState<string | null>(null)
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setDetailError(null)
    onLoadDetail(resource.id)
      .then((loaded) => {
        if (!cancelled) setDetail(loaded)
      })
      .catch(() => {
        if (!cancelled) setDetailError('Could not load enrichment detail')
      })
    return () => {
      cancelled = true
    }
  }, [resource.id, onLoadDetail])

  useEffect(() => {
    let cancelled = false
    setContent(null)
    setContentError(null)
    onLoadContent(resource.id)
      .then((loaded) => {
        if (!cancelled) setContent(loaded)
      })
      .catch(() => {
        if (!cancelled) setContentError('Could not load source text')
      })
    return () => {
      cancelled = true
    }
  }, [resource.id, onLoadContent])

  useEffect(() => {
    let cancelled = false
    onLoadAttachments(resource.node_id)
      .then((loaded) => {
        if (!cancelled) setAttachments(loaded)
      })
      .catch(() => {
        if (!cancelled) setAttachments([])
      })
    return () => {
      cancelled = true
    }
  }, [resource.node_id, onLoadAttachments])

  useEffect(() => {
    return () => {
      if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    }
  }, [pdfUrl])

  const enrichment = detail?.enrichment ?? null
  const payload = enrichment?.payload ?? null
  const authors = enrichment?.authors ?? []
  const published = formatDate(enrichment?.published_at)
  const tags = enrichment?.tags ?? []
  const pdfAttachments = useMemo(
    () => attachments.filter((attachment) => attachment.mime_type === 'application/pdf'),
    [attachments],
  )

  async function fetchFullText() {
    setIsRefreshing(true)
    setContentError(null)
    try {
      setContent(await onRefreshContent(resource.id))
    } catch (error) {
      setContentError(error instanceof Error ? error.message : String(error))
    } finally {
      setIsRefreshing(false)
    }
  }

  async function openPdf(attachmentId: string) {
    if (pdfUrl) URL.revokeObjectURL(pdfUrl)
    setPdfUrl(await onOpenAttachment(attachmentId))
  }

  async function uploadPdf(file: File) {
    setIsUploading(true)
    try {
      const uploaded = await onUploadAttachment(resource.node_id, file)
      setAttachments((current) => [...current, uploaded])
      if (uploaded.mime_type === 'application/pdf') await openPdf(uploaded.id)
    } finally {
      setIsUploading(false)
    }
  }

  const summary = payloadString(payload, 'summary')
  const methodology = payloadString(payload, 'methodology')
  const applicability = payloadString(payload, 'applicability')
  const architecture = payloadString(payload, 'architecture_summary')
  const licenseName = payloadString(payload, 'license_name')
  const activity = payloadString(payload, 'activity_summary')
  const keyFindings = payloadList(payload, 'key_findings')
  const limitations = payloadList(payload, 'limitations')
  const capabilities = payloadList(payload, 'capabilities')
  const risks = payloadList(payload, 'risks')
  const techStack = payloadList(payload, 'tech_stack')

  return (
    <article className="reader" aria-label={`${kindLabel(resource.kind)} reader`}>
      <div className="reader-main">
        <header className="reader-header">
          <button type="button" className="reader-back" onClick={onClose}>
            ← Library
          </button>
          <div className="reader-chips">
            <span className="reader-chip">{kindLabel(resource.kind)}</span>
            <span className="reader-chip reader-chip-status">{lifecycleLabel(resource.lifecycle_status)}</span>
            {resource.progress_percent !== null && (
              <span className="reader-chip">{resource.progress_percent}% read</span>
            )}
          </div>
          <h1 className="reader-title">{resource.title}</h1>
          <p className="reader-byline">
            {authors.length > 0 && <span>{authors.join(', ')}</span>}
            {authors.length > 0 && published && <span aria-hidden="true"> · </span>}
            {published && <time dateTime={enrichment?.published_at ?? undefined}>{published}</time>}
          </p>
          <div className="reader-actions">
            {resource.source_url && (
              <a className="reader-source" href={resource.source_url} target="_blank" rel="noreferrer">
                Open original source
              </a>
            )}
            {pdfAttachments.map((attachment) => (
              <button
                key={attachment.id}
                type="button"
                className="reader-source-button"
                onClick={() => openPdf(attachment.id)}
              >
                Read PDF · {attachment.file_name}
              </button>
            ))}
          </div>
          {tags.length > 0 && (
            <ul className="reader-tags">
              {tags.map((tag) => (
                <li key={tag}>{tag}</li>
              ))}
            </ul>
          )}
          {resource.progress_percent !== null && (
            <div
              className="reader-progress"
              role="progressbar"
              aria-valuenow={resource.progress_percent}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span style={{ width: `${resource.progress_percent}%` }} />
            </div>
          )}
        </header>

        <div className="reader-article">
          {detailError && (
            <p className="view-error" role="alert">
              {detailError}
            </p>
          )}
          {!detail && !detailError && <p className="view-empty">Loading the reading view…</p>}

          {summary && (
            <ReaderSection title="Summary">
              <p className="reader-lead">{summary}</p>
            </ReaderSection>
          )}
          {keyFindings.length > 0 && (
            <ReaderSection title="Key findings">
              <ul className="reader-list">
                {keyFindings.map((finding) => (
                  <li key={finding}>{finding}</li>
                ))}
              </ul>
            </ReaderSection>
          )}
          {methodology && (
            <ReaderSection title="Methodology">
              <p>{methodology}</p>
            </ReaderSection>
          )}
          {limitations.length > 0 && (
            <ReaderSection title="Limitations">
              <ul className="reader-list">
                {limitations.map((limitation) => (
                  <li key={limitation}>{limitation}</li>
                ))}
              </ul>
            </ReaderSection>
          )}
          {capabilities.length > 0 && (
            <ReaderSection title="Capabilities">
              <ul className="reader-list">
                {capabilities.map((capability) => (
                  <li key={capability}>{capability}</li>
                ))}
              </ul>
            </ReaderSection>
          )}
          {architecture && (
            <ReaderSection title="Architecture">
              <p>{architecture}</p>
            </ReaderSection>
          )}
          {techStack.length > 0 && (
            <ReaderSection title="Tech stack">
              <ul className="reader-tags">
                {techStack.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </ReaderSection>
          )}
          {licenseName && (
            <ReaderSection title="License">
              <p>{licenseName}</p>
            </ReaderSection>
          )}
          {activity && (
            <ReaderSection title="Activity">
              <p>{activity}</p>
            </ReaderSection>
          )}
          {risks.length > 0 && (
            <ReaderSection title="Risks">
              <ul className="reader-list">
                {risks.map((risk) => (
                  <li key={risk}>{risk}</li>
                ))}
              </ul>
            </ReaderSection>
          )}
          {applicability && (
            <ReaderSection title="Applicability">
              <p>{applicability}</p>
            </ReaderSection>
          )}
          {enrichment?.abstract && (
            <ReaderSection title="Abstract">
              <p>{enrichment.abstract}</p>
            </ReaderSection>
          )}

          <ReaderSection title="Full text">
            {content ? (
              <div
                className="prose"
                aria-label="Source text"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(content.body_markdown) }}
              />
            ) : (
              <div className="reader-empty-card">
                <p>
                  The full source is not stored yet. Fetch it from the original URL, or attach the
                  PDF to read it here.
                </p>
                <button type="button" className="reader-primary" onClick={fetchFullText} disabled={isRefreshing}>
                  {isRefreshing ? 'Fetching…' : 'Fetch full text'}
                </button>
                {contentError && (
                  <p className="view-error" role="alert">
                    {contentError}
                  </p>
                )}
              </div>
            )}
          </ReaderSection>

          {pdfUrl && (
            <ReaderSection title="PDF">
              <iframe className="reader-pdf" title="Attached PDF" src={pdfUrl} />
            </ReaderSection>
          )}

          {enrichment && enrichment.cited_evidence.length > 0 && (
            <ReaderSection title="Cited evidence">
              <ul className="reader-evidence">
                {enrichment.cited_evidence.map((evidence) => (
                  <li key={`${evidence.adapter_name}-${evidence.source_reference}`}>
                    {evidence.adapter_name} · {evidence.source_reference}
                  </li>
                ))}
              </ul>
            </ReaderSection>
          )}
        </div>
      </div>

      <aside className="reader-rail" aria-label="Reading notes">
        <ResearchDetailPanel
          resource={resource}
          onUpdate={(patch) => onUpdate(resource.id, patch)}
        />

        <div className="field">
          <span className="field-label">PDF attachment</span>
          <label className="reader-upload">
            <input
              type="file"
              accept="application/pdf"
              aria-label="Attach PDF"
              disabled={isUploading}
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) uploadPdf(file)
                event.target.value = ''
              }}
            />
            {isUploading ? 'Uploading…' : 'Attach a PDF to read here'}
          </label>
        </div>

        {detail && detail.relations.length > 0 && (
          <div className="field">
            <span className="field-label">Related</span>
            <ul className="reader-related">
              {detail.relations.map((relation) => (
                <li key={relation.edge_id}>
                  <button type="button" onClick={() => onGoToRelation(relation)}>
                    {relation.edge_type_name} → {relation.node_title}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {detail && detail.related_documents.length > 0 && (
          <div className="field">
            <span className="field-label">Related Wiki pages</span>
            <ul className="reader-related">
              {detail.related_documents.map((document) => (
                <li key={document.document_id}>{document.title}</li>
              ))}
            </ul>
          </div>
        )}

        {detail?.provenance && (
          <p className="reader-provenance">
            {detail.provenance.source} · {detail.provenance.stage} · {detail.provenance.status}
          </p>
        )}

        <button type="button" className="archive-button" onClick={() => onArchive(resource.id)}>
          Archive
        </button>
      </aside>
    </article>
  )
}
