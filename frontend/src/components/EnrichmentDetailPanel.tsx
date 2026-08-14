import { useEffect, useState } from 'react'
import type { ResourceDetail } from '../types'

interface EnrichmentDetailPanelProps {
  resourceId: string
  onLoadDetail: (resourceId: string) => Promise<ResourceDetail>
  onGoToRelation?: (relation: ResourceDetail['relations'][number]) => void
}

function TextSection({ title, value }: { title: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <section className="enrichment-section">
      <h3 className="enrichment-section-title">{title}</h3>
      <p className="enrichment-paragraph">{value}</p>
    </section>
  )
}

function ListSection({ title, values }: { title: string; values: string[] | null | undefined }) {
  if (!values || values.length === 0) return null
  return (
    <section className="enrichment-section">
      <h3 className="enrichment-section-title">{title}</h3>
      <ul className="enrichment-findings">
        {values.map((value, index) => (
          <li key={index}>{value}</li>
        ))}
      </ul>
    </section>
  )
}

function EnrichmentPayloadSection({ detail }: { detail: ResourceDetail }) {
  const { enrichment } = detail
  if (!enrichment) {
    return <p className="view-empty">No enrichment yet.</p>
  }
  const payload = enrichment.payload as unknown as Record<string, unknown>
  const str = (key: string): string | null => {
    const value = payload[key]
    return typeof value === 'string' && value.trim() ? value : null
  }
  const list = (key: string): string[] =>
    Array.isArray(payload[key]) ? (payload[key] as string[]) : []
  const summary = str('summary')
  const keyFindings = list('key_findings')
  const capabilities = list('capabilities')

  const textSections: { title: string; value: string | null }[] = [
    { title: 'Research problem', value: str('research_problem') },
    { title: 'Motivation', value: str('motivation') },
    { title: 'Related work', value: str('related_work') },
    { title: 'Methodology', value: str('methodology') },
    { title: 'Datasets & experiments', value: str('datasets_and_experiments') },
    { title: 'Comparisons & ablations', value: str('comparisons_and_ablations') },
    { title: 'Theoretical contributions', value: str('theoretical_contributions') },
    { title: 'Practical contributions', value: str('practical_contributions') },
    { title: 'Assumptions', value: str('assumptions') },
    { title: 'Reproducibility', value: str('reproducibility') },
    { title: 'Figure & table notes', value: str('figure_table_notes') },
    { title: 'Interpretation', value: str('interpretation') },
    { title: 'Critical analysis', value: str('critical_analysis') },
    { title: 'Architecture', value: str('architecture_summary') },
    { title: 'Applicability', value: str('applicability') },
  ]
  const listSections: { title: string; values: string[] }[] = [
    { title: 'Key results', values: list('key_results') },
    { title: 'Open questions', values: list('open_questions') },
    { title: 'Key terms', values: list('key_terms') },
    { title: 'Limitations', values: list('limitations') },
    { title: 'Risks', values: list('risks') },
    { title: 'Tech stack', values: list('tech_stack') },
  ]

  return (
    <div className="enrichment-payload">
      <div className="enrichment-heading">
        {enrichment.tags.length > 0 && (
          <span className="enrichment-tags">
            {enrichment.tags.map((tag) => (
              <span key={tag} className="enrichment-tag">
                {tag}
              </span>
            ))}
          </span>
        )}
        {(enrichment.authors.length > 0 ||
          enrichment.published_at ||
          typeof payload.license_name === 'string') && (
          <p className="enrichment-byline">
            {enrichment.authors.length > 0 && <span>{enrichment.authors.join(', ')}</span>}
            {enrichment.published_at && (
              <span>Published {enrichment.published_at.slice(0, 10)}</span>
            )}
            {typeof payload.license_name === 'string' && <span>{payload.license_name}</span>}
          </p>
        )}
      </div>

      {summary && (
        <section className="enrichment-section">
          <h3 className="enrichment-section-title">Summary</h3>
          <p className="enrichment-paragraph">{summary}</p>
        </section>
      )}

      {keyFindings.length > 0 && (
        <section className="enrichment-section">
          <h3 className="enrichment-section-title">Key findings</h3>
          <ul className="enrichment-findings">
            {keyFindings.map((finding, index) => (
              <li key={index}>{finding}</li>
            ))}
          </ul>
        </section>
      )}

      {capabilities.length > 0 && (
        <section className="enrichment-section">
          <h3 className="enrichment-section-title">Capabilities</h3>
          <ul className="enrichment-findings">
            {capabilities.map((capability, index) => (
              <li key={index}>{capability}</li>
            ))}
          </ul>
        </section>
      )}

      {enrichment.abstract && (
        <section className="enrichment-section">
          <h3 className="enrichment-section-title">Abstract</h3>
          <p className="enrichment-paragraph">{enrichment.abstract}</p>
        </section>
      )}

      {textSections.map(
        (section) =>
          section.value && <TextSection key={section.title} title={section.title} value={section.value} />,
      )}
      {listSections.map(
        (section) =>
          section.values.length > 0 && (
            <ListSection key={section.title} title={section.title} values={section.values} />
          ),
      )}

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
