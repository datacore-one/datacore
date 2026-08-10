// goals — tools (CLI / open-source surface).
//
// Plain JS (ESM) for direct dynamic import by the datacore-mcp server.
// Operates directly on 0-personal/goals.yaml. Read-only +
// best-effort writes — no flock here. For atomic, gated writes with
// event emission, see `app-tools/index.mjs` (daemon-wrapped).

import { z } from 'zod'
import * as fs from 'fs'
import * as path from 'path'
import * as yaml from 'js-yaml'

const HORIZONS = ['five_year', 'year', 'quarter', 'month', 'week']
const STATUSES = ['open', 'done', 'abandoned']

function goalsPath(basePath) {
  return path.join(basePath, '0-personal', 'goals.yaml')
}

function readGoals(basePath) {
  const p = goalsPath(basePath)
  if (!fs.existsSync(p)) return { goals: [] }
  try {
    const raw = fs.readFileSync(p, 'utf-8')
    const data = yaml.load(raw) ?? {}
    if (!Array.isArray(data.goals)) data.goals = []
    return data
  } catch (err) {
    return { goals: [], _error: err.message }
  }
}

function writeGoals(basePath, data) {
  const p = goalsPath(basePath)
  fs.mkdirSync(path.dirname(p), { recursive: true })
  fs.writeFileSync(p, yaml.dump(data, { sortKeys: false }), 'utf-8')
}

function nextId(items) {
  const used = new Set()
  for (const item of items) {
    if (typeof item?.id === 'string' && item.id.startsWith('g-')) {
      const n = parseInt(item.id.slice(2), 10)
      if (Number.isFinite(n)) used.add(n)
    }
  }
  let n = 1
  while (used.has(n)) n++
  return `g-${n}`
}

export const tools = [
  {
    name: 'list_goals',
    description: 'List goals from 0-personal/goals.yaml. Read-only — daemon not required.',
    inputSchema: z.object({
      horizon: z.enum(HORIZONS).optional(),
      status: z.enum(STATUSES).optional(),
    }),
    handler: async (args, ctx) => {
      const data = readGoals(ctx.storage.basePath)
      let rows = data.goals
      if (args.horizon) rows = rows.filter(g => g.horizon === args.horizon)
      if (args.status) rows = rows.filter(g => (g.status ?? 'open') === args.status)
      return { count: rows.length, goals: rows }
    },
  },

  {
    name: 'create_goal',
    description: 'Create a new goal. Best-effort write (no flock); for safe writes use the daemon-wrapped tool.',
    inputSchema: z.object({
      statement: z.string().min(1),
      horizon: z.enum(HORIZONS),
      parent_id: z.string().optional(),
      owner: z.string().optional(),
      key_results: z.array(z.string()).optional(),
    }),
    handler: async (args, ctx) => {
      const data = readGoals(ctx.storage.basePath)
      const id = nextId(data.goals)
      const row = {
        id,
        horizon: args.horizon,
        statement: args.statement.trim(),
        status: 'open',
        created: new Date().toISOString().replace(/\.\d+Z$/, '+00:00'),
        progress_log: [],
      }
      if (args.parent_id) row.parent_id = args.parent_id
      if (args.owner) row.owner = args.owner
      if (args.key_results?.length) row.key_results = args.key_results.filter(Boolean)

      data.goals.push(row)
      writeGoals(ctx.storage.basePath, data)
      return { ok: true, goal_id: id, goal: row }
    },
  },

  {
    name: 'update_goal',
    description: 'Edit a goal\'s fields. Best-effort write.',
    inputSchema: z.object({
      goal_id: z.string(),
      statement: z.string().optional(),
      status: z.enum(STATUSES).optional(),
      owner: z.string().optional(),
      key_results: z.array(z.string()).optional(),
    }),
    handler: async (args, ctx) => {
      const data = readGoals(ctx.storage.basePath)
      const target = data.goals.find(g => g?.id === args.goal_id)
      if (!target) return { error: `goal ${args.goal_id} not found` }

      if (args.statement !== undefined) target.statement = args.statement.trim()
      if (args.status !== undefined) target.status = args.status
      if (args.owner !== undefined) target.owner = args.owner.trim()
      if (args.key_results !== undefined) target.key_results = args.key_results.filter(Boolean)

      writeGoals(ctx.storage.basePath, data)
      return { ok: true, goal_id: args.goal_id, goal: target }
    },
  },

  {
    name: 'log_progress',
    description: 'Append a progress note to a goal\'s progress_log.',
    inputSchema: z.object({
      goal_id: z.string(),
      note: z.string().min(1),
    }),
    handler: async (args, ctx) => {
      const data = readGoals(ctx.storage.basePath)
      const target = data.goals.find(g => g?.id === args.goal_id)
      if (!target) return { error: `goal ${args.goal_id} not found` }

      if (!Array.isArray(target.progress_log)) target.progress_log = []
      target.progress_log.push({
        at: new Date().toISOString().replace(/\.\d+Z$/, '+00:00'),
        note: args.note.trim(),
      })

      writeGoals(ctx.storage.basePath, data)
      return { ok: true, goal_id: args.goal_id, goal: target }
    },
  },
]
