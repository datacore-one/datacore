/**
 * goals — app-tools (premium / daemon-wrapped surface).
 *
 * Mirrors `tools/index.js` but routes through the datacore-app daemon.
 * The daemon adds:
 *   - bulk-mutation gate at the user.chat threshold (3/30s)
 *   - safety checkpoint (git snapshot before every mutation)
 *   - synthetic event emission so dashboard panels refresh
 *
 * Discovered by app-mcp-server.mjs from
 *   .datacore/modules/goals/app-tools/index.mjs
 *
 * Each tool gets `ctx.daemonCall(method, path, body)` which sends
 *   X-Datacore-Actor: user.chat
 */

function fmtErr(scope, payload) {
  if (payload?.code) {
    return { content: [{ type: 'text', text: `${scope} failed (${payload.code}): ${payload.message ?? 'unknown error'}` }], isError: true };
  }
  return { content: [{ type: 'text', text: `${scope} failed: ${JSON.stringify(payload).slice(0, 200)}` }], isError: true };
}

const HORIZONS = ['five_year', 'year', 'quarter', 'month', 'week'];
const STATUSES = ['open', 'done', 'abandoned'];

export const tools = [
  {
    name: 'list_goals',
    description: 'List goals from 0-personal/goals.yaml. Optional horizon filter (five_year/year/quarter/month/week).',
    inputSchema: {
      type: 'object',
      properties: {
        horizon: { type: 'string', enum: HORIZONS, description: 'Optional horizon filter' },
      },
    },
    async handler(args, ctx) {
      const qs = args.horizon ? `?horizon=${encodeURIComponent(args.horizon)}` : '';
      const result = await ctx.daemonCall('GET', `/goals${qs}`);
      if (result?.code) return fmtErr('List goals', result);
      const list = result?.goals ?? [];
      if (list.length === 0) return 'No goals found.';
      const lines = list.map(g => `- [${g.id}] (${g.horizon}/${g.status}) ${g.statement}`);
      return lines.join('\n');
    },
  },
  {
    name: 'create_goal',
    description: 'Create a new goal at the given horizon. Optionally link to a parent goal (e.g. quarterly OKR rolls up to annual).',
    inputSchema: {
      type: 'object',
      properties: {
        statement: { type: 'string', description: 'The goal statement (non-empty)' },
        horizon: { type: 'string', enum: HORIZONS },
        parent_id: { type: 'string', description: 'Optional parent goal id (e.g. g-1)' },
        owner: { type: 'string', description: 'Optional owner handle' },
        key_results: { type: 'array', items: { type: 'string' }, description: 'Optional KR list' },
      },
      required: ['statement', 'horizon'],
    },
    async handler(args, ctx) {
      const body = { statement: args.statement, horizon: args.horizon };
      if (args.parent_id) body.parent_id = args.parent_id;
      if (args.owner) body.owner = args.owner;
      if (args.key_results) body.key_results = args.key_results;
      const result = await ctx.daemonCall('POST', '/goals', body);
      if (result?.code) return fmtErr('Create goal', result);
      return `Created goal ${result.goal_id} (${args.horizon}): "${args.statement}".`;
    },
  },
  {
    name: 'update_goal',
    description: 'Edit a goal\'s statement, status, owner, or key results.',
    inputSchema: {
      type: 'object',
      properties: {
        goal_id: { type: 'string' },
        statement: { type: 'string' },
        status: { type: 'string', enum: STATUSES },
        owner: { type: 'string' },
        key_results: { type: 'array', items: { type: 'string' } },
      },
      required: ['goal_id'],
    },
    async handler(args, ctx) {
      const { goal_id, ...patch } = args;
      const result = await ctx.daemonCall('PATCH', `/goals/${encodeURIComponent(goal_id)}`, patch);
      if (result?.code) return fmtErr('Update goal', result);
      const fields = Object.keys(patch).join(', ');
      return `Updated goal ${goal_id}. Fields: ${fields}.`;
    },
  },
  {
    name: 'log_progress',
    description: 'Append a progress note to a goal\'s progress_log. Use this to capture qualitative progress between status changes.',
    inputSchema: {
      type: 'object',
      properties: {
        goal_id: { type: 'string' },
        note: { type: 'string', description: 'Progress note text' },
      },
      required: ['goal_id', 'note'],
    },
    async handler(args, ctx) {
      const result = await ctx.daemonCall('POST', `/goals/${encodeURIComponent(args.goal_id)}/progress`, { note: args.note });
      if (result?.code) return fmtErr('Log progress', result);
      return `Logged progress on ${args.goal_id}.`;
    },
  },
];
