// Replay a controlled model's placement call and its independent observation outcome.
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createServer } from 'node:http'
import { createRequire } from 'node:module'
import { mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { portableSession } from './portable-session.mjs'
import { launchProfile as launch } from './profile-test.mjs'

const workspace = resolve(import.meta.dirname, '..')
const snapshotRoot = join(import.meta.dirname, 'snapshots/place-verification')
const args = { receipt: 'rcpt-place', wait: true }
const failedResult = { id: 'job-place', skill: 'place', receipt: 'rcpt-place', state: 'succeeded',
  execution: 'succeeded', verification: { status: 'failed', checks: { released: 'passed', position: 'passed', support: 'failed' },
    reason: 'object rests on another piece; re-plan before retrying' }, message: 'placed e:piece on e:table', log: [] }

for (const outcome of ['failed', 'passed']) {
  const snapshot = outcome === 'failed' ? snapshotRoot : join(snapshotRoot, 'normal-placement')
  const result = outcome === 'failed' ? failedResult : { ...failedResult, verification: { status: 'passed',
    checks: { released: 'passed', position: 'passed', support: 'passed' },
    reason: 'fresh stable observations confirm the requested placement', supportGapM: 0.001,
    supportUncertaintyM: 0.0027, supportToleranceM: 0.01 } }
  const finalText = outcome === 'failed'
    ? 'Mechanical execution completed, but support verification failed. The object rests on another piece. Re-plan before retrying; do not continue dependent placements.'
    : 'Mechanical execution completed and fresh stable observations passed placement verification within the reported contact-height tolerance.'

  test(`shipped Ego profile replays a placement with verification ${outcome}`, { timeout: 240000 }, async t => {
    const root = await mkdtemp(join(tmpdir(), 'steward-place-verification-'))
    t.after(() => rm(root, { recursive: true, force: true }))
    const calls = []
    let modelStep = 0
    const recording = process.env.PLACE_VERIFICATION_RECORD === '1'
    const server = createServer(async (req, res) => {
      let body = ''; for await (const chunk of req) body += chunk
      if (req.url === '/ego/receipt?id=rcpt-place') {
        res.setHeader('content-type', 'application/json')
        res.end(JSON.stringify({ id: 'rcpt-place', skill: 'place', feasible: true, risk: 0.02,
          maxRisk: 0.35, issues: [], expiresAt: 100, now: 0, used: false, plan: {} })); return
      }
      if (req.url === '/skill/run' || req.url === '/skill/wait') {
        calls.push({ path: req.url, body: JSON.parse(body) })
        const value = req.url === '/skill/run'
          ? { ...result, state: 'running', execution: 'running', verification: { status: 'pending', checks: {}, reason: 'executing' } }
          : result
        res.setHeader('content-type', 'application/json'); res.end(JSON.stringify(value)); return
      }
      if (!recording) { res.writeHead(500); res.end('model network disabled during replay'); return }
      if (req.url !== '/v1/messages') { res.writeHead(404); res.end('unknown fixture route'); return }
      const first = modelStep++ === 0
      res.setHeader('content-type', 'text/event-stream')
      const send = (event, data) => res.write(`event: ${event}\ndata: ${JSON.stringify({ type: event, ...data })}\n\n`)
      send('message_start', { message: { id: 'placement-response', type: 'message', role: 'assistant', model: 'deepseek-flash',
        content: [], stop_reason: null, usage: { input_tokens: 100, output_tokens: 0 } } })
      send('content_block_start', { index: 0, content_block: first
        ? { type: 'tool_use', id: 'placement-call', name: 'skill_run', input: {} } : { type: 'text', text: '' } })
      send('content_block_delta', { index: 0, delta: first
        ? { type: 'input_json_delta', partial_json: JSON.stringify(args) }
        : { type: 'text_delta', text: finalText } })
      send('content_block_stop', { index: 0 })
      send('message_delta', { delta: { stop_reason: first ? 'tool_use' : 'end_turn', stop_sequence: null }, usage: { output_tokens: 50 } })
      send('message_stop', {})
      res.end()
    })
    await new Promise(ok => server.listen(0, '127.0.0.1', ok))
    t.after(() => { server.closeAllConnections(); return new Promise(ok => server.close(ok)) })
    const endpoint = `http://127.0.0.1:${server.address().port}`
    const cwd = join(root, 'work'); await mkdir(cwd)
    const env = { ...process.env, DSH_HOME: join(root, 'home'), DSH_SNAPSHOT_FILE: join(snapshot, 'session.v4.jsonl') }
    delete env.DEEPSEEK_API_KEY; delete env.DEEPSEEK_BASE_URL
    if (recording) { env.DEEPSEEK_API_KEY = 'synthetic-fixture-key'; env.DEEPSEEK_BASE_URL = endpoint }
    await launch(['--profile', 'ego', '--from-default-profile', 'headless', '--dump-default-config'], cwd, env)
    await launch(['plugin', '--profile', 'ego', 'add', join(workspace, 'packages/dsh-bundle-ego')], cwd, env)
    const patch = [
      { id: 'tools', config: { mode: 'native' } }, { id: 'session-title-llm', disabled: true },
      { id: 'steward-ego-runtime', config: { endpoint, timeoutMs: 5000 } },
      { id: 'agent-default-model', config: { provider: 'deepseek-official', model: 'deepseek-flash' } },
      { id: 'session-persistence-jsonl', config: { root: './sessions', compression: 'none' } },
      { id: 'session-log-deepseek', config: { enabled: false } },
    ]
    if (!recording) patch.push({ id: 'llm-deepseek', disabled: true }, { id: 'llm-deepseek-account', disabled: true },
      { insert: [{ id: 'llm-replay', name: pathToFileURL(createRequire(import.meta.url).resolve('@deepseek-ai/dsh-llm-replay')).href,
        config: { providers: [{ id: 'deepseek-official', models: [{ id: 'deepseek-flash', contextWindow: 128000, reasoningEfforts: ['low'], defaultReasoningEffort: 'low' }] }] } }] })
    const patchPath = join(root, 'patch.json'); await writeFile(patchPath, JSON.stringify(patch))
    const output = await launch(['--profile', 'ego', '--patch', patchPath, '--json',
      'This synthetic fixture already supplied rcpt-place. Run skill_run with receipt rcpt-place and wait=true. Report mechanical and verification outcomes separately. Do not retry or call other tools.'], cwd, env)
    assert.deepEqual(calls, [{ path: '/skill/run', body: { receipt: 'rcpt-place' } },
      { path: '/skill/wait', body: { job: 'job-place', timeoutS: 180 } }])
    const events = output.trim().split('\n').filter(x => x.startsWith('{')).map(x => JSON.parse(x))
    const tools = events.filter(e => e.type === 'tool_result')
    assert.equal(tools.length, 1)
    const value = JSON.parse(tools[0].result)
    assert.deepEqual(value, { job: result.id, skill: result.skill, state: result.state, message: result.message, log: [],
      execution: result.execution, verification: result.verification })
    const transcript = JSON.stringify(value, null, 2) + '\n'
    if (recording) {
      const paths = await readdir(join(cwd, 'sessions'), { recursive: true })
      const file = paths.find(p => p.endsWith('session.v4.jsonl')); assert.ok(file)
      const raw = await readFile(join(cwd, 'sessions', file), 'utf8')
      await mkdir(snapshot, { recursive: true })
      await writeFile(join(snapshot, 'session.v4.jsonl'), portableSession(raw, root, endpoint)
        .replaceAll(workspace.replaceAll('\\', '/'), '{{dshWorkspace}}'))
      await writeFile(join(snapshot, 'tools.expected.json'), transcript)
    } else assert.equal(transcript, await readFile(join(snapshot, 'tools.expected.json'), 'utf8'))
  })
}
