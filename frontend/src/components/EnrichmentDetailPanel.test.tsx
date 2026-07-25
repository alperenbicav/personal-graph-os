import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { EnrichmentDetailPanel } from './EnrichmentDetailPanel'
import type { ResourceDetail } from '../types'

const baseResource: ResourceDetail['resource'] = {
  id: 'r1',
  workspace_id: 'ws-1',
  node_id: 'n1',
  kind: 'paper',
  canonical_identifier: 'arxiv:1',
  source_url: null,
  lifecycle_status: 'inbox',
  next_action: null,
  next_action_dismissed: false,
  open_questions: [],
  takeaways: [],
  progress_percent: null,
  review_at: null,
  last_activity_at: '2026-01-01T00:00:00Z',
  repository_label: null,
  title: 'A paper',
  body: '',
}

const detail: ResourceDetail = {
  resource: baseResource,
  enrichment: {
    id: 'v1',
    profile_id: 'p1',
    version_number: 1,
    resource_kind: 'paper',
    payload: {
      summary: 'A summary',
      key_findings: ['Finding one'],
      methodology: 'Method',
      limitations: ['Limitation one'],
      applicability: 'Applies here',
    },
    tags: ['nlp'],
    evidence_content_hashes: [],
    cited_evidence: [
      {
        adapter_name: 'article_adapter',
        source_reference: 'https://example.com',
        content_hash: 'a'.repeat(64),
        retrieved_at: '2026-01-01T00:00:00Z',
      },
    ],
    authors: ['Jane Doe'],
    published_at: '2025-06-01T00:00:00Z',
    abstract: 'An abstract',
    provider_name: 'fake',
    model_name: null,
    confidence: 0.9,
    created_by: 'agent',
    created_at: '2026-01-01T00:00:00Z',
  },
  relations: [
    {
      edge_id: 'e1',
      direction: 'outgoing',
      edge_type_name: 'relates to',
      node_id: 'n2',
      node_title: 'Related repo',
      target_domain: 'resource',
      target_entity_id: 'r2',
      resource_kind: 'github_repository',
    },
  ],
  related_documents: [{ document_id: 'd1', title: 'Notes', kind: 'note' }],
  provenance: {
    ingestion_job_id: 'j1',
    source: 'url',
    stage: 'committed',
    status: 'succeeded',
    created_at: '2026-01-02T00:00:00Z',
  },
}

describe('EnrichmentDetailPanel', () => {
  it('shows a loading message before the detail resolves', () => {
    render(
      <EnrichmentDetailPanel
        resourceId="r1"
        onLoadDetail={() => new Promise(() => {})}
      />,
    )
    expect(screen.getByText(/loading detail/i)).toBeInTheDocument()
  })

  it('renders enrichment payload, relations, related documents, and provenance', async () => {
    render(<EnrichmentDetailPanel resourceId="r1" onLoadDetail={() => Promise.resolve(detail)} />)

    await waitFor(() => expect(screen.getByText('A summary')).toBeInTheDocument())
    expect(screen.getByText('Finding one')).toBeInTheDocument()
    expect(screen.getByText('Jane Doe')).toBeInTheDocument()
    expect(screen.getByText(/relates to → Related repo/)).toBeInTheDocument()
    expect(screen.getByText('Notes')).toBeInTheDocument()
    expect(screen.getByText(/url · committed · succeeded/)).toBeInTheDocument()
  })

  it('shows an error message when loading the detail fails', async () => {
    render(
      <EnrichmentDetailPanel resourceId="r1" onLoadDetail={() => Promise.reject(new Error('boom'))} />,
    )
    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument())
  })

  it('calls onGoToRelation when a relation row is clicked', async () => {
    const onGoToRelation = vi.fn()
    render(
      <EnrichmentDetailPanel
        resourceId="r1"
        onLoadDetail={() => Promise.resolve(detail)}
        onGoToRelation={onGoToRelation}
      />,
    )
    await waitFor(() => expect(screen.getByText(/relates to → Related repo/)).toBeInTheDocument())
    screen.getByText(/relates to → Related repo/).closest('button')?.click()
    expect(onGoToRelation).toHaveBeenCalledWith(detail.relations[0])
  })
})
