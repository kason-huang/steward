import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const capability = JSON.parse(readFileSync(new URL('../../capabilities/panda_pick.json', import.meta.url), 'utf8'))
const run = new (Object.getPrototypeOf(async function () {}).constructor)('tools', 'args', capability.code)

test('generic Panda pick capability binds the current target and requires all terminal fields', async () => {
  const requests = []
  const tools = {
    async ego_predict(args) { requests.push(args); return { feasible: true, withinRiskLimit: true, receipt: 'receipt-current' } },
    async skill_run(args) { assert.equal(args.receipt, 'receipt-current'); return {
      job: 'job-current', state: 'succeeded', goalStatus: 'achieved', stopStatus: 'confirmed', evidence: { target: 'e:current' },
    } },
  }
  const result = await run(tools, { target: 'e:current' })
  assert.deepEqual(requests, [{ skill: 'pick', target: 'e:current' }])
  assert.equal(result.job, 'job-current')
  tools.skill_run = async () => ({ job: 'job-failed', state: 'failed', goalStatus: 'not_achieved', stopStatus: 'confirmed' })
  await assert.rejects(run(tools, { target: 'e:other' }), /pick not confirmed/)
  assert.equal(capability.status, 'candidate')
})
