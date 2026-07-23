import { useState, type FormEvent } from 'react'
import { ApiError, getWorkspace } from '../api/client'
import { setToken } from '../api/session'

interface UnlockScreenProps {
  onUnlocked: () => void
}

export function UnlockScreen({ onUnlocked }: UnlockScreenProps) {
  const [tokenInput, setTokenInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isVerifying, setIsVerifying] = useState(false)

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const candidate = tokenInput.trim()
    if (!candidate) {
      setError('Enter the access token.')
      return
    }

    setIsVerifying(true)
    setError(null)
    // Setting the token before verifying lets `getWorkspace()` actually send it; a wrong
    // token never lingers afterward because a 401 clears the session again automatically.
    setToken(candidate)
    try {
      await getWorkspace()
      onUnlocked()
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        setError('That token was rejected. Check it and try again.')
      } else {
        setError('Could not reach the Personal Graph OS server. Is it running?')
      }
    } finally {
      setIsVerifying(false)
    }
  }

  return (
    <div className="unlock-screen">
      <form className="unlock-screen__panel" onSubmit={handleSubmit}>
        <h1>Personal Graph OS</h1>
        <p>Enter the access token to unlock this workspace.</p>
        <input
          type="password"
          autoComplete="off"
          autoFocus
          aria-label="Access token"
          placeholder="Access token"
          value={tokenInput}
          onChange={(event) => setTokenInput(event.target.value)}
        />
        {error && (
          <p className="unlock-screen__error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={isVerifying}>
          {isVerifying ? 'Unlocking…' : 'Unlock'}
        </button>
      </form>
    </div>
  )
}
