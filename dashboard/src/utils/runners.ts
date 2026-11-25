import type { WorkspaceSettings } from '../types'

export type RunnerSettings = WorkspaceSettings['runners'][number]

/** How busy a runner is: "1 running · no limit" or "2 of 4 running". */
export function runnerSessions(runner: Pick<RunnerSettings, 'running' | 'max_sessions'>): string {
  if (runner.max_sessions === null) return `${runner.running} running · no limit`
  return `${runner.running} of ${runner.max_sessions} running`
}

/** The dot beside a runner: healthy, needs attention, failing, or off. */
export function runnerDot(runner: Pick<RunnerSettings, 'state' | 'tone'>): string {
  if (runner.tone) return runner.tone
  if (runner.state === 'Down') return 'off'
  if (runner.state === 'Draining') return 'attention'
  return 'ok'
}

/** Whether a runner can run subscription agents, in words. */
export function runnerAgents(agents: boolean | null): { text: string; warn: boolean } {
  if (agents === null) return { text: 'Not known yet', warn: false }
  if (agents) return { text: 'Claude Code and Codex', warn: false }
  return { text: 'No agent image. Claude Code and Codex runs placed here fail.', warn: true }
}

/** A key fingerprint short enough to compare by eye: "SHA256:vzDs2O0Y…z2G1EU=". */
export function shortFingerprint(fingerprint: string): string {
  const [algorithm, hash] = fingerprint.includes(':')
    ? (fingerprint.split(':', 2) as [string, string])
    : ['', fingerprint]
  if (hash.length <= 20) return fingerprint
  return `${algorithm ? `${algorithm}:` : ''}${hash.slice(0, 8)}…${hash.slice(-7)}`
}
