import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

const capability = JSON.parse(readFileSync(new URL('../../capabilities/panda_pick_place.json', import.meta.url), 'utf8'))
const run = new (Object.getPrototypeOf(async function () {}).constructor)('tools', 'args', capability.code)

test('Panda composite capability binds both observed ids and requires confirmed terminal success', async () => {
  const requests = []
  const tools = {
    async ego_predict(args) { requests.push(args); return { feasible: true, withinRiskLimit: true, receipt: 'receipt-current' } },
    async skill_run(args) { assert.equal(args.receipt, 'receipt-current'); return {
      job: 'job-current', state: 'succeeded', goalStatus: 'achieved', stopStatus: 'confirmed',
    } },
  }
  const args = { target: 'e:current-object', on: 'e:current-basket' }
  const result = await run(tools, args)
  assert.deepEqual(requests, [{ skill: 'libero_pick_place', ...args }])
  assert.equal(result.job, 'job-current')
  await assert.rejects(run(tools, { target: args.target }), /requires/)
  for (const outcome of [
    { state: 'failed', goalStatus: 'not_achieved', stopStatus: 'confirmed' },
    { state: 'succeeded', goalStatus: 'unconfirmed', stopStatus: 'confirmed' },
    { state: 'succeeded', goalStatus: 'achieved', stopStatus: 'unknown' },
  ]) {
    tools.skill_run = async () => ({ job: 'job-incomplete', ...outcome })
    await assert.rejects(run(tools, args), /pick not confirmed/)
  }
  assert.equal(capability.status, 'candidate')
})
