import assert from 'node:assert/strict'
import test from 'node:test'

import { SIGN_IN_PATH, signInUrl } from '../src/utils/signIn'

test('signInUrl comes back to the page that needed sign-in', () => {
  assert.equal(signInUrl('/'), SIGN_IN_PATH)
  assert.equal(signInUrl('/runs/4?tab=output'), '/accounts/login/?next=%2Fruns%2F4%3Ftab%3Doutput')
})

test('signInUrl sends the old login route home', () => {
  assert.equal(signInUrl('/login'), SIGN_IN_PATH)
  assert.equal(signInUrl('/login?x=1'), SIGN_IN_PATH)
  assert.equal(signInUrl('/logins'), '/accounts/login/?next=%2Flogins')
})
