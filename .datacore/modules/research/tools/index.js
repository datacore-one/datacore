// Research Module — MCP Tool Definitions
// Reads research_learning.org to report queue status and source management.
// Plain JS (ESM) for direct dynamic import by the MCP server.

import { z } from 'zod'
import * as fs from 'fs'
import * as path from 'path'
import { execSync } from 'child_process'

// --- Helpers ---

function findResearchOrg(basePath) {
  // Check settings-specified location, fall back to default
  const defaultPath = path.join(basePath, '0-personal', 'org', 'research_learning.org')
  if (fs.existsSync(defaultPath)) return defaultPath
  return null
}

function parseResearchEntries(content) {
  const lines = content.split('\n')
  const entries = []
  for (let i = 0; i < lines.length; i++) {
    const m = lines[i].match(/^(\*+)\s+(TODO|NEXT|DONE|WAITING)\s+(.+?)(\s+:[:\w]+:)?\s*$/)
    if (m) {
      const tags = (m[4] || '').trim()
      // Check for URL in body
      let url = null
      for (let j = i + 1; j < Math.min(i + 10, lines.length); j++) {
        if (lines[j].match(/^(\*+)\s/)) break
        const urlMatch = lines[j].match(/https?:\/\/\S+/)
        if (urlMatch) { url = urlMatch[0]; break }
      }
      entries.push({
        state: m[2],
        title: m[3].trim(),
        tags,
        url,
        line: i + 1,
      })
    }
  }
  return entries
}

// --- Tools ---

export const tools = [
  {
    name: 'queue',
    description: 'List pending research links and their processing status',
    inputSchema: z.object({
      state: z.enum(['TODO', 'NEXT', 'DONE', 'WAITING', 'all']).optional()
        .describe('Filter by state (default: pending only)'),
    }),
    handler: async (args, ctx) => {
      const orgPath = findResearchOrg(ctx.storage.basePath)
      if (!orgPath) return { error: 'research_learning.org not found' }

      const content = fs.readFileSync(orgPath, 'utf-8')
      let entries = parseResearchEntries(content)

      if (!args.state || args.state !== 'all') {
        const filterState = args.state || null
        if (filterState) {
          entries = entries.filter(e => e.state === filterState)
        } else {
          entries = entries.filter(e => ['TODO', 'NEXT'].includes(e.state))
        }
      }

      const byState = {}
      for (const e of entries) {
        byState[e.state] = (byState[e.state] || 0) + 1
      }

      return {
        total: entries.length,
        by_state: byState,
        entries: entries.slice(0, 30),
      }
    },
  },

  {
    name: 'sources',
    description: 'Query the research source registry for configured providers',
    inputSchema: z.object({
      query: z.string().optional().describe('Search sources by name'),
    }),
    handler: async (args, ctx) => {
      const registryPath = path.join(ctx.storage.basePath, '.datacore', 'registry', 'sources.yaml')
      if (!fs.existsSync(registryPath)) {
        return { sources: [], message: 'No source registry found at .datacore/registry/sources.yaml' }
      }

      const content = fs.readFileSync(registryPath, 'utf-8')
      // Simple YAML parser for nested mapping format:
      //   sources:
      //     datacortex:
      //       type: internal
      const sources = []
      let current = null
      let inSources = false
      for (const line of content.split('\n')) {
        if (line.match(/^sources:\s*$/)) { inSources = true; continue }
        if (!inSources) continue
        if (line.match(/^[^\s#]/) && !line.match(/^sources:/)) break
        // Top-level source key (2-space indent, no dash)
        const nameMatch = line.match(/^  ([a-zA-Z][\w-]*):\s*$/)
        if (nameMatch) {
          if (current) sources.push(current)
          current = { name: nameMatch[1] }
          continue
        }
        // Property (4-space indent)
        if (current) {
          const kvMatch = line.match(/^\s{4}([\w_]+):\s*(.+)/)
          if (kvMatch) {
            let val = kvMatch[2].trim()
            if (val === 'true') val = true
            else if (val === 'false') val = false
            current[kvMatch[1]] = val
          }
        }
      }
      if (current) sources.push(current)
      // Filter disabled sources
      const activeSources = sources.filter(s => s.enabled !== false)

      let filtered = activeSources
      if (args.query) {
        const q = args.query.toLowerCase()
        filtered = activeSources.filter(s => s.name.toLowerCase().includes(q) || (s.description && s.description.toLowerCase().includes(q)))
      }

      return { count: filtered.length, sources: filtered }
    },
  },

  {
    name: 'transcribe_youtube',
    description: 'Extract transcript and metadata from a YouTube video or playlist URL. Returns structured JSON with title, channel, duration, transcript text, timestamps, and chapters.',
    inputSchema: z.object({
      url: z.string().describe('YouTube video or playlist URL'),
    }),
    handler: async (args, ctx) => {
      const scriptPath = path.join(ctx.storage.basePath, '.datacore', 'lib', 'youtube_transcript.py')
      if (!fs.existsSync(scriptPath)) {
        return { error: 'youtube_transcript.py not found at .datacore/lib/' }
      }

      try {
        const result = execSync(
          `python3 "${scriptPath}" --url "${args.url}"`,
          { encoding: 'utf-8', timeout: 120_000, maxBuffer: 10 * 1024 * 1024 }
        )
        return JSON.parse(result)
      } catch (err) {
        const stderr = err.stderr || ''
        const stdout = err.stdout || ''
        return {
          error: 'Transcript extraction failed',
          details: stderr.trim() || stdout.trim() || err.message,
        }
      }
    },
  },
]
