import assert from 'node:assert/strict'
import test from 'node:test'
import type { ToolInput } from '../src/types'
import { describeRule, parseValue, parseValues, ruleTypesFor, sameRules, withRule, withoutRule } from '../src/utils/toolRules'

const input: ToolInput = {
  properties: {
    channel: { type: 'string' },
    limit: { type: 'integer' },
    text: { type: 'string' },
  },
  required: ['text'],
}

test('offers only the rules that fit an argument', () => {
  assert.deepEqual(ruleTypesFor(input, 'limit').map((rule) => rule.value), ['fixed', 'allowed', 'maximum', 'forbidden'])
  assert.deepEqual(ruleTypesFor(input, 'channel').map((rule) => rule.value), ['fixed', 'allowed', 'forbidden'])
  assert.deepEqual(ruleTypesFor(input, 'text').map((rule) => rule.value), ['fixed', 'allowed'])
})

test('parses values by the argument type', () => {
  assert.equal(parseValue({ type: 'integer' }, ' 5 '), 5)
  assert.equal(parseValue({ type: 'boolean' }, 'false'), false)
  assert.equal(parseValue({ type: 'string' }, '42'), '42')
  assert.deepEqual(parseValues({ type: 'string' }, '#alerts, #ops,'), ['#alerts', '#ops'])
})

test('describes rules in plain words', () => {
  assert.equal(describeRule('channel', { type: 'allowed', values: ['#alerts', '#ops'] }), 'channel is only #alerts or #ops')
  assert.equal(describeRule('state', { type: 'fixed', value: 'open' }), 'state is always open')
  assert.equal(describeRule('limit', { type: 'maximum', value: 50 }), 'limit is at most 50')
  assert.equal(describeRule('url', { type: 'forbidden' }), 'url is never sent')
})

test('adds and removes rules without touching other tools', () => {
  const one = withRule({}, 'post', 'url', { type: 'forbidden' })
  const two = withRule(one, 'list', 'limit', { type: 'maximum', value: 5 })

  assert.deepEqual(withoutRule(two, 'post', 'url'), { list: { limit: { type: 'maximum', value: 5 } } })
  assert.equal(sameRules(two, { list: two.list, post: two.post }), true)
  assert.equal(sameRules(two, one), false)
})
