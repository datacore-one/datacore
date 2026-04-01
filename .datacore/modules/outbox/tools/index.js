// Outbox Module — MCP Tool Definitions
// Scans outbox directories and provides archive search.
// Plain JS (ESM) for direct dynamic import by the MCP server.

import { z } from 'zod'
import { execFile } from 'child_process'
import { promisify } from 'util'
import * as fs from 'fs'
import * as path from 'path'

const execFileAsync = promisify(execFile)

// --- Helpers ---

function findOutboxDirs(basePath) {
  const dirs = []
  try {
    for (const entry of fs.readdirSync(basePath)) {
      if (/^\d+-/.test(entry)) {
        const outboxPath = path.join(basePath, entry, '4-outbox')
        if (fs.existsSync(outboxPath)) {
          dirs.push({ space: entry, path: outboxPath })
        }
      }
    }
  } catch { /* ignore */ }
  return dirs
}

function scanOutboxItems(outboxPath) {
  const items = []
  try {
    for (const sub of fs.readdirSync(outboxPath)) {
      const subPath = path.join(outboxPath, sub)
      if (fs.statSync(subPath).isDirectory()) {
        const files = fs.readdirSync(subPath).filter(f => !f.startsWith('.'))
        for (const f of files) {
          items.push({ destination: sub, file: f })
        }
      }
    }
  } catch { /* ignore */ }
  return items
}

// --- Tools ---

export const tools = [
  {
    name: 'pending',
    description: 'List items pending in outbox across spaces',
    inputSchema: z.object({
      space: z.string().optional().describe('Filter by space name'),
    }),
    handler: async (args, ctx) => {
      let outboxDirs = findOutboxDirs(ctx.storage.basePath)
      if (args.space) {
        outboxDirs = outboxDirs.filter(d => d.space === args.space)
      }

      const results = []
      let total = 0
      for (const dir of outboxDirs) {
        const items = scanOutboxItems(dir.path)
        total += items.length
        if (items.length > 0) {
          results.push({ space: dir.space, count: items.length, items })
        }
      }

      return { total, spaces: results }
    },
  },

  {
    name: 'archive_search',
    description: 'Search archived content on server via archive_search.py',
    inputSchema: z.object({
      query: z.string().describe('Search query'),
      limit: z.number().optional().describe('Max results (default: 10)'),
    }),
    handler: async (args, ctx) => {
      const scriptPath = path.join(
        ctx.storage.basePath, '.datacore', 'modules', 'outbox', 'lib', 'archive_search.py'
      )
      if (!fs.existsSync(scriptPath)) {
        return { error: 'archive_search.py not found' }
      }

      try {
        const { stdout } = await execFileAsync('python3', [
          scriptPath, '--query', args.query,
          '--limit', String(args.limit || 10),
          '--json',
        ], {
          cwd: path.join(ctx.storage.basePath, '.datacore', 'modules', 'outbox', 'lib'),
          timeout: 30000,
        })
        return JSON.parse(stdout)
      } catch (err) {
        return { error: `Archive search failed: ${err.message}` }
      }
    },
  },

  {
    name: 'dispose',
    description: 'Permanently delete files with logging. Dry-run by default (preview what would be deleted). Set confirm=true to actually delete.',
    inputSchema: z.object({
      paths: z.array(z.string()).describe('File paths to dispose of'),
      reason: z.string().optional().describe('Reason for disposal'),
      confirm: z.boolean().optional().describe('Set to true to actually delete (default: dry-run preview)'),
    }),
    handler: async (args, ctx) => {
      const scriptPath = path.join(
        ctx.storage.basePath, '.datacore', 'modules', 'outbox', 'lib', 'archive_sync.py'
      )
      if (!fs.existsSync(scriptPath)) {
        return { error: 'archive_sync.py not found' }
      }

      const pathsJson = JSON.stringify(args.paths)
      const reason = (args.reason || '').replace(/'/g, "\\'")
      const confirm = args.confirm ? 'True' : 'False'

      const code = `
import sys, json
sys.path.insert(0, '${path.join(ctx.storage.basePath, '.datacore', 'modules', 'outbox', 'lib')}')
from archive_sync import DisposeHandler
from pathlib import Path

handler = DisposeHandler(Path('${ctx.storage.basePath}'))
result = handler.dispose(${pathsJson}, reason='${reason}', confirm=${confirm})
print(json.dumps(result))
`
      try {
        const { stdout } = await execFileAsync('python3', ['-c', code], {
          cwd: path.join(ctx.storage.basePath, '.datacore', 'modules', 'outbox', 'lib'),
          timeout: 15000,
        })
        return JSON.parse(stdout)
      } catch (err) {
        return { error: `Dispose failed: ${err.message}` }
      }
    },
  },
]
