import type { ChatHealth } from './api'

export function healthProblem(health: ChatHealth | undefined): string | null {
  if (!health || health.codex.available) return null
  const { detail } = health.codex
  if (detail === 'not_authenticated') {
    return 'The assistant is not signed in yet. Someone with access to the server needs to run the Codex login (see docs/CODEX.md).'
  }
  if (detail === 'disabled') return 'The assistant is turned off on the server (see docs/CODEX.md).'
  if (detail === 'binary_not_found') {
    return 'The Codex CLI is not installed on the server, so the assistant cannot answer (see docs/CODEX.md).'
  }
  return 'The assistant is not available right now. Check the Codex setup on the server (see docs/CODEX.md).'
}
