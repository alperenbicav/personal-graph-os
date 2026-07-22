import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { DiscoveryView } from './DiscoveryView'
import type { DiscoveryPreview, DiscoveryRun } from '../types'

function fillCandidates(text: string) {
  fireEvent.change(screen.getByLabelText('Candidates'), { target: { value: text } })
}

describe('DiscoveryView', () => {
  it('parses pasted lines and previews with the entered instruction', async () => {
    const preview: DiscoveryPreview = {
      instruction: 'import papers',
      sources_searched: [],
      filters_interpreted: {},
      candidates: [
        {
          candidate: { identifier: 'https://arxiv.org/abs/2401.00001', title: 'A paper' },
          canonical_identifier: 'arxiv:2401.00001',
          decision: 'create',
          reason: 'new canonical identity',
          existing_resource_id: null,
          duplicate_of_candidate_index: null,
        },
      ],
    }
    const onPreview = vi.fn().mockResolvedValue(preview)

    render(<DiscoveryView onPreview={onPreview} onApply={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('Instruction'), { target: { value: 'import papers' } })
    fillCandidates('https://arxiv.org/abs/2401.00001 | A paper')
    fireEvent.click(screen.getByRole('button', { name: /^preview$/i }))

    await waitFor(() =>
      expect(onPreview).toHaveBeenCalledWith('import papers', [
        { identifier: 'https://arxiv.org/abs/2401.00001', title: 'A paper', description: '' },
      ]),
    )
    expect(await screen.findByText('A paper')).toBeInTheDocument()
    expect(screen.getByText('create')).toBeInTheDocument()
  })

  it('disables preview until at least one candidate line is entered', () => {
    render(<DiscoveryView onPreview={vi.fn()} onApply={vi.fn()} />)
    expect(screen.getByRole('button', { name: /^preview$/i })).toBeDisabled()

    fillCandidates('https://arxiv.org/abs/2401.00001 | A paper')
    expect(screen.getByRole('button', { name: /^preview$/i })).not.toBeDisabled()
  })

  it('keeps confirm import disabled until a preview with a non-reject decision exists', async () => {
    const rejectOnlyPreview: DiscoveryPreview = {
      instruction: 'x',
      sources_searched: [],
      filters_interpreted: {},
      candidates: [
        {
          candidate: { identifier: 'not a url', title: 'Junk' },
          canonical_identifier: null,
          decision: 'reject',
          reason: 'could not canonicalize',
          existing_resource_id: null,
          duplicate_of_candidate_index: null,
        },
      ],
    }
    const onPreview = vi.fn().mockResolvedValue(rejectOnlyPreview)
    render(<DiscoveryView onPreview={onPreview} onApply={vi.fn()} />)

    expect(screen.getByRole('button', { name: /confirm import/i })).toBeDisabled()

    fillCandidates('not a url | Junk')
    fireEvent.click(screen.getByRole('button', { name: /^preview$/i }))

    await screen.findByText('reject')
    expect(screen.getByRole('button', { name: /confirm import/i })).toBeDisabled()
  })

  it('applies the batch and renders an imported/reused run summary', async () => {
    const preview: DiscoveryPreview = {
      instruction: 'import',
      sources_searched: [],
      filters_interpreted: {},
      candidates: [
        {
          candidate: { identifier: 'https://arxiv.org/abs/2401.00001', title: 'Paper' },
          canonical_identifier: 'arxiv:2401.00001',
          decision: 'create',
          reason: 'new canonical identity',
          existing_resource_id: null,
          duplicate_of_candidate_index: null,
        },
      ],
    }
    const run: DiscoveryRun = {
      id: 'run-1',
      workspace_id: 'ws-1',
      agent_identity: 'manual-import',
      instruction: 'import',
      sources_searched: [],
      filters_interpreted: {},
      candidates: [
        {
          raw_identifier: 'https://arxiv.org/abs/2401.00001',
          canonical_identifier: 'arxiv:2401.00001',
          title: 'Paper',
          kind: null,
          description: '',
          evidence: [],
          outcome: 'imported',
          reason: null,
          existing_resource_id: null,
          imported_node_id: 'node-1',
        },
      ],
      started_at: '2026-01-01T00:00:00Z',
      completed_at: '2026-01-01T00:00:01Z',
    }
    const onPreview = vi.fn().mockResolvedValue(preview)
    const onApply = vi.fn().mockResolvedValue(run)

    render(<DiscoveryView onPreview={onPreview} onApply={onApply} />)

    fireEvent.change(screen.getByLabelText('Instruction'), { target: { value: 'import' } })
    fillCandidates('https://arxiv.org/abs/2401.00001 | Paper')
    fireEvent.click(screen.getByRole('button', { name: /^preview$/i }))
    await screen.findByText('create')

    fireEvent.click(screen.getByRole('button', { name: /confirm import/i }))

    await waitFor(() => expect(onApply).toHaveBeenCalledWith('import', [
      { identifier: 'https://arxiv.org/abs/2401.00001', title: 'Paper', description: '' },
    ]))
    expect(await screen.findByText(/imported 1/i)).toBeInTheDocument()
    expect(screen.getByText('imported')).toBeInTheDocument()
  })

  it('shows the preview error message when the request fails', async () => {
    const onPreview = vi.fn().mockRejectedValue(new Error('workspace does not exist'))
    render(<DiscoveryView onPreview={onPreview} onApply={vi.fn()} />)

    fillCandidates('https://arxiv.org/abs/2401.00001 | Paper')
    fireEvent.click(screen.getByRole('button', { name: /^preview$/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('workspace does not exist')
  })
})
