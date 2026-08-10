/**
 * decisions — app-tools (premium / daemon-wrapped surface).
 *
 * Mirrors `tools/index.js` but routes through the datacore-app daemon.
 * Decisions are bound to the active space (the daemon resolves
 * `[space]/3-knowledge/decisions/`). Switch the active space via
 * `app_set_space` if you want to write a decision elsewhere.
 *
 * Discovered by app-mcp-server.mjs from
 *   .datacore/modules/decisions/app-tools/index.mjs
 */

function fmtErr(scope, payload) {
  if (payload?.code) {
    return { content: [{ type: 'text', text: `${scope} failed (${payload.code}): ${payload.message ?? 'unknown error'}` }], isError: true };
  }
  return { content: [{ type: 'text', text: `${scope} failed: ${JSON.stringify(payload).slice(0, 200)}` }], isError: true };
}

const STATUSES = ['proposed', 'accepted', 'superseded', 'deprecated'];

export const tools = [
  {
    name: 'list_decisions',
    description: 'List decisions in the currently active space (sorted most-recent-first). Optional status filter.',
    inputSchema: {
      type: 'object',
      properties: {
        status: { type: 'string', enum: STATUSES },
      },
    },
    async handler(args, ctx) {
      const qs = args.status ? `?status=${encodeURIComponent(args.status)}` : '';
      const result = await ctx.daemonCall('GET', `/decisions${qs}`);
      if (result?.code) return fmtErr('List decisions', result);
      const rows = result?.decisions ?? [];
      if (rows.length === 0) return 'No decisions found in the active space.';
      const lines = rows.map(r => `- [${r.status}] ${r.date} — ${r.title} (${r.id})`);
      return lines.join('\n');
    },
  },
  {
    name: 'read_decision',
    description: 'Read a decision\'s full body (markdown). The id is the slug like "2026-05-04-archive-meridian".',
    inputSchema: {
      type: 'object',
      properties: {
        decision_id: { type: 'string' },
      },
      required: ['decision_id'],
    },
    async handler(args, ctx) {
      const result = await ctx.daemonCall('GET', `/decisions/${encodeURIComponent(args.decision_id)}`);
      if (result?.code) return fmtErr('Read decision', result);
      return `# ${result.title} (${result.status})\n\n${result.body ?? ''}`;
    },
  },
  {
    name: 'create_decision',
    description: 'Log a new decision in MADR-ish format under the active space\'s 3-knowledge/decisions/. Creates YYYY-MM-DD-<slug>.md with status=proposed.',
    inputSchema: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        context: { type: 'string', description: 'Why this decision is needed' },
        decision: { type: 'string', description: 'What was decided' },
        consequences: { type: 'string', description: 'Resulting tradeoffs and follow-ups' },
      },
      required: ['title'],
    },
    async handler(args, ctx) {
      const body = { title: args.title };
      if (args.context) body.context = args.context;
      if (args.decision) body.decision = args.decision;
      if (args.consequences) body.consequences = args.consequences;
      const result = await ctx.daemonCall('POST', '/decisions', body);
      if (result?.code) return fmtErr('Create decision', result);
      return `Logged decision ${result.decision_id}: "${args.title}" (proposed).`;
    },
  },
  {
    name: 'update_status',
    description: 'Promote or deprecate a decision (proposed → accepted → superseded/deprecated).',
    inputSchema: {
      type: 'object',
      properties: {
        decision_id: { type: 'string' },
        status: { type: 'string', enum: STATUSES },
      },
      required: ['decision_id', 'status'],
    },
    async handler(args, ctx) {
      const result = await ctx.daemonCall('PATCH', `/decisions/${encodeURIComponent(args.decision_id)}/status`, { status: args.status });
      if (result?.code) return fmtErr('Update decision status', result);
      return `Decision ${args.decision_id} → ${args.status}.`;
    },
  },
];
