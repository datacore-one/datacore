// Comms Module — MCP Tool Definitions
// Reads engagement state for pipeline status queries.
// Plain JS (ESM) for direct dynamic import by the MCP server.

import { z } from 'zod'
import * as fs from 'fs'
import * as path from 'path'

function stateFilePath(basePath) {
  return path.join(basePath, '.datacore', 'state', 'engagement-state.json')
}

function loadState(basePath) {
  const fp = stateFilePath(basePath)
  if (!fs.existsSync(fp)) return null
  try {
    return JSON.parse(fs.readFileSync(fp, 'utf-8'))
  } catch {
    return null
  }
}

function todayKey() {
  return new Date().toISOString().slice(0, 10)
}

export const tools = [
  {
    name: 'engagement_status',
    description: 'Engagement pipeline status — pending approvals, posted today, discovery stats, daily limits',
    inputSchema: z.object({}),
    handler: async (args, ctx) => {
      const state = loadState(ctx.storage.basePath)
      if (!state) return { status: 'not_initialized', message: 'No engagement state found. Run the engine first.' }

      const today = todayKey()
      const stats = (state.daily_stats || {})[today] || {}
      const config = state.config || {}

      return {
        pending: (state.pending || []).length,
        posted_today: stats.posted || 0,
        drafted_today: stats.drafted || 0,
        rejected_today: stats.rejected || 0,
        expired_today: stats.expired || 0,
        limits: {
          max_per_hour: config.max_per_hour || 5,
          max_per_day: config.max_per_day || 15,
        },
        total_posted: (state.posted || []).length,
        total_seen: Object.keys(state.seen || {}).length,
        last_run: state.last_run || 'never',
      }
    },
  },

  {
    name: 'engagement_queue',
    description: 'List pending engagement reply drafts awaiting Telegram approval',
    inputSchema: z.object({
      limit: z.number().optional().describe('Max items to return (default: 10)'),
    }),
    handler: async (args, ctx) => {
      const state = loadState(ctx.storage.basePath)
      if (!state) return { pending: [], message: 'No engagement state found.' }

      const limit = args.limit || 10
      const pending = (state.pending || []).slice(0, limit)

      return {
        count: pending.length,
        total: (state.pending || []).length,
        drafts: pending.map(p => ({
          id: p.id,
          target_author: p.target_author,
          target_snippet: p.target_content_snippet,
          draft_reply: p.draft_reply,
          discovered_at: p.discovered_at,
          expires_at: p.expires_at,
          chars: p.draft_reply?.length || 0,
        })),
      }
    },
  },

  {
    name: 'engagement_history',
    description: 'Recent posted engagement replies with target info and timestamps',
    inputSchema: z.object({
      days: z.number().optional().describe('Lookback period in days (default: 7)'),
      limit: z.number().optional().describe('Max results (default: 20)'),
    }),
    handler: async (args, ctx) => {
      const state = loadState(ctx.storage.basePath)
      if (!state) return { posted: [], message: 'No engagement state found.' }

      const days = args.days || 7
      const limit = args.limit || 20
      const cutoff = new Date(Date.now() - days * 86400000).toISOString()

      const posted = (state.posted || [])
        .filter(p => p.posted_at >= cutoff)
        .reverse()
        .slice(0, limit)

      // Aggregate daily stats for the period
      const dailyStats = {}
      for (const [date, stats] of Object.entries(state.daily_stats || {})) {
        if (date >= cutoff.slice(0, 10)) {
          dailyStats[date] = stats
        }
      }

      return {
        count: posted.length,
        replies: posted.map(p => ({
          our_tweet_id: p.our_tweet_id,
          our_url: `https://x.com/FairDataSociety/status/${p.our_tweet_id}`,
          target_author: p.target_author,
          reply_text: p.draft_reply,
          posted_at: p.posted_at,
        })),
        daily_stats: dailyStats,
      }
    },
  },
]
