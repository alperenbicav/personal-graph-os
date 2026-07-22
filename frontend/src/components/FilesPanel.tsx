import { useEffect, useState } from 'react'
import * as api from '../api/client'
import { messageFor } from '../lib/errors'
import type { Attachment, FileReference } from '../types'

interface FilesPanelProps {
  nodeId: string
}

function formatBytes(sizeBytes: number): string {
  if (sizeBytes < 1024) return `${sizeBytes} B`
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`
  return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
}

function referenceProvenanceLabel(reference: FileReference): string {
  const parts = [reference.machine_name]
  if (reference.repository_name) parts.push(reference.repository_name)
  if (reference.git_ref) parts.push(`@${reference.git_ref}`)
  return parts.join(' · ')
}

function referenceVerificationLabel(reference: FileReference): string {
  if (reference.last_verified_at === null) return 'Unverified'
  const verifiedAt = new Date(reference.last_verified_at).toLocaleString()
  return reference.is_missing ? `Missing (checked ${verifiedAt})` : `Present (checked ${verifiedAt})`
}

/** Selected-node Files section: managed `Attachment`s (copied, checksummed bytes) shown
 * separately from non-copying `FileReference`s (provenance pointers this app never reads or
 * writes). Self-fetching, like `SchemaEditor`. The parent `Inspector` remounts this whole
 * subtree on node switch (`key={node.id}`), and the load effect below also guards against a
 * stale in-flight response landing after `nodeId` has already changed, so per-node state can
 * never leak across selections either way. */
export function FilesPanel({ nodeId }: FilesPanelProps) {
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [fileReferences, setFileReferences] = useState<FileReference[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [pendingUpload, setPendingUpload] = useState<File | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(null)
  const [verifyingId, setVerifyingId] = useState<string | null>(null)

  const [machineName, setMachineName] = useState('')
  const [relativePath, setRelativePath] = useState('')
  const [repositoryName, setRepositoryName] = useState('')
  const [absolutePath, setAbsolutePath] = useState('')
  const [gitRef, setGitRef] = useState('')
  const [isCreatingReference, setIsCreatingReference] = useState(false)

  useEffect(() => {
    let isCurrent = true

    async function load() {
      setIsLoading(true)
      setLoadError(null)
      try {
        const [loadedAttachments, loadedReferences] = await Promise.all([
          api.listAttachments(nodeId),
          api.listFileReferences(nodeId),
        ])
        if (!isCurrent) return
        setAttachments(loadedAttachments)
        setFileReferences(loadedReferences)
      } catch (error) {
        if (!isCurrent) return
        setLoadError(messageFor(error))
      } finally {
        if (isCurrent) setIsLoading(false)
      }
    }

    load()
    return () => {
      isCurrent = false
    }
  }, [nodeId])

  function retryLoad() {
    setIsLoading(true)
    setLoadError(null)
    Promise.all([api.listAttachments(nodeId), api.listFileReferences(nodeId)])
      .then(([loadedAttachments, loadedReferences]) => {
        setAttachments(loadedAttachments)
        setFileReferences(loadedReferences)
      })
      .catch((error) => setLoadError(messageFor(error)))
      .finally(() => setIsLoading(false))
  }

  async function attemptUpload(file: File) {
    setIsUploading(true)
    setActionError(null)
    try {
      const attachment = await api.uploadAttachment(nodeId, file)
      setAttachments((current) => [...current, attachment])
      setPendingUpload(null)
    } catch (error) {
      // Keep the selection so the user can retry without re-choosing the file.
      setActionError(messageFor(error))
    } finally {
      setIsUploading(false)
    }
  }

  function handleFileSelected(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setPendingUpload(file)
    attemptUpload(file)
  }

  function retryUpload() {
    if (pendingUpload) attemptUpload(pendingUpload)
  }

  async function handleDownload(attachment: Attachment) {
    setActionError(null)
    try {
      await api.downloadAttachment(attachment.id, attachment.file_name)
    } catch (error) {
      setActionError(messageFor(error))
    }
  }

  async function handleDeleteAttachment(attachmentId: string) {
    setActionError(null)
    try {
      await api.deleteAttachment(attachmentId)
      setAttachments((current) => current.filter((a) => a.id !== attachmentId))
    } catch (error) {
      setActionError(messageFor(error))
    } finally {
      setConfirmingDeleteId(null)
    }
  }

  async function handleCreateReference() {
    if (!machineName.trim() || !relativePath.trim()) return
    setIsCreatingReference(true)
    setActionError(null)
    try {
      const reference = await api.createFileReference(nodeId, {
        machine_name: machineName.trim(),
        relative_path: relativePath.trim(),
        repository_name: repositoryName.trim() || undefined,
        absolute_path: absolutePath.trim() || undefined,
        git_ref: gitRef.trim() || undefined,
      })
      setFileReferences((current) => [...current, reference])
      setMachineName('')
      setRelativePath('')
      setRepositoryName('')
      setAbsolutePath('')
      setGitRef('')
    } catch (error) {
      // Keep every draft field so a rejected reference can be corrected, not retyped.
      setActionError(messageFor(error))
    } finally {
      setIsCreatingReference(false)
    }
  }

  async function handleDeleteReference(fileReferenceId: string) {
    setActionError(null)
    try {
      await api.deleteFileReference(fileReferenceId)
      setFileReferences((current) => current.filter((r) => r.id !== fileReferenceId))
    } catch (error) {
      setActionError(messageFor(error))
    } finally {
      setConfirmingDeleteId(null)
    }
  }

  async function handleVerifyReference(fileReferenceId: string) {
    setVerifyingId(fileReferenceId)
    setActionError(null)
    try {
      const verified = await api.verifyFileReference(fileReferenceId)
      setFileReferences((current) =>
        current.map((r) => (r.id === fileReferenceId ? verified : r)),
      )
    } catch (error) {
      setActionError(messageFor(error))
    } finally {
      setVerifyingId(null)
    }
  }

  if (isLoading) {
    return (
      <div className="files-panel">
        <span className="field-label">Files</span>
        <p className="files-panel-status">Loading files…</p>
      </div>
    )
  }

  if (loadError) {
    return (
      <div className="files-panel">
        <span className="field-label">Files</span>
        <p className="field-error">{loadError}</p>
        <button type="button" onClick={retryLoad}>
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="files-panel">
      <span className="field-label">Attachments</span>
      {actionError && <p className="field-error">{actionError}</p>}

      {attachments.length === 0 && <p className="files-panel-status">No attachments yet.</p>}
      <ul className="files-panel-list">
        {attachments.map((attachment) => (
          <li key={attachment.id} className="files-panel-row">
            <span className="files-panel-name">{attachment.file_name}</span>
            <span className="files-panel-meta">{formatBytes(attachment.size_bytes)}</span>
            <button type="button" onClick={() => handleDownload(attachment)}>
              Download
            </button>
            {confirmingDeleteId === attachment.id ? (
              <>
                <button type="button" onClick={() => handleDeleteAttachment(attachment.id)}>
                  Confirm delete
                </button>
                <button type="button" onClick={() => setConfirmingDeleteId(null)}>
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                aria-label={`Remove ${attachment.file_name}`}
                onClick={() => setConfirmingDeleteId(attachment.id)}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>

      <label className="files-panel-upload-label" htmlFor={`files-panel-upload-${nodeId}`}>
        {isUploading ? 'Uploading…' : 'Add file'}
      </label>
      <input
        id={`files-panel-upload-${nodeId}`}
        type="file"
        disabled={isUploading}
        onChange={handleFileSelected}
      />
      {pendingUpload && actionError && !isUploading && (
        <div className="files-panel-row">
          <span className="files-panel-meta">Failed to upload {pendingUpload.name}</span>
          <button type="button" onClick={retryUpload}>
            Retry upload
          </button>
        </div>
      )}

      <div className="divider" />

      <span className="field-label">Repository / file references</span>
      {fileReferences.length === 0 && <p className="files-panel-status">No references yet.</p>}
      <ul className="files-panel-list">
        {fileReferences.map((reference) => (
          <li key={reference.id} className="files-panel-row">
            <div>
              <div className="files-panel-name">{reference.relative_path}</div>
              <div className="files-panel-meta">{referenceProvenanceLabel(reference)}</div>
              <div className="files-panel-meta">{referenceVerificationLabel(reference)}</div>
            </div>
            <button
              type="button"
              disabled={reference.absolute_path === null || verifyingId === reference.id}
              onClick={() => handleVerifyReference(reference.id)}
            >
              {verifyingId === reference.id ? 'Verifying…' : 'Verify'}
            </button>
            {confirmingDeleteId === reference.id ? (
              <>
                <button type="button" onClick={() => handleDeleteReference(reference.id)}>
                  Confirm delete
                </button>
                <button type="button" onClick={() => setConfirmingDeleteId(null)}>
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                aria-label={`Remove reference ${reference.relative_path}`}
                onClick={() => setConfirmingDeleteId(reference.id)}
              >
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>

      <div className="files-panel-row">
        <label className="sr-only" htmlFor={`files-panel-machine-name-${nodeId}`}>
          Machine name
        </label>
        <input
          id={`files-panel-machine-name-${nodeId}`}
          type="text"
          placeholder="Machine name"
          value={machineName}
          onChange={(event) => setMachineName(event.target.value)}
        />
        <label className="sr-only" htmlFor={`files-panel-relative-path-${nodeId}`}>
          Relative path
        </label>
        <input
          id={`files-panel-relative-path-${nodeId}`}
          type="text"
          placeholder="Relative path"
          value={relativePath}
          onChange={(event) => setRelativePath(event.target.value)}
        />
        <label className="sr-only" htmlFor={`files-panel-repository-name-${nodeId}`}>
          Repository name (optional)
        </label>
        <input
          id={`files-panel-repository-name-${nodeId}`}
          type="text"
          placeholder="Repository (optional)"
          value={repositoryName}
          onChange={(event) => setRepositoryName(event.target.value)}
        />
        <label className="sr-only" htmlFor={`files-panel-absolute-path-${nodeId}`}>
          Absolute path (optional, for verification)
        </label>
        <input
          id={`files-panel-absolute-path-${nodeId}`}
          type="text"
          placeholder="Absolute path (optional, for verification)"
          value={absolutePath}
          onChange={(event) => setAbsolutePath(event.target.value)}
        />
        <label className="sr-only" htmlFor={`files-panel-git-ref-${nodeId}`}>
          Git ref (optional)
        </label>
        <input
          id={`files-panel-git-ref-${nodeId}`}
          type="text"
          placeholder="Git ref (optional)"
          value={gitRef}
          onChange={(event) => setGitRef(event.target.value)}
        />
        <button
          type="button"
          disabled={!machineName.trim() || !relativePath.trim() || isCreatingReference}
          onClick={handleCreateReference}
        >
          Add reference
        </button>
      </div>
    </div>
  )
}
