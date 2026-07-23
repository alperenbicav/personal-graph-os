import type { DiscoveryCandidateInput } from '../types'

/** Manual paste is line-oriented, not a form-per-candidate: `identifier | title | description`,
 * one candidate per line. There is no metadata fetch (decision #6, `WORK.md`), so a title must
 * always be supplied by the caller rather than derived from the identifier. */
export function parseDiscoveryCandidates(raw: string): DiscoveryCandidateInput[] {
  return raw
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .map((line) => {
      const [identifier = '', title = '', description = ''] = line.split('|').map((part) => part.trim())
      return { identifier, title: title || identifier, description }
    })
    .filter((candidate) => candidate.identifier.length > 0)
}
