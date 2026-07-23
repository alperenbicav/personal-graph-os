import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { UpdatePrompt } from './components/UpdatePrompt.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* Mounted unconditionally, not gated behind unlock: the app shell/worker must be
        installable and cacheable before a user ever authenticates. */}
    <UpdatePrompt />
    <App />
  </StrictMode>,
)
