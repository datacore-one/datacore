// decisions — tools (CLI / open-source surface).
//
// Plain JS (ESM) for direct dynamic import by the datacore-mcp server.
// Operates directly on `[space]/3-knowledge/decisions/*.md` files.
// Read-only + best-effort writes — no flock here. For atomic, gated
// writes through the active-space resolver, see `app-tools/index.mjs`.

import { z } from 'zod'
import * as fs from 'fs'
import * as path from 'path'

const STATUSES = ['proposed', 'accepted', 'superseded', 'deprecated']
const DECISIONS_SUBDIR = path.join('3-knowledge', 'decisions')

function findDecisionRoots(basePath) {
  const out = []
  try {
    for (const entry of fs.readdirSync(basePath)) {
      if (!/^\d+-/.test(entry)) continue
      const dir = path.join(basePath, entry, DECISIONS_SUBDIR)
      if (fs.existsSync(dir)) out.push({ space: entry, dir })
    }
  } catch { /* ignore */ }
  return out
}

function parseFrontMatter(text) {
  if (!text.startsWith('---')) return {}
  const end = text.indexOf('\n---', 3)
  if (end < 0) return {}
  const block = text.slice(3, end).trim()
  const out = {}
  for (const line of block.split('\n')) {
    const t = line.trim()
    if (!t || t.startsWith('#')) continue
    const idx = t.indexOf(':')
    if (idx < 0) continue
    const k = t.slice(0, idx).trim()
    let v = t.slice(idx + 1).trim()
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1)
    }
    out[k] = v
  }
  return out
}

function parseStatusSection(text) {
  const m = text.match(/^## Status\s*\n+([a-z]+)/m)
  return m && STATUSES.includes(m[1]) ? m[1] : null
}

function summarize(filePath) {
  const text = fs.readFileSync(filePath, 'utf-8')
  const fm = parseFrontMatter(text)
  const titleMatch = text.match(/^#\s+(.+)$/m)
  const id = path.basename(filePath, '.md')
  const title = (fm.title || (titleMatch ? titleMatch[1] : id)).trim()
  let status = (fm.status || parseStatusSection(text) || 'proposed').trim()
  if (!STATUSES.includes(status)) status = 'proposed'
  const dateMatch = id.match(/^(\d{4}-\d{2}-\d{2})/)
  return {
    id,
    title,
    date: fm.date || (dateMatch ? dateMatch[1] : ''),
    status,
    path: filePath,
  }
}

function slugify(title) {
  return (title || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'decision'
}

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

export const tools = [
  {
    name: 'list_decisions',
    description: 'List decisions across all spaces (sorted most-recent-first). Optional space + status filter.',
    inputSchema: z.object({
      space: z.string().optional().describe('Filter by space (e.g. 2-datacore)'),
      status: z.enum(STATUSES).optional(),
    }),
    handler: async (args, ctx) => {
      let roots = findDecisionRoots(ctx.storage.basePath)
      if (args.space) roots = roots.filter(r => r.space === args.space)

      const rows = []
      for (const { space, dir } of roots) {
        for (const f of fs.readdirSync(dir).filter(n => n.endsWith('.md'))) {
          try {
            const summary = summarize(path.join(dir, f))
            rows.push({ space, ...summary })
          } catch { /* ignore */ }
        }
      }
      rows.sort((a, b) => (b.date ?? '').localeCompare(a.date ?? ''))
      const filtered = args.status ? rows.filter(r => r.status === args.status) : rows
      return { count: filtered.length, decisions: filtered }
    },
  },

  {
    name: 'read_decision',
    description: 'Read a decision\'s full markdown body. Specify space if the decision id is ambiguous.',
    inputSchema: z.object({
      decision_id: z.string(),
      space: z.string().optional(),
    }),
    handler: async (args, ctx) => {
      let roots = findDecisionRoots(ctx.storage.basePath)
      if (args.space) roots = roots.filter(r => r.space === args.space)

      for (const { space, dir } of roots) {
        const file = path.join(dir, `${args.decision_id}.md`)
        if (fs.existsSync(file)) {
          const summary = summarize(file)
          return { space, ...summary, body: fs.readFileSync(file, 'utf-8') }
        }
      }
      return { error: `decision ${args.decision_id} not found` }
    },
  },

  {
    name: 'create_decision',
    description: 'Create a new decision file in [space]/3-knowledge/decisions/ in MADR-ish format. Best-effort write.',
    inputSchema: z.object({
      space: z.string().describe('Target space (e.g. 2-datacore)'),
      title: z.string().min(1),
      context: z.string().optional(),
      decision: z.string().optional(),
      consequences: z.string().optional(),
    }),
    handler: async (args, ctx) => {
      const dir = path.join(ctx.storage.basePath, args.space, DECISIONS_SUBDIR)
      fs.mkdirSync(dir, { recursive: true })

      const date = todayIso()
      const slug = slugify(args.title).slice(0, 48)
      let id = `${date}-${slug}`
      let target = path.join(dir, `${id}.md`)
      let n = 2
      while (fs.existsSync(target)) {
        id = `${date}-${slug}-${n}`
        target = path.join(dir, `${id}.md`)
        n++
      }

      const body = [
        '---',
        `title: ${args.title}`,
        `date: ${date}`,
        'status: proposed',
        '---',
        '',
        `# ${args.title}`,
        '',
        '## Context',
        '',
        (args.context ?? '').trim() || '(...)',
        '',
        '## Decision',
        '',
        (args.decision ?? '').trim() || '(...)',
        '',
        '## Status',
        '',
        'proposed',
        '',
        '## Consequences',
        '',
        (args.consequences ?? '').trim() || '(...)',
        '',
      ].join('\n')

      fs.writeFileSync(target, body, 'utf-8')
      return { ok: true, decision_id: id, space: args.space, path: target }
    },
  },

  {
    name: 'update_status',
    description: 'Promote or deprecate a decision (proposed → accepted → superseded/deprecated). Updates front matter and Status section.',
    inputSchema: z.object({
      space: z.string().describe('Space the decision lives in'),
      decision_id: z.string(),
      status: z.enum(STATUSES),
    }),
    handler: async (args, ctx) => {
      const file = path.join(ctx.storage.basePath, args.space, DECISIONS_SUBDIR, `${args.decision_id}.md`)
      if (!fs.existsSync(file)) return { error: `decision ${args.decision_id} not found in ${args.space}` }

      let text = fs.readFileSync(file, 'utf-8')
      if (text.startsWith('---')) {
        text = text.replace(/^status:\s*\w+\s*$/m, `status: ${args.status}`)
      }
      text = text.replace(/^## Status\s*\n+\w+/m, `## Status\n\n${args.status}`)
      fs.writeFileSync(file, text, 'utf-8')
      return { ok: true, decision_id: args.decision_id, status: args.status, path: file }
    },
  },
]
