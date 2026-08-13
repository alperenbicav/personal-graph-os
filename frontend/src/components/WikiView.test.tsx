import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { WikiView } from './WikiView'
import type { DocumentDetail, WikiDocument } from '../types'

function makeDocument(overrides: Partial<WikiDocument> = {}): WikiDocument {
  return {
    id: 'doc-1',
    workspace_id: 'ws-1',
    kind: 'note',
    title: 'My first page',
    collection_id: null,
    tag_ids: [],
    is_archived: false,
    source: 'manual',
    source_reference: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function makeDetail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    document: makeDocument(),
    latest_version: {
      id: 'v-1',
      document_id: 'doc-1',
      version_number: 1,
      body_markdown: '# hello',
      created_by: 'manual',
      created_at: '2026-01-01T00:00:00Z',
    },
    collection: null,
    tags: [],
    outbound_links: [],
    backlinks: [],
    version_count: 1,
    ...overrides,
  }
}

function baseProps() {
  return {
    workspaceId: 'ws-1',
    onLoadDocuments: vi.fn().mockResolvedValue([makeDocument()]),
    onLoadDetail: vi.fn().mockResolvedValue(makeDetail()),
    onLoadVersions: vi.fn().mockResolvedValue([]),
    onCreateDocument: vi.fn().mockResolvedValue(makeDocument({ id: 'doc-2', title: 'New page' })),
    onUpdateMetadata: vi.fn().mockResolvedValue(makeDocument()),
    onEditBody: vi.fn().mockResolvedValue({
      id: 'v-2',
      document_id: 'doc-1',
      version_number: 2,
      body_markdown: '# updated',
      created_by: 'human/local-user/rest',
      created_at: '2026-01-01T00:01:00Z',
    }),
    onLoadCollections: vi.fn().mockResolvedValue([]),
    onCreateCollection: vi.fn(),
    onLoadTags: vi.fn().mockResolvedValue([]),
    onAddDocumentLink: vi.fn().mockResolvedValue(undefined),
  }
}

describe('WikiView', () => {
  it('lists documents and loads detail on selection', async () => {
    const props = baseProps()
    render(<WikiView {...props} />)

    const row = await screen.findByText('My first page')
    fireEvent.click(row)

    await waitFor(() => expect(props.onLoadDetail).toHaveBeenCalledWith('doc-1'))
    expect(await screen.findByRole('heading', { name: 'My first page' })).toBeInTheDocument()
  })

  it('creates a page through the create-page modal', async () => {
    const props = baseProps()
    render(<WikiView {...props} />)

    fireEvent.click(await screen.findByRole('button', { name: '+ New page' }))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'New page' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() =>
      expect(props.onCreateDocument).toHaveBeenCalledWith(
        expect.objectContaining({ title: 'New page', kind: 'note' }),
      ),
    )
  })

  it('disables create until a title is entered', async () => {
    render(<WikiView {...baseProps()} />)

    fireEvent.click(await screen.findByRole('button', { name: '+ New page' }))

    expect(screen.getByRole('button', { name: 'Create' })).toBeDisabled()
  })

  it('saves an edited body only once it differs from the loaded version', async () => {
    const props = baseProps()
    render(<WikiView {...props} />)

    fireEvent.click(await screen.findByText('My first page'))
    await screen.findByRole('heading', { name: 'My first page' })

    const editor = screen.getByLabelText('Page body (Markdown)') as HTMLTextAreaElement
    expect(screen.getByRole('button', { name: /^save$/i })).toBeDisabled()

    fireEvent.change(editor, { target: { value: '# changed' } })
    expect(screen.getByRole('button', { name: /^save$/i })).not.toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: /^save$/i }))

    await waitFor(() => expect(props.onEditBody).toHaveBeenCalledWith('doc-1', '# changed'))
  })

  it('navigates to a backlinked page on click', async () => {
    const props = baseProps()
    props.onLoadDetail = vi
      .fn()
      .mockResolvedValueOnce(
        makeDetail({
          backlinks: [makeDocument({ id: 'doc-3', title: 'Referring page' })],
        }),
      )
      .mockResolvedValueOnce(makeDetail({ document: makeDocument({ id: 'doc-3', title: 'Referring page' }) }))
    render(<WikiView {...props} />)

    fireEvent.click(await screen.findByText('My first page'))
    const backlink = await screen.findByRole('button', { name: 'Referring page' })
    fireEvent.click(backlink)

    await waitFor(() => expect(props.onLoadDetail).toHaveBeenCalledWith('doc-3'))
  })
})
