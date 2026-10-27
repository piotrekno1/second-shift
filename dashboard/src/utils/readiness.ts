import type { Capability, Connection, Job } from '../types'

/** Why a job cannot be switched on yet, or '' when nothing blocks it. */
export function enableBlocker(job: Pick<Job, 'enabled' | 'capabilities'>): string {
  if (job.enabled) return ''
  const waiting = job.capabilities.filter((capability) => capability.required && capability.grant_status !== 'approved')
  if (!waiting.length) return ''
  const names = waiting.map((capability) => capability.binding).join(', ')
  return `Approve ${names} first. Scheduled runs would fail without ${waiting.length === 1 ? 'it' : 'them'}.`
}

/** The workspace model connection, when its last check failed or it is not connected. */
export function brokenModelConnection(connections: Connection[]): Connection | null {
  const model = connections.find((connection) => connection.kind === 'model' && connection.is_default)
    ?? connections.find((connection) => connection.kind === 'model')
  if (!model) return null
  return model.status !== 'connected' || model.last_check?.ok === false ? model : null
}

/** Approving again only makes sense when the chosen connection changed. */
export function approvalPending(capability: Pick<Capability, 'grant_status' | 'connection_id'>, selected: number | null): boolean {
  return capability.grant_status !== 'approved' || selected !== capability.connection_id
}
