import type { User } from '../types'

/**
 * The actions /api/me lists in `permissions`. They match Action in
 * secondshift/workspaces/access.py. The server enforces them. The dashboard
 * only uses them to hide controls the person cannot use.
 */
export const Action = {
  AddGitHubRepositories: 'add GitHub repositories',
  AddLocalRepositories: 'add local repositories',
  AddConnections: 'add connections',
  EditConnections: 'edit connections',
  TestConnections: 'test connections',
  DisconnectConnections: 'disconnect connections',
  DeleteConnections: 'delete connections',
  ReviewCapabilities: 'review capabilities',
  InvitePeople: 'invite people',
  ManageMembers: 'change roles or remove people',
  ResyncRepositories: 'resync repositories',
  ChangeRepositoryState: 'change repository state',
  RemoveRepositories: 'remove repositories',
  EnableJobs: 'enable jobs',
  RunJobs: 'run jobs',
} as const

export type Action = (typeof Action)[keyof typeof Action]

export function allows(user: User | null, action: Action): boolean {
  return user?.permissions.includes(action) ?? false
}
