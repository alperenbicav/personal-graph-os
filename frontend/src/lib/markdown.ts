/** The one markdown → sanitized HTML pipeline for every view (EP-2026-013 ST-01).
 *
 * `marked` parses, DOMPurify strips anything executable. Previously each view carried its own
 * identical copy of this pair; they are unified here so a hardening change lands once.
 */
import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.setOptions({ gfm: true, breaks: true })

export function renderMarkdown(markdown: string): string {
  const html = marked.parse(markdown, { async: false }) as string
  return DOMPurify.sanitize(html)
}
