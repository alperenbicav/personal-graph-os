import type { ReactNode } from 'react'

export type PillTone = 'neutral' | 'teal' | 'brass' | 'coral' | 'moss' | 'violet'

const TONE_CLASS: Record<PillTone, string> = {
  neutral: 'pill-neutral',
  teal: 'pill-teal',
  brass: 'pill-brass',
  coral: 'pill-coral',
  moss: 'pill-moss',
  violet: 'pill-violet',
}

/** Small colored badge for statuses, kinds, and labels (EP-2026-013 ST-01). */
export function Pill({
  tone = 'neutral',
  children,
}: {
  tone?: PillTone
  children: ReactNode
}) {
  return <span className={`pill ${TONE_CLASS[tone]}`}>{children}</span>
}
