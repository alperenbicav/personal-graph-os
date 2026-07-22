import { useState } from 'react'
import type { DiscoveryCandidateInput, DiscoveryPreview, DiscoveryRun } from '../types'
import { messageFor } from '../lib/errors'
import { parseDiscoveryCandidates } from '../lib/discovery'

interface DiscoveryViewProps {
  onPreview: (instruction: string, candidates: DiscoveryCandidateInput[]) => Promise<DiscoveryPreview>
  onApply: (instruction: string, candidates: DiscoveryCandidateInput[]) => Promise<DiscoveryRun>
}

const PLACEHOLDER =
  'One candidate per line: identifier | title | description (optional)\n' +
  'https://arxiv.org/abs/2401.00001 | A great paper\n' +
  'https://github.com/octocat/hello-world | An example repo'

export function DiscoveryView({ onPreview, onApply }: DiscoveryViewProps) {
  const [instruction, setInstruction] = useState('')
  const [raw, setRaw] = useState('')
  const [preview, setPreview] = useState<DiscoveryPreview | null>(null)
  const [run, setRun] = useState<DiscoveryRun | null>(null)
  const [isBusy, setIsBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const candidates = parseDiscoveryCandidates(raw)
  const canApply = Boolean(preview) && preview!.candidates.some((c) => c.decision !== 'reject')

  function resetOutcome() {
    setPreview(null)
    setRun(null)
  }

  async function runPreview() {
    if (candidates.length === 0) return
    setIsBusy(true)
    setError(null)
    try {
      setPreview(await onPreview(instruction.trim() || 'manual import', candidates))
      setRun(null)
    } catch (previewError) {
      setError(messageFor(previewError))
    } finally {
      setIsBusy(false)
    }
  }

  async function confirmApply() {
    if (candidates.length === 0) return
    setIsBusy(true)
    setError(null)
    try {
      setRun(await onApply(instruction.trim() || 'manual import', candidates))
      setPreview(null)
    } catch (applyError) {
      setError(messageFor(applyError))
    } finally {
      setIsBusy(false)
    }
  }

  return (
    <div className="discovery-view" aria-label="Discovery import">
      <div className="discovery-form">
        <label className="sr-only" htmlFor="discovery-instruction">
          Instruction
        </label>
        <input
          id="discovery-instruction"
          type="text"
          placeholder="What are you importing? (recorded with the run)"
          value={instruction}
          onChange={(event) => setInstruction(event.target.value)}
        />
        <label className="sr-only" htmlFor="discovery-candidates">
          Candidates
        </label>
        <textarea
          id="discovery-candidates"
          placeholder={PLACEHOLDER}
          value={raw}
          onChange={(event) => {
            setRaw(event.target.value)
            resetOutcome()
          }}
          rows={5}
        />
        <div className="discovery-actions">
          <button type="button" onClick={runPreview} disabled={isBusy || candidates.length === 0}>
            Preview
          </button>
          <button type="button" onClick={confirmApply} disabled={isBusy || !canApply}>
            Confirm import
          </button>
        </div>
      </div>

      {error && (
        <p className="view-empty" role="alert">
          {error}
        </p>
      )}

      {preview && (
        <div className="discovery-preview" aria-label="Discovery preview">
          {preview.candidates.map((candidatePreview, index) => (
            <div className="discovery-candidate-row" key={index}>
              <span className="node-row-title">{candidatePreview.candidate.title}</span>
              <span className={`discovery-decision discovery-decision-${candidatePreview.decision}`}>
                {candidatePreview.decision}
              </span>
              <span className="discovery-reason">{candidatePreview.reason}</span>
            </div>
          ))}
        </div>
      )}

      {run && (
        <div className="discovery-run-summary" aria-label="Discovery run summary">
          <p className="discovery-run-totals">
            Imported {run.candidates.filter((c) => c.outcome === 'imported').length} · Reused/
            skipped {run.candidates.filter((c) => c.outcome !== 'imported').length}
          </p>
          {run.candidates.map((candidate, index) => (
            <div className="discovery-candidate-row" key={index}>
              <span className="node-row-title">{candidate.title}</span>
              <span
                className={
                  candidate.outcome === 'imported'
                    ? 'discovery-decision discovery-decision-create'
                    : candidate.outcome === 'failed'
                      ? 'discovery-decision discovery-decision-reject'
                      : 'discovery-decision discovery-decision-reuse'
                }
              >
                {candidate.outcome}
              </span>
              {candidate.reason && <span className="discovery-reason">{candidate.reason}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
