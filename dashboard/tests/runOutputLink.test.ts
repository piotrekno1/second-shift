import assert from 'node:assert/strict'
import test from 'node:test'
import { runOutputLink } from '../src/utils/runOutputLink'

test('report output links download from their own run', () => {
  assert.equal(runOutputLink('/outputs/dependency_licenses.csv', 2), '/api/runs/2/saved-files/dependency_licenses.csv')
  assert.equal(runOutputLink('/outputs/reports/license%20list.csv', 3), '/api/runs/3/saved-files/reports/license%20list.csv')
})

test('other links and unsafe output paths are preserved', () => {
  for (const href of ['https://example.com/outputs/a.csv', '//example.com/outputs/a.csv', '/outputs/../a.csv', '/outputs/%2e%2e/a.csv', '/outputs/%invalid', '/outputs/']) {
    assert.equal(runOutputLink(href, 2), href)
  }
})
