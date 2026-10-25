import assert from 'node:assert/strict'
import test from 'node:test'

import type { User } from '../src/types'
import { Action, allows } from '../src/utils/permissions'

function person(permissions: string[]): User {
  return {
    email: 'a@x.dev',
    workspace_name: 'Team',
    workspace_slug: 'team',
    role: 'admin',
    permissions,
    can_use_admin: false,
    can_create_workspace: false,
    local_repositories_enabled: false,
    environment: 'Production',
    sandbox: 'gVisor sandbox',
  }
}

test('allows only the actions the server listed', () => {
  const admin = person([Action.RunJobs])
  assert.equal(allows(admin, Action.RunJobs), true)
  assert.equal(allows(admin, Action.AddConnections), false)
})

test('nobody signed in can do nothing', () => {
  assert.equal(allows(null, Action.RunJobs), false)
})
