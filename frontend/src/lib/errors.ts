export function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
