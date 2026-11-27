import type { Context } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-agent'
import type {} from '@deepseek-ai/dsh-tools'

function skillChoices(properties: unknown): string[] | null {
  if (!properties || typeof properties !== 'object' || !('skill' in properties)) return []
  const skill = properties.skill
  if (!skill || typeof skill !== 'object') return []
  if ('enum' in skill && Array.isArray(skill.enum)) {
    return skill.enum.filter((value): value is string => typeof value === 'string').sort()
  }
  return 'type' in skill && skill.type === 'string' ? null : []
}

/** Publish the scoped tool schemas used by the robot agent, reduced to names and skill choices. */
export function reportSessionTools(ctx: Context, readOnly: boolean): void {
  ctx.inject(['tools'], (ctx) => {
    const lastBySession = new Map<string, string>()
    ctx.on('agent/request', async ({ agent, signal }, next) => {
      const request = await next()
      if (signal.aborted) return request
      const schemas = ctx.tools.schemas(agent)
      const prediction = schemas.find((tool) => tool.name === 'ego_predict')
      const line = JSON.stringify({
        type: 'steward_tool_catalog',
        sessionId: agent.id,
        tools: schemas.map((tool) => tool.name).sort(),
        predictSkills: skillChoices(prediction?.parameters.properties),
        readOnly,
      })
      if (lastBySession.get(agent.id) !== line) {
        lastBySession.set(agent.id, line)
        process.stdout.write(line + '\n')
      }
      return request
    })
    ctx.on('agent/disposed', ({ agent }) => { lastBySession.delete(agent.id) })
  })
}
