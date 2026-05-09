/**
 * gtd — app-tools (premium / daemon-wrapped surface).
 *
 * Daemon-wrapped task mutations callable from the chat sidecar.
 * Each tool routes through datacore-app daemon which adds:
 *   - bulk-mutation gate at user.chat threshold (3/30s)
 *   - safety checkpoint (git snapshot before every mutation)
 *   - synthetic event emission so dashboard panels refresh
 *
 * Discovered by app-mcp-server.mjs from
 *   .datacore/modules/gtd/app-tools/index.mjs
 *
 * Each tool gets ctx.daemonCall(method, path, body) which sends
 * X-Datacore-Actor: user.chat (the actor classification triggers
 * the tighter bulk-mutation threshold).
 */

function fmtErr(scope, payload) {
  if (payload?.code) {
    return {
      content: [
        {
          type: 'text',
          text: `${scope} failed (${payload.code}): ${payload.message ?? 'unknown error'}`,
        },
      ],
      isError: true,
    };
  }
  return {
    content: [
      { type: 'text', text: `${scope} failed: ${JSON.stringify(payload).slice(0, 200)}` },
    ],
    isError: true,
  };
}

export const tools = [
  {
    name: 'add_task',
    description:
      "Add a new task to the active space's inbox. Title is required; tags are optional (use without leading colons — e.g. ['datacore','urgent']). For AI delegation, use delegate_task afterwards. Returns the new task id.",
    inputSchema: {
      type: 'object',
      properties: {
        title: { type: 'string', description: 'Task title (required, non-empty)' },
        tags: {
          type: 'array',
          items: { type: 'string' },
          description: 'Optional tags WITHOUT colons (e.g. ["datacore","urgent"])',
        },
      },
      required: ['title'],
    },
    async handler(args, ctx) {
      const body = { title: args.title };
      if (args.tags && Array.isArray(args.tags) && args.tags.length > 0) {
        body.tags = args.tags;
      }
      const result = await ctx.daemonCall('POST', '/org/capture', body);
      if (result?.code) return fmtErr('Add task', result);
      return `Captured task ${result.task_id}: "${args.title}". It's in the inbox — process_inbox or triage manually to route it.`;
    },
  },
  {
    name: 'mark_task_done',
    description:
      'Mark a task DONE by its id. Use task ids from app_get_tasks or list_next_actions.',
    inputSchema: {
      type: 'object',
      properties: {
        task_id: { type: 'string', description: 'Task id (e.g. "org-abc123")' },
      },
      required: ['task_id'],
    },
    async handler(args, ctx) {
      const result = await ctx.daemonCall(
        'POST',
        `/org/task/${encodeURIComponent(args.task_id)}/state`,
        { state: 'DONE' },
      );
      if (result?.code) return fmtErr('Mark task done', result);
      return `Marked ${args.task_id} DONE.`;
    },
  },
  {
    name: 'set_task_state',
    description:
      'Change a task\'s state. Allowed: TODO, NEXT, WAITING, DONE. Use this for finer-grained transitions than mark_task_done.',
    inputSchema: {
      type: 'object',
      properties: {
        task_id: { type: 'string' },
        state: {
          type: 'string',
          enum: ['TODO', 'NEXT', 'WAITING', 'DONE'],
        },
      },
      required: ['task_id', 'state'],
    },
    async handler(args, ctx) {
      const result = await ctx.daemonCall(
        'POST',
        `/org/task/${encodeURIComponent(args.task_id)}/state`,
        { state: args.state },
      );
      if (result?.code) return fmtErr('Set task state', result);
      return `Set ${args.task_id} → ${args.state}.`;
    },
  },
  {
    name: 'delegate_task',
    description:
      'Delegate a task to AI by adding the :AI: tag. Nightshift will pick it up on the next run (or immediately via nightshift_trigger).',
    inputSchema: {
      type: 'object',
      properties: {
        task_id: { type: 'string', description: 'Task id (e.g. "org-abc123")' },
      },
      required: ['task_id'],
    },
    async handler(args, ctx) {
      const result = await ctx.daemonCall(
        'POST',
        `/org/task/${encodeURIComponent(args.task_id)}/nightshift`,
        {},
      );
      if (result?.code) return fmtErr('Delegate task', result);
      return `Delegated ${args.task_id} to AI (added :AI: tag). Next nightshift cycle will pick it up — or call nightshift_trigger to run now.`;
    },
  },
  // Note: to process the inbox, invoke the existing /process-inbox slash command
  // via the Skill tool. We don't expose a duplicate daemon endpoint for it because
  // the inbox-coordinator agent needs Claude Code dispatch, not a daemon subprocess.
];
