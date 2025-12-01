// GTD Module — MCP Tool Definitions
// These tools operate on org-mode files in Datacore spaces.
// Backend: org_workspace_adapter.py (replaces org_parser.py)

import { z } from 'zod'
import * as fs from 'fs'
import * as path from 'path'
import { execFile } from 'child_process'
import { promisify } from 'util'

const execFileAsync = promisify(execFile)

interface ModuleToolContext {
  storage: { basePath: string; mode: string }
  modulePath: string
  dataPath: string
  spaceName?: string
}

interface ModuleToolDefinition {
  name: string
  description: string
  inputSchema: z.ZodType
  handler: (args: unknown, context: ModuleToolContext) => Promise<unknown>
}

// --- Helpers ---

function findOrgFile(basePath: string, space: string | undefined, filename: string): string | null {
  if (space) {
    const p = path.join(basePath, space, 'org', filename)
    if (fs.existsSync(p)) return p
  }
  const personal = path.join(basePath, '0-personal', 'org', filename)
  if (fs.existsSync(personal)) return personal
  return null
}

function findAllOrgFiles(basePath: string, filename: string): Array<{ space: string; path: string }> {
  const results: Array<{ space: string; path: string }> = []
  try {
    for (const entry of fs.readdirSync(basePath)) {
      if (/^\d+-/.test(entry)) {
        const orgPath = path.join(basePath, entry, 'org', filename)
        if (fs.existsSync(orgPath)) {
          results.push({ space: entry, path: orgPath })
        }
      }
    }
  } catch (err: any) {
    process.stderr.write(`findAllOrgFiles: ${err.message}\n`)
  }
  return results
}

async function runAdapter(basePath: string, args: string[]): Promise<unknown> {
  const adapterScript = path.join(basePath, '.datacore', 'lib', 'org_workspace_adapter.py')
  try {
    const { stdout } = await execFileAsync('python3', [adapterScript, ...args], {
      timeout: 30000,
      env: { ...process.env },
    })
    return JSON.parse(stdout.trim())
  } catch (err: any) {
    return { error: `Adapter failed: ${err.message}`, detail: err.stderr?.slice(0, 500) || '' }
  }
}

// --- Tools ---

export const tools: ModuleToolDefinition[] = [
  {
    name: 'inbox_count',
    description: 'Count items in GTD inbox across spaces',
    inputSchema: z.object({
      space: z.string().optional().describe('Space name (e.g., "0-personal"). Omit for all spaces.'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { space } = args as { space?: string }

      if (space) {
        const orgPath = findOrgFile(ctx.storage.basePath, space, 'inbox.org')
        if (!orgPath) return { error: `No inbox.org found in ${space}` }
        return runAdapter(ctx.storage.basePath, ['count', '--files', orgPath])
      }

      const files = findAllOrgFiles(ctx.storage.basePath, 'inbox.org')
      if (files.length === 0) return { total: 0, spaces: [] }
      const results = await Promise.all(
        files.map(async f => {
          const r = await runAdapter(ctx.storage.basePath, ['count', '--files', f.path]) as any
          if (r.error) return { space: f.space, count: 0, error: r.error }
          return { space: f.space, count: r.count ?? 0 }
        })
      )
      const errors = results.filter(r => r.error)
      const total = results.reduce((sum, r) => sum + r.count, 0)
      const response: any = { total, spaces: results }
      if (errors.length > 0) response.warnings = errors.map((e: any) => `${e.space}: ${e.error}`)
      return response
    },
  },

  {
    name: 'add_task',
    description: 'Add a task to inbox.org with proper org-mode formatting',
    inputSchema: z.object({
      title: z.string().describe('Task title'),
      tags: z.string().optional().describe('Tags in :tag1:tag2: format'),
      scheduled: z.string().regex(/^\d{4}-\d{2}-\d{2}$/).optional().describe('Schedule date: YYYY-MM-DD'),
      space: z.string().optional().describe('Target space (default: 0-personal)'),
      priority: z.enum(['A', 'B', 'C']).optional().describe('Priority level'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { title, tags, scheduled, space, priority } = args as {
        title: string; tags?: string; scheduled?: string; space?: string; priority?: string
      }

      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'inbox.org')
      if (!orgPath) return { error: `No inbox.org found in ${targetSpace}` }

      const adapterArgs = ['add', '--file', orgPath, '--heading', title]
      if (tags) adapterArgs.push('--tags', tags)
      if (scheduled) adapterArgs.push('--scheduled', scheduled)
      if (priority) adapterArgs.push('--priority', priority)

      const result = await runAdapter(ctx.storage.basePath, adapterArgs) as { added?: boolean; id?: string }
      return { ...result, space: targetSpace }
    },
  },

  {
    name: 'list_next_actions',
    description: 'List TODO/NEXT tasks from next_actions.org',
    inputSchema: z.object({
      space: z.string().optional().describe('Space name (default: 0-personal)'),
      state: z.enum(['TODO', 'NEXT', 'WAITING', 'all']).optional().describe('Filter by state (default: all)'),
      limit: z.number().optional().describe('Max results (default: 20)'),
      tag: z.string().optional().describe('Filter by tag (e.g., "AI")'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { space, state, limit, tag } = args as {
        space?: string; state?: string; limit?: number; tag?: string
      }

      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      if (!orgPath) return { error: `No next_actions.org found in ${targetSpace}` }

      const adapterArgs = ['list', '--file', orgPath]
      if (state && state !== 'all') adapterArgs.push('--states', state)
      if (limit) adapterArgs.push('--limit', String(limit))
      if (tag) adapterArgs.push('--tags', tag.replace(/:/g, ''))

      const result = await runAdapter(ctx.storage.basePath, adapterArgs) as { count: number; tasks: unknown[] }
      return { space: targetSpace, ...result }
    },
  },

  {
    name: 'complete_task',
    description: 'Mark a task as DONE in next_actions.org',
    inputSchema: z.object({
      title: z.string().describe('Task title (or substring) to match'),
      space: z.string().optional().describe('Space name (default: 0-personal)'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { title, space } = args as { title: string; space?: string }

      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      if (!orgPath) return { error: `No next_actions.org found in ${targetSpace}` }

      const result = await runAdapter(ctx.storage.basePath, [
        'complete', '--file', orgPath, '--title', title,
      ])
      return { ...result as object, space: targetSpace }
    },
  },

  {
    name: 'agenda_view',
    description: 'Query tasks from org files with flexible filters (state, tags, focus area, deadline, scheduled)',
    inputSchema: z.object({
      states: z.array(z.string()).optional().describe('Filter by states (e.g., ["TODO", "NEXT"])'),
      tags: z.string().optional().describe('Tag filter (e.g., "AI" or ":AI:")'),
      focus_area: z.string().optional().describe('Category/focus area filter (partial match)'),
      deadline_within: z.number().optional().describe('Show tasks with deadline within N days'),
      scheduled_within: z.number().optional().describe('Show tasks scheduled within N days'),
      space: z.string().optional().describe('Space to query (omit for 0-personal)'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { states, tags, focus_area, deadline_within, scheduled_within, space } = args as {
        states?: string[]; tags?: string; focus_area?: string
        deadline_within?: number; scheduled_within?: number; space?: string
      }

      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      if (!orgPath) return { error: `No next_actions.org found in ${targetSpace}` }

      if (scheduled_within !== undefined && deadline_within !== undefined) {
        return { error: 'Provide either scheduled_within or deadline_within, not both' }
      }
      if (scheduled_within !== undefined) {
        const r = await runAdapter(ctx.storage.basePath, ['agenda', '--file', orgPath, '--days', String(scheduled_within)])
        return { space: targetSpace, ...(r as object) }
      }
      if (deadline_within !== undefined) {
        const r = await runAdapter(ctx.storage.basePath, ['deadlines', '--file', orgPath, '--days', String(deadline_within)])
        return { space: targetSpace, ...(r as object) }
      }

      const adapterArgs = ['list', '--file', orgPath]
      if (states && states.length > 0) adapterArgs.push('--states', states.join(','))
      if (tags) adapterArgs.push('--tags', tags.replace(/:/g, ''))

      const result = await runAdapter(ctx.storage.basePath, adapterArgs) as { tasks: Array<Record<string, unknown>>; count: number }
      if ('error' in (result as object)) return result

      let tasks = result.tasks || []
      if (focus_area) {
        const fa = focus_area.toLowerCase()
        tasks = tasks.filter((t: any) => {
          const props = t.properties || {}
          return (props.CATEGORY || '').toLowerCase().includes(fa) ||
                 (t.heading || '').toLowerCase().includes(fa)
        })
      }

      return { space: targetSpace, count: tasks.length, tasks }
    },
  },

  {
    name: 'deadline_warnings',
    description: 'List tasks with upcoming or overdue deadlines from next_actions.org',
    inputSchema: z.object({
      days: z.number().optional().describe('Show deadlines within N days (default: 14)'),
      space: z.string().optional().describe('Space name (default: 0-personal)'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { days: daysArg, space } = args as { days?: number; space?: string }
      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      if (!orgPath) return { error: `No next_actions.org found in ${targetSpace}` }

      const r = await runAdapter(ctx.storage.basePath, [
        'deadlines', '--file', orgPath, '--days', String(daysArg || 14),
      ])
      return { space: targetSpace, ...(r as object) }
    },
  },

  {
    name: 'archive_tasks',
    description: 'Archive DONE tasks older than N days from next_actions.org to archive file',
    inputSchema: z.object({
      space: z.string().optional().describe('Space name (default: 0-personal)'),
      min_age_days: z.number().optional().describe('Only archive DONE tasks closed more than N days ago (default: 30)'),
      dry_run: z.boolean().optional().describe('If true, list candidates without archiving (default: true)'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { space, min_age_days, dry_run } = args as { space?: string; min_age_days?: number; dry_run?: boolean }
      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      if (!orgPath) return { error: `No next_actions.org found in ${targetSpace}` }

      const adapterArgs = [
        'archive-done', '--file', orgPath,
        '--min-age', String(min_age_days || 30),
      ]
      const isDryRun = dry_run !== false  // default: dry-run enabled unless explicitly false
      if (isDryRun) adapterArgs.push('--dry-run')

      const r = await runAdapter(ctx.storage.basePath, adapterArgs)
      return { space: targetSpace, ...(r as object) }
    },
  },

  {
    name: 'write_clock_entry',
    description: 'Write a CLOCK time entry to a task LOGBOOK drawer in an org file',
    inputSchema: z.object({
      file: z.string().describe('Org file path (relative to data root or absolute)'),
      heading: z.string().optional().describe('Task heading (title substring to match)'),
      task_id: z.string().optional().describe('Task :ID: property for precise matching'),
      start: z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/).describe('Start time (YYYY-MM-DDTHH:MM)'),
      end: z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/).describe('End time (YYYY-MM-DDTHH:MM)'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { file, heading, task_id, start, end } = args as {
        file: string; heading?: string; task_id?: string; start: string; end: string
      }
      let filePath = file
      if (!path.isAbsolute(filePath)) {
        filePath = path.join(ctx.storage.basePath, filePath)
      }
      filePath = path.resolve(filePath)
      if (!filePath.startsWith(path.resolve(ctx.storage.basePath) + path.sep)) {
        return { error: 'File path must be within the data directory' }
      }
      if (!fs.existsSync(filePath)) return { error: `File not found: ${filePath}` }

      const adapterArgs = ['write-clock', '--file', filePath, '--start', start, '--end', end]
      if (task_id) adapterArgs.push('--id', task_id)
      else if (heading) adapterArgs.push('--title', heading)
      else return { error: 'Must provide either heading or task_id' }

      return runAdapter(ctx.storage.basePath, adapterArgs)
    },
  },

  {
    name: 'duplicate_check',
    description: 'Check for duplicate or near-duplicate tasks in next_actions.org',
    inputSchema: z.object({
      title: z.string().describe('Task title to check'),
      threshold: z.number().optional().describe('Similarity threshold 0-1 (default: 0.7)'),
      space: z.string().optional().describe('Space name (default: 0-personal)'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { title, threshold, space } = args as { title: string; threshold?: number; space?: string }
      const targetSpace = space || '0-personal'
      const nextActionsPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      const inboxPath = findOrgFile(ctx.storage.basePath, targetSpace, 'inbox.org')

      const results: unknown[] = []
      for (const orgPath of [nextActionsPath, inboxPath].filter(Boolean)) {
        const r = await runAdapter(ctx.storage.basePath, [
          'duplicates', '--file', orgPath!,
          '--title', title,
          '--threshold', String(threshold || 0.7),
        ]) as any
        if (!r.error && r.duplicates?.length) results.push(...r.duplicates)
      }
      if (!nextActionsPath && !inboxPath) return { error: `No org files found in ${targetSpace}` }
      return {
        query: title,
        threshold: threshold || 0.7,
        has_duplicates: results.length > 0,
        duplicates: results.slice(0, 10),
      }
    },
  },

  {
    name: 'project_health',
    description: 'Analyze project health: stuck projects, tasks without next actions, stale items',
    inputSchema: z.object({
      space: z.string().optional().describe('Space name (default: 0-personal)'),
      stale_days: z.number().optional().describe('Days without activity to flag as stale (default: 14)'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { space, stale_days } = args as { space?: string; stale_days?: number }
      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      if (!orgPath) return { error: `No next_actions.org found in ${targetSpace}` }

      const adapterArgs = ['project-health', '--file', orgPath]
      if (stale_days) adapterArgs.push('--stale-days', String(stale_days))
      return runAdapter(ctx.storage.basePath, adapterArgs)
    },
  },

  {
    name: 'effort_aggregate',
    description: 'Aggregate effort estimates across tasks by focus area and state',
    inputSchema: z.object({
      space: z.string().optional().describe('Space name (default: 0-personal)'),
      states: z.array(z.string()).optional().describe('Filter by states (default: ["TODO", "NEXT"])'),
    }),
    handler: async (args: unknown, ctx: ModuleToolContext) => {
      const { space, states } = args as { space?: string; states?: string[] }
      const targetSpace = space || '0-personal'
      const orgPath = findOrgFile(ctx.storage.basePath, targetSpace, 'next_actions.org')
      if (!orgPath) return { error: `No next_actions.org found in ${targetSpace}` }

      const adapterArgs = ['effort-summary', '--file', orgPath]
      if (states && states.length > 0) adapterArgs.push('--states', states.join(','))

      return runAdapter(ctx.storage.basePath, adapterArgs)
    },
  },

]
