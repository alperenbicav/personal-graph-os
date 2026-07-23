import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import * as api from '../api/client'
import { FilesPanel } from './FilesPanel'
import type { Attachment, FileReference } from '../types'

vi.mock('../api/client')

const mockedApi = vi.mocked(api)

const attachment: Attachment = {
  id: 'att-1',
  node_id: 'node-1',
  file_name: 'notes.pdf',
  mime_type: 'application/pdf',
  size_bytes: 2048,
  checksum_sha256: 'a'.repeat(64),
  storage_relative_path: 'att-1',
  created_at: '2026-01-01T00:00:00Z',
}

const unverifiedReference: FileReference = {
  id: 'ref-1',
  node_id: 'node-1',
  machine_name: 'laptop',
  relative_path: 'repo/README.md',
  repository_name: 'apilex-agent',
  absolute_path: null,
  git_ref: null,
  last_verified_at: null,
  is_missing: false,
}

beforeEach(() => {
  vi.clearAllMocks()
  mockedApi.listAttachments.mockResolvedValue([])
  mockedApi.listFileReferences.mockResolvedValue([])
})

describe('FilesPanel', () => {
  it('shows a loading state, then attachments and references once loaded', async () => {
    mockedApi.listAttachments.mockResolvedValue([attachment])
    mockedApi.listFileReferences.mockResolvedValue([unverifiedReference])

    render(<FilesPanel nodeId="node-1" />)

    expect(screen.getByText(/loading files/i)).toBeInTheDocument()
    expect(await screen.findByText('notes.pdf')).toBeInTheDocument()
    expect(screen.getByText('repo/README.md')).toBeInTheDocument()
    expect(screen.getByText(/unverified/i)).toBeInTheDocument()
  })

  it('shows an empty state for no attachments and no references', async () => {
    render(<FilesPanel nodeId="node-1" />)
    expect(await screen.findByText(/no attachments yet/i)).toBeInTheDocument()
    expect(screen.getByText(/no references yet/i)).toBeInTheDocument()
  })

  it('shows a retry button when loading fails, and reloads on retry', async () => {
    mockedApi.listAttachments.mockRejectedValueOnce(new Error('network down'))
    render(<FilesPanel nodeId="node-1" />)

    expect(await screen.findByText('network down')).toBeInTheDocument()

    mockedApi.listAttachments.mockResolvedValue([attachment])
    fireEvent.click(screen.getByRole('button', { name: /retry/i }))

    expect(await screen.findByText('notes.pdf')).toBeInTheDocument()
  })

  it('uploads a file and appends it to the attachment list', async () => {
    mockedApi.uploadAttachment.mockResolvedValue(attachment)
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText(/no attachments yet/i)

    const file = new File(['hello'], 'notes.pdf', { type: 'application/pdf' })
    const input = document.getElementById('files-panel-upload-node-1') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    expect(await screen.findByText('notes.pdf')).toBeInTheDocument()
    expect(mockedApi.uploadAttachment).toHaveBeenCalledWith('node-1', file)
  })

  it('keeps the selected file and offers a retry when an upload is rejected', async () => {
    mockedApi.uploadAttachment.mockRejectedValueOnce(new Error('upload failed'))
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText(/no attachments yet/i)

    const file = new File(['hello'], 'notes.pdf', { type: 'application/pdf' })
    const input = document.getElementById('files-panel-upload-node-1') as HTMLInputElement
    fireEvent.change(input, { target: { files: [file] } })

    expect(await screen.findByText('upload failed')).toBeInTheDocument()
    expect(screen.getByText(/failed to upload notes.pdf/i)).toBeInTheDocument()

    mockedApi.uploadAttachment.mockResolvedValue(attachment)
    fireEvent.click(screen.getByRole('button', { name: /retry upload/i }))

    expect(await screen.findByText('notes.pdf')).toBeInTheDocument()
    expect(mockedApi.uploadAttachment).toHaveBeenCalledTimes(2)
    expect(mockedApi.uploadAttachment).toHaveBeenNthCalledWith(2, 'node-1', file)
  })

  it('downloads an attachment through the API client', async () => {
    mockedApi.listAttachments.mockResolvedValue([attachment])
    mockedApi.downloadAttachment.mockResolvedValue(undefined)
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText('notes.pdf')

    fireEvent.click(screen.getByRole('button', { name: /download/i }))

    await waitFor(() =>
      expect(mockedApi.downloadAttachment).toHaveBeenCalledWith('att-1', 'notes.pdf'),
    )
  })

  it('requires a confirmation click before deleting an attachment', async () => {
    mockedApi.listAttachments.mockResolvedValue([attachment])
    mockedApi.deleteAttachment.mockResolvedValue(undefined)
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText('notes.pdf')

    fireEvent.click(screen.getByRole('button', { name: /remove notes.pdf/i }))
    expect(mockedApi.deleteAttachment).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: /confirm delete/i }))
    await waitFor(() => expect(mockedApi.deleteAttachment).toHaveBeenCalledWith('att-1'))
    expect(await screen.findByText(/no attachments yet/i)).toBeInTheDocument()
  })

  it('cancels a pending delete confirmation without calling the API', async () => {
    mockedApi.listAttachments.mockResolvedValue([attachment])
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText('notes.pdf')

    fireEvent.click(screen.getByRole('button', { name: /remove notes.pdf/i }))
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))

    expect(mockedApi.deleteAttachment).not.toHaveBeenCalled()
    expect(screen.getByText('notes.pdf')).toBeInTheDocument()
  })

  it('creates a file reference from the form, including an optional git ref, and clears the draft on success', async () => {
    const created: FileReference = { ...unverifiedReference, id: 'ref-2', git_ref: 'main' }
    mockedApi.createFileReference.mockResolvedValue(created)
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText(/no references yet/i)

    fireEvent.change(screen.getByLabelText(/machine name/i), {
      target: { value: 'laptop' },
    })
    fireEvent.change(screen.getByLabelText(/relative path/i), {
      target: { value: 'repo/README.md' },
    })
    fireEvent.change(screen.getByLabelText(/git ref/i), { target: { value: 'main' } })
    fireEvent.click(screen.getByRole('button', { name: /add reference/i }))

    expect(await screen.findByText('repo/README.md')).toBeInTheDocument()
    expect(mockedApi.createFileReference).toHaveBeenCalledWith('node-1', {
      machine_name: 'laptop',
      relative_path: 'repo/README.md',
      repository_name: undefined,
      absolute_path: undefined,
      git_ref: 'main',
    })
    expect(screen.getByLabelText(/machine name/i)).toHaveValue('')
  })

  it('retains every reference draft field when creation is rejected', async () => {
    mockedApi.createFileReference.mockRejectedValue(new Error('conflict'))
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText(/no references yet/i)

    fireEvent.change(screen.getByLabelText(/machine name/i), { target: { value: 'laptop' } })
    fireEvent.change(screen.getByLabelText(/^relative path/i), {
      target: { value: 'repo/README.md' },
    })
    fireEvent.change(screen.getByLabelText(/repository name/i), {
      target: { value: 'apilex-agent' },
    })
    fireEvent.click(screen.getByRole('button', { name: /add reference/i }))

    expect(await screen.findByText('conflict')).toBeInTheDocument()
    expect(screen.getByLabelText(/machine name/i)).toHaveValue('laptop')
    expect(screen.getByLabelText(/^relative path/i)).toHaveValue('repo/README.md')
    expect(screen.getByLabelText(/repository name/i)).toHaveValue('apilex-agent')
  })

  it('disables verify for a reference with no absolute_path', async () => {
    mockedApi.listFileReferences.mockResolvedValue([unverifiedReference])
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText('repo/README.md')

    expect(screen.getByRole('button', { name: /^verify$/i })).toBeDisabled()
  })

  it('verifies a reference with an absolute_path and shows the updated status', async () => {
    const verifiable: FileReference = { ...unverifiedReference, absolute_path: '/tmp/README.md' }
    const verified: FileReference = {
      ...verifiable,
      is_missing: false,
      last_verified_at: '2026-07-22T10:00:00Z',
    }
    mockedApi.listFileReferences.mockResolvedValue([verifiable])
    mockedApi.verifyFileReference.mockResolvedValue(verified)
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText('repo/README.md')

    fireEvent.click(screen.getByRole('button', { name: /^verify$/i }))

    expect(await screen.findByText(/present/i)).toBeInTheDocument()
    expect(mockedApi.verifyFileReference).toHaveBeenCalledWith('ref-1')
  })

  it('deletes a file reference after confirmation', async () => {
    mockedApi.listFileReferences.mockResolvedValue([unverifiedReference])
    mockedApi.deleteFileReference.mockResolvedValue(undefined)
    render(<FilesPanel nodeId="node-1" />)
    await screen.findByText('repo/README.md')

    fireEvent.click(screen.getByRole('button', { name: /remove reference/i }))
    fireEvent.click(screen.getByRole('button', { name: /confirm delete/i }))

    await waitFor(() => expect(mockedApi.deleteFileReference).toHaveBeenCalledWith('ref-1'))
    expect(await screen.findByText(/no references yet/i)).toBeInTheDocument()
  })

  it('resets to loading state and refetches when the node id changes', async () => {
    mockedApi.listAttachments.mockResolvedValue([attachment])
    const { rerender } = render(<FilesPanel nodeId="node-1" />)
    await screen.findByText('notes.pdf')

    mockedApi.listAttachments.mockResolvedValue([])
    rerender(<FilesPanel nodeId="node-2" />)

    await waitFor(() => expect(mockedApi.listAttachments).toHaveBeenCalledWith('node-2'))
    expect(await screen.findByText(/no attachments yet/i)).toBeInTheDocument()
  })

  it('ignores a late-arriving response for a node that is no longer selected', async () => {
    const nodeAAttachment: Attachment = { ...attachment, id: 'att-a', file_name: 'a.pdf' }
    const nodeBAttachment: Attachment = { ...attachment, id: 'att-b', file_name: 'b.pdf' }
    let resolveNodeA: (value: Attachment[]) => void = () => {}
    const nodeADeferred = new Promise<Attachment[]>((resolve) => {
      resolveNodeA = resolve
    })
    mockedApi.listAttachments.mockImplementation((nodeId: string) =>
      nodeId === 'node-a' ? nodeADeferred : Promise.resolve([nodeBAttachment]),
    )

    const { rerender } = render(<FilesPanel nodeId="node-a" />)
    rerender(<FilesPanel nodeId="node-b" />)

    expect(await screen.findByText('b.pdf')).toBeInTheDocument()

    // Node A's request resolves only after node B is already showing; it must not overwrite B.
    resolveNodeA([nodeAAttachment])
    await Promise.resolve()

    expect(screen.getByText('b.pdf')).toBeInTheDocument()
    expect(screen.queryByText('a.pdf')).not.toBeInTheDocument()
  })
})
