import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ResourceReader } from './ResourceReader'
import type { Resource, ResourceDetail } from '../types'

const resource: Resource = {
  id: 'r1',
  workspace_id: 'ws-1',
  node_id: 'n1',
  kind: 'paper',
  canonical_identifier: 'arxiv:1',
  source_url: 'https://arxiv.org/abs/1',
  lifecycle_status: 'reading',
  next_action: null,
  next_action_dismissed: false,
  open_questions: [],
  takeaways: [],
  progress_percent: 40,
  review_at: null,
  last_activity_at: '2026-01-01T00:00:00Z',
  repository_label: null,
  title: 'Attention Is All You Need',
  body: '',
}

const detail: ResourceDetail = {
  resource,
  enrichment: {
    id: 'v1',
    profile_id: 'p1',
    version_number: 1,
    resource_kind: 'paper',
    payload: {
      summary: 'Transformers replace recurrence with attention.',
      key_findings: ['Self-attention scales better'],
      methodology: 'Encoder-decoder attention',
      limitations: ['Quadratic cost'],
      applicability: 'Sequence modeling',
    },
    tags: ['nlp'],
    evidence_content_hashes: [],
    cited_evidence: [],
    authors: ['Vaswani et al.'],
    published_at: '2017-06-12T00:00:00Z',
    abstract: 'We propose a new simple network architecture.',
    provider_name: 'fake',
    model_name: null,
    confidence: 0.9,
    created_by: 'agent',
    created_at: '2026-01-01T00:00:00Z',
  },
  relations: [],
  related_documents: [],
  provenance: null,
}

describe('ResourceReader', () => {
  it('renders the paper in the main reading column, not a cramped inspector list', async () => {
    render(
      <ResourceReader
        resource={resource}
        onClose={vi.fn()}
        onUpdate={vi.fn()}
        onLoadDetail={() => Promise.resolve(detail)}
        onGoToRelation={vi.fn()}
        onLoadContent={() => Promise.resolve(null)}
        onRefreshContent={vi.fn()}
        onLoadAttachments={() => Promise.resolve([])}
        onUploadAttachment={vi.fn()}
        onOpenAttachment={vi.fn()}
        onArchive={vi.fn()}
      />,
    )

    expect(await screen.findByRole('heading', { name: 'Attention Is All You Need' })).toBeInTheDocument()
    expect(screen.getByText('Transformers replace recurrence with attention.')).toBeInTheDocument()
    expect(screen.getByText('Self-attention scales better')).toBeInTheDocument()
    expect(screen.getByRole('article', { name: /paper reader/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open original source/i })).toHaveAttribute(
      'href',
      'https://arxiv.org/abs/1',
    )
  })

  it('fetches and shows full source text in the main column', async () => {
    const onRefreshContent = vi.fn().mockResolvedValue({
      resource_id: 'r1',
      body_markdown: '## Introduction\n\nThe whole paper.',
      content_hash: 'a'.repeat(64),
      adapter_name: 'html',
      retrieved_at: '2026-01-01T00:00:00Z',
    })
    render(
      <ResourceReader
        resource={resource}
        onClose={vi.fn()}
        onUpdate={vi.fn()}
        onLoadDetail={() => Promise.resolve(detail)}
        onGoToRelation={vi.fn()}
        onLoadContent={() => Promise.resolve(null)}
        onRefreshContent={onRefreshContent}
        onLoadAttachments={() => Promise.resolve([])}
        onUploadAttachment={vi.fn()}
        onOpenAttachment={vi.fn()}
        onArchive={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: /fetch full text/i }))
    await waitFor(() => expect(onRefreshContent).toHaveBeenCalledWith('r1'))
    expect(await screen.findByLabelText('Source text')).toHaveTextContent('The whole paper.')
  })

  it('embeds an attached PDF in the main reading area', async () => {
    const onOpenAttachment = vi.fn().mockResolvedValue('blob:pdf-1')
    render(
      <ResourceReader
        resource={resource}
        onClose={vi.fn()}
        onUpdate={vi.fn()}
        onLoadDetail={() => Promise.resolve(detail)}
        onGoToRelation={vi.fn()}
        onLoadContent={() => Promise.resolve(null)}
        onRefreshContent={vi.fn()}
        onLoadAttachments={() =>
          Promise.resolve([
            {
              id: 'a1',
              node_id: 'n1',
              file_name: 'paper.pdf',
              mime_type: 'application/pdf',
              size_bytes: 12,
              checksum_sha256: 'b'.repeat(64),
              storage_relative_path: 'paper.pdf',
              created_at: '2026-01-01T00:00:00Z',
            },
          ])
        }
        onUploadAttachment={vi.fn()}
        onOpenAttachment={onOpenAttachment}
        onArchive={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: /read pdf/i }))
    await waitFor(() => expect(onOpenAttachment).toHaveBeenCalledWith('a1'))
    expect(await screen.findByTitle('Attached PDF')).toHaveAttribute('src', 'blob:pdf-1')
  })

  it('returns to the library from the reader', async () => {
    const onClose = vi.fn()
    render(
      <ResourceReader
        resource={resource}
        onClose={onClose}
        onUpdate={vi.fn()}
        onLoadDetail={() => Promise.resolve(detail)}
        onGoToRelation={vi.fn()}
        onLoadContent={() => Promise.resolve(null)}
        onRefreshContent={vi.fn()}
        onLoadAttachments={() => Promise.resolve([])}
        onUploadAttachment={vi.fn()}
        onOpenAttachment={vi.fn()}
        onArchive={vi.fn()}
      />,
    )

    fireEvent.click(await screen.findByRole('button', { name: /library/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
