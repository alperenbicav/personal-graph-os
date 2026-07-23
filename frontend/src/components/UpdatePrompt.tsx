import { useRegisterSW } from 'virtual:pwa-register/react'

// A deliberately manual reload, never `registerType: 'autoUpdate'`: silently activating a
// new service worker mid-session could serve a new HTML shell against still-loaded old JS
// (or vice versa). The user controls exactly when old and new asset graphs swap.
export function UpdatePrompt() {
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW()

  if (!needRefresh) return null

  return (
    <div className="update-prompt" role="status">
      <span>A new version is available.</span>
      <button type="button" onClick={() => updateServiceWorker(true)}>
        Reload
      </button>
      <button type="button" onClick={() => setNeedRefresh(false)}>
        Dismiss
      </button>
    </div>
  )
}
