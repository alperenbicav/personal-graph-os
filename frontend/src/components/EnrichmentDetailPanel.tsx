import { useEffect, useState } from 'react'
import type { ResourceDetail } from '../types'

interface EnrichmentDetailPanelProps {
  resourceId: string
  onLoadDetail: (resourceId: string) => Promise<ResourceDetail>
  onGoToRelation?: (relation: ResourceDetail['relations'][number]) => void
}

function ListField({ label, values }: { label: string; values: string[] }) {
  if (values.length === 0) return null
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      <ul className="string-list enrichment-readonly-list">
        {values.map((value, index) => (
          <li key={index}>
            <span>{value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function TextField({ label, value }: { label: string; value: string | null }) {
  if (!value) return null
  return (
    <div className="field">
      <span className="field-label">{label}</span>
      <p className="field-value">{value}</p>
    </div>
  )
}

function EnrichmentPayloadSection({ detail }: { detail: ResourceDetail }) {
  const { enrichment } = detail
  if (!enrichment) {
    return <p className="view-empty">No enrichment yet.</p>
  }
  const payload = enrichment.payload as unknown as Record<string, unknown>

  return (
    <div className="enrichment-payload">
      <TextField label="Summary" value={(payload.summary as string) ?? null} />
      <ListField label="Key findings" values={(payload.key_findings as string[]) ?? []} />
      <ListField label="Capabilities" values={(payload.capabilities as string[]) ?? []} />
      <TextField
        label="Architecture"
        value={(payload.architecture_summary as string | null) ?? null}
      />
      <TextField label="Methodology" value={(payload.methodology as string | null) ?? null} />
      <ListField label="Limitations" values={(payload.limitations as string[]) ?? []} />
      <ListField label="Risks" values={(payload.risks as string[]) ?? []} />
      <TextField label="Applicability" value={(payload.applicability as string | null) ?? null} />
      <ListField label="Tech stack" values={(payload.tech_stack as string[]) ?? []} />
      <TextField label="License" value={(payload.license_name as string | null) ?? null} />
      <TextField label="Activity" value={(payload.activity_summary as string | null) ?? null} />
      <ListField label="Tags" values={[...enrichment.tags]} />

      {enrichment.authors.length > 0 && (
        <TextField label="Authors" value={enrichment.authors.join(', ')} />
      )}
      <TextField
        label="Published"
        value={enrichment.published_at ? enrichment.published_at.slice(0, 10) : null}
      />
      <TextField label="Abstract" value={enrichment.abstract} />

      {enrichment.cited_evidence.length > 0 && (
        <div className="field">
          <span className="field-label">Cited evidence</span>
          <ul className="string-list enrichment-readonly-list">
            {enrichment.cited_evidence.map((evidence, index) => (
              <li key={index}>
                <span>
                  {evidence.adapter_name} · {evidence.source_reference}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

export function EnrichmentDetailPanel({
  resourceId,
  onLoadDetail,
  onGoToRelation,
}: EnrichmentDetailPanelProps) {
  const [detail, setDetail] = useState<ResourceDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setError(null)
    onLoadDetail(resourceId)
      .then((response) => {
        if (!cancelled) setDetail(response)
      })
      .catch(() => {
        if (!cancelled) setError('Could not load enrichment detail')
      })
    return () => {
      cancelled = true
    }
  }, [resourceId, onLoadDetail])

  if (error) {
    return (
      <div className="enrichment-detail" role="alert">
        {error}
      </div>
    )
  }

  if (!detail) {
    return <p className="view-empty">Loading detail…</p>
  }

  return (
    <div className="enrichment-detail">
      <EnrichmentPayloadSection detail={detail} />

      {detail.relations.length > 0 && (
        <div className="field">
          <span className="field-label">Relations</span>
          <ul className="string-list enrichment-readonly-list">
            {detail.relations.map((relation) => (
              <li key={relation.edge_id}>
                <button
                  type="button"
                  className="enrichment-relation-row"
                  onClick={() => onGoToRelation?.(relation)}
                >
                  {relation.edge_type_name} → {relation.node_title}
                  <span className="node-row-lifecycle">{relation.target_domain}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {detail.related_documents.length > 0 && (
        <div className="field">
          <span className="field-label">Related Wiki pages</span>
          <ul className="string-list enrichment-readonly-list">
            {detail.related_documents.map((document) => (
              <li key={document.document_id}>
                <span>{document.title}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {detail.provenance && (
        <div className="field">
          <span className="field-label">Job provenance</span>
          <p className="field-value">
            {detail.provenance.source} · {detail.provenance.stage} · {detail.provenance.status} ·{' '}
            {detail.provenance.created_at.slice(0, 10)}
          </p>
        </div>
      )}
    </div>
  )
}
