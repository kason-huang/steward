import assert from 'node:assert/strict'
import { test } from 'node:test'
import './registered-profile-surface.mjs'
import { Context } from '@deepseek-ai/cordis'

for (const artifact of ['src/index.ts', 'lib/index.js']) {
  const { EgoRuntimeClient } = await import(new URL(`../packages/dsh-ego-runtime-client/${artifact}`, import.meta.url))
  const { apply } = await import(new URL(`../packages/dsh-tool-ego/${artifact}`, import.meta.url))
  const reflection = await import(new URL(`../packages/dsh-ego-reflection/${artifact}`, import.meta.url))
  for (const environment of [undefined, 'off', 'toy']) for (const runtime of ['off', 'toy']) {
    test(`${artifact}: environment=${environment ?? 'unset'}, runtime=${runtime}`, async t => {
      const previous = process.env.STEWARD_ASSEMBLY_PROFILE
      if (environment === undefined) delete process.env.STEWARD_ASSEMBLY_PROFILE
      else process.env.STEWARD_ASSEMBLY_PROFILE = environment
      t.after(() => {
        if (previous === undefined) delete process.env.STEWARD_ASSEMBLY_PROFILE
        else process.env.STEWARD_ASSEMBLY_PROFILE = previous
      })
      const calls = [], logs = [], registered = [], hooks = new Map()
      t.mock.method(console, 'error', message => logs.push(message))
      const identity = [[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]
      const calibration = { stamp: 3, joint_encoders: {}, reference_camera: 'head_camera',
        calibration_source: 'rig_calibration', camera_axes: 'opencv_x_right_y_down_z_forward',
        views: { head_camera: { width: 8, height: 8, K: [[4,0,4],[0,4,4],[0,0,1]],
          camera_to_reference: identity, renderNearM: 0.01 } } }
      t.mock.method(globalThis, 'fetch', async (url, options) => {
        const path = new URL(url).pathname
        calls.push({ path, method: options.method, body: options.body })
        if (path === '/ego/control') return Response.json(runtime === 'toy' ? { owner: null, assemblyProfile: 'toy' } : { owner: null })
        if (path === '/ego/cameras/calibration') return Response.json(calibration)
        if (path === '/ego/explore') return Response.json({ id: 'job' })
        throw new Error(`unexpected request: ${path}`)
      })
      const ctx = new Context()
      t.after(() => ctx.fiber.dispose())
      const ego = new EgoRuntimeClient(ctx, { endpoint: 'http://ego.invalid', timeoutMs: 1000,
        cameraCaptureDir: 'unused', cameraMaxBytes: 1000 })
      const consumer = { ego, tools: { register: tool => { registered.push(tool); return () => {} } },
        on: (name, fn) => { const handlers = hooks.get(name) ?? []; handlers.push(fn); hooks.set(name, handlers) }, effect: fn => fn() }
      await Promise.all([apply(consumer), reflection.apply(consumer, reflection.Config({}))])
      const prediction = registered.find(tool => tool.name === 'ego_predict')
      assert.equal(prediction.parameters.properties.skill.enum.includes('complete_assembly'), runtime === 'toy')
      assert.equal(prediction.parameters.properties.skill.enum.includes('insert_screw_into_hole'), runtime === 'off')
      assert.equal(registered.some(tool => tool.name === 'ego_screw_holes'), runtime === 'off')
      assert.equal(await ego.assemblyProfile(), runtime)
      assert.deepEqual(calls, [{ path: '/ego/control', method: 'GET', body: undefined }])
      assert.equal(hooks.has('agent/turn-stopping'), runtime === 'toy')
      const mismatches = logs.filter(line => line.startsWith('STEWARD_HARNESS_PROFILE '))
      assert.equal(mismatches.length, (environment ?? 'off') === runtime ? 0 : 1)
      if (mismatches.length) assert.deepEqual(JSON.parse(mismatches[0].split(' ').slice(1).join(' ')),
        { environment: environment ?? 'off', runtime, selected: runtime })
      await ego.explore()
      assert.deepEqual(JSON.parse(calls.at(-1).body), runtime === 'toy' ? { timeoutS: 120 } : {})
      const parsed = await ego.cameraCalibration()
      assert.equal('stamp' in parsed, runtime === 'toy')
      assert.equal('renderNearM' in parsed.views.head_camera, runtime === 'toy')
      assert.equal(calls.filter(call => call.path === '/ego/control').length, 1)
      assert.equal(process.env.STEWARD_ASSEMBLY_PROFILE, environment)
    })
  }
  for (const response of [{ assemblyProfile: 'unsupported' }, { assemblyProfile: 1 }, { assemblyProfile: false }, { assemblyProfile: null }]) {
    test(`${artifact}: rejects invalid profile ${JSON.stringify(response)}`, async t => {
      t.mock.method(globalThis, 'fetch', async () => Response.json(response))
      const ctx = new Context(); t.after(() => ctx.fiber.dispose())
      const ego = new EgoRuntimeClient(ctx, { endpoint: 'http://ego.invalid', timeoutMs: 1000,
        cameraCaptureDir: 'unused', cameraMaxBytes: 1000 })
      const registered = []
      await assert.rejects(apply({ ego, tools: { register: tool => registered.push(tool) } }), /Unsupported Ego assembly profile/)
      assert.deepEqual(registered, [])
    })
  }
  for (const body of ['xlerobot', 'phyagent-piper']) test(`${artifact}: discovers ${body} and forwards a common TCP request`, async t => {
    const calls = []
    const skills = body === 'xlerobot' ? ['navigate', 'pick', 'place', 'look_around'] : ['pick', 'place']
    const target = { position: [0.27, -0.155, 0.85], orientation: [0, 1, 0, 0] }
    const feedback = { id: 'command', state: 'reached', pose: target, stamp: 1, gripper: { holding: false } }
    const capabilities = { body, skills, cameras: ['camera'], arms: [{ id: 'working' }],
      s0Tools: { ego_gripper_pose: 'read', body_set_pose: 'control', body_set_gripper: 'control',
        ...(body === 'phyagent-piper' ? { body_set_joints: 'control' } : {}) },
      tcp: { frame: 'base', orientationOrder: 'wxyz' } }
    t.mock.method(globalThis, 'fetch', async (url, options) => {
      const path = new URL(url).pathname
      if (path === '/ego/control') return Response.json({ owner: null, capabilities })
      assert.equal(path, '/ego/body/command')
      calls.push(JSON.parse(options.body))
      return Response.json(feedback)
    })
    const ctx = new Context()
    t.after(() => ctx.fiber.dispose())
    const config = { endpoint: 'http://ego.invalid', timeoutMs: 1000, owner: 'operator',
      taskId: 'task', intentRevision: 'revision' }
    const ego = new EgoRuntimeClient(ctx, config)
    const registered = []
    await apply({ ego, tools: { register: tool => registered.push(tool) } })
    assert.deepEqual(registered.filter(tool => tool.name.startsWith('body_set_')).map(tool => tool.name),
      ['body_set_pose', 'body_set_gripper'])
    const poseTool = registered.find(tool => tool.name === 'body_set_pose')
    const execution = { signal: new AbortController().signal }
    assert.deepEqual(await poseTool.execute(target, execution), feedback)
    assert.deepEqual(calls, [{ pose: target, owner: 'operator', taskId: 'task', intentRevision: 'revision' }])
    const description = await registered.find(tool => tool.name === 'ego_capabilities').execute({}, execution)
    assert.equal(description.body, body)
    assert.deepEqual(description.skills, skills)
    assert.equal(description.s0Tools.body_set_joints, undefined)
    assert.deepEqual(registered.find(tool => tool.name === 'ego_predict').parameters.properties.skill.enum.sort(),
      skills.filter(skill => skill !== 'look_around').sort())
    assert.equal(registered.some(tool => tool.name === 'ego_explore'), body === 'xlerobot')

    const readOnlyContext = new Context()
    t.after(() => readOnlyContext.fiber.dispose())
    const readOnly = new EgoRuntimeClient(readOnlyContext, { ...config, readOnly: true })
    const restricted = []
    await apply({ ego: readOnly, tools: { register: tool => restricted.push(tool) } }, { allowDirectS0: true })
    assert.ok(!restricted.some(tool => tool.name.startsWith('body_set_')))
    const readOnlyDescription = await restricted.find(tool => tool.name === 'ego_capabilities').execute({}, execution)
    assert.deepEqual(readOnlyDescription.s0Tools, { ego_gripper_pose: 'read' })
    assert.throws(() => readOnly.directBody({ pose: target }), /only permits observation/)
    assert.equal(calls.length, 1)
  })
  test(`${artifact}: connection failure prevents registration instead of guessing`, async t => {
    t.mock.method(globalThis, 'fetch', async () => { throw Error('unavailable') })
    const ctx = new Context(); t.after(() => ctx.fiber.dispose())
    const ego = new EgoRuntimeClient(ctx, { endpoint: 'http://ego.invalid', timeoutMs: 1000,
      cameraCaptureDir: 'unused', cameraMaxBytes: 1000 })
    const registered = []
    await assert.rejects(apply({ ego, tools: { register: tool => registered.push(tool) } }), /unavailable/)
    assert.deepEqual(registered, [])
  })
}
