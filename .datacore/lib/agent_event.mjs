// agent_event.mjs — DIP-0027 / DIP-0028 compliant emitter for Node-based runtimes
// (OpenClaw plugins, Hermes Node hooks, future Cursor adapter).
//
// No npm dependencies — uses Node stdlib (`fs`, `os`, `path`, `http`).
//
// Usage from an OpenClaw plugin handling outbound TG (or any agent action):
//
//   import { AgentEventEmitter } from './agent_event.mjs';
//   const em = new AgentEventEmitter({
//     agent_id: 'mr-data@plur-claw',
//     runtime: 'openclaw',
//   });
//   await em.tick();
//   await em.taskCompleted({ task_id: 'org-foo', outcome: 'success', tokens_used: 8000 });
//
// The `agent_id` SHOULD be ActivityPub-shaped for peer agents (DIP-0023).
// All emit calls are fail-soft by default — they log a warning on capture
// failure and resolve null. The host process keeps running.

import { promises as fs } from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

const AUTH_DIR = path.join(os.homedir(), '.datacore', 'lens', 'auth');
const TOKEN_PATH = path.join(AUTH_DIR, 'token');
const PORT_PATH = path.join(AUTH_DIR, 'port');
const DEFAULT_HOST = '127.0.0.1';
const DEFAULT_PORT = 51717;
const DEFAULT_TIMEOUT_MS = 2000;

// Mirror of the agent.* disciplined-core schema (DIP-0027 §8). Manually kept
// in sync with .datacore/modules/lens/lib/schema.py — the schema is the
// source of truth, this is a fast-fail client-side check.
const AGENT_REQUIRED_KEYS = {
  'agent.tick': [],
  'agent.session_started': ['session_id'],
  'agent.session_ended': ['session_id'],
  'agent.task_received': ['task_id', 'sender'],
  'agent.task_claimed': ['task_id'],
  'agent.task_completed': ['task_id', 'outcome'],
  'agent.task_failed': ['task_id', 'error_class'],
  'agent.approval_requested': ['request_id', 'action_class'],
  'agent.approval_resolved': ['request_id', 'granted'],
  'agent.message_sent': ['recipient', 'message_class'],
  'agent.message_received': ['sender', 'message_class'],
  'agent.decision': ['decision_id', 'branch'],
  'agent.escalated': ['task_id', 'to'],
  'agent.error': ['error_class'],
};

const VALID_TASK_OUTCOMES = ['success', 'partial', 'nochange'];
const VALID_USED_KINDS = ['cited', 'paraphrased', 'contradicted', 'ignored'];
const VALID_RUNTIMES = ['claude-code', 'openclaw', 'hermes', 'nightshift', 'external'];

export class EmitError extends Error {
  constructor(message) {
    super(message);
    this.name = 'EmitError';
  }
}

async function readTokenAndPort() {
  let token = null;
  let port = DEFAULT_PORT;
  try { token = (await fs.readFile(TOKEN_PATH, 'utf8')).trim() || null; } catch { /* fail-soft */ }
  try {
    const raw = (await fs.readFile(PORT_PATH, 'utf8')).trim();
    const parsed = parseInt(raw, 10);
    if (Number.isFinite(parsed)) port = parsed;
  } catch { /* fail-soft */ }
  return { token, port };
}

function postJson({ host, port, path: urlPath, token, body, timeoutMs }) {
  return new Promise((resolve) => {
    const data = Buffer.from(JSON.stringify(body), 'utf8');
    const req = http.request(
      {
        host,
        port,
        path: urlPath,
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json',
          'Content-Length': data.length.toString(),
        },
        timeout: timeoutMs,
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          try {
            const parsed = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            resolve(parsed);
          } catch {
            resolve(null);
          }
        });
      },
    );
    req.on('error', () => resolve(null));
    req.on('timeout', () => { req.destroy(); resolve(null); });
    req.write(data);
    req.end();
  });
}

export class AgentEventEmitter {
  constructor({ agent_id, runtime, client = null, failSoft = true } = {}) {
    if (!agent_id) throw new EmitError('agent_id required');
    if (!VALID_RUNTIMES.includes(runtime)) {
      throw new EmitError(
        `runtime ${JSON.stringify(runtime)} not in ${JSON.stringify(VALID_RUNTIMES)}`,
      );
    }
    this.agent_id = agent_id;
    this.runtime = runtime;
    this.failSoft = failSoft;
    this._client = client; // optional injectable for tests; null = use HTTP
    this._host = DEFAULT_HOST;
    this._port = null;
    this._token = null;
    this._discovered = false;
  }

  async _discoverAuth() {
    if (this._discovered) return;
    const { token, port } = await readTokenAndPort();
    this._token = token;
    this._port = port;
    this._discovered = true;
  }

  async emit(event_type, { metadata = {}, target_id = null } = {}) {
    const meta = { agent_id: this.agent_id, runtime: this.runtime, ...metadata };

    // Client-side disciplined-core validation
    if (event_type in AGENT_REQUIRED_KEYS) {
      const missing = AGENT_REQUIRED_KEYS[event_type].filter((k) => !(k in meta));
      if (missing.length) {
        const msg = `event ${event_type} missing required metadata: ${JSON.stringify(missing)}`;
        if (!this.failSoft) throw new EmitError(msg);
        console.warn(`[agent_event] ${msg}`);
        return null;
      }
    } else if (event_type.startsWith('agent.')) {
      const msg = `unknown disciplined-core event_type ${event_type} — register in DIP-0027 §8 first`;
      if (!this.failSoft) throw new EmitError(msg);
      console.warn(`[agent_event] ${msg}`);
      return null;
    }

    // Test injection
    if (this._client) {
      return this._client.capture({
        event_type,
        actor: 'agent',
        surface: 'agent',
        target_id,
        metadata: meta,
      });
    }

    await this._discoverAuth();
    if (!this._token) {
      console.warn('[agent_event] no lens auth token — capture skipped');
      return null;
    }

    return postJson({
      host: this._host,
      port: this._port,
      path: '/api/lens/events',
      token: this._token,
      body: {
        events: [{
          event_type,
          actor: 'agent',
          surface: 'agent',
          target_id,
          metadata: meta,
        }],
      },
      timeoutMs: DEFAULT_TIMEOUT_MS,
    });
  }

  // --- lifecycle ---------------------------------------------------------
  tick(extra = {}) { return this.emit('agent.tick', { metadata: extra }); }

  sessionStarted({ session_id, ...extra }) {
    return this.emit('agent.session_started', { metadata: { session_id, ...extra } });
  }

  sessionEnded({ session_id, duration_ms, tokens_used, outcome, ...extra }) {
    const metadata = { session_id, ...extra };
    if (duration_ms != null) metadata.duration_ms = duration_ms;
    if (tokens_used != null) metadata.tokens_used = tokens_used;
    if (outcome != null) metadata.outcome = outcome;
    return this.emit('agent.session_ended', { metadata });
  }

  // --- tasks -------------------------------------------------------------
  taskReceived({ task_id, sender, trust_tier, ...extra }) {
    const metadata = { task_id, sender, ...extra };
    if (trust_tier != null) metadata.trust_tier = trust_tier;
    return this.emit('agent.task_received', { target_id: task_id, metadata });
  }

  taskClaimed({ task_id, ...extra }) {
    return this.emit('agent.task_claimed', { target_id: task_id, metadata: { task_id, ...extra } });
  }

  taskCompleted({ task_id, outcome, tokens_used, cost_usd, ...extra }) {
    if (!VALID_TASK_OUTCOMES.includes(outcome)) {
      const msg = `outcome ${JSON.stringify(outcome)} not in ${JSON.stringify(VALID_TASK_OUTCOMES)}`;
      if (!this.failSoft) throw new EmitError(msg);
      console.warn(`[agent_event] ${msg}`);
      return null;
    }
    const metadata = { task_id, outcome, ...extra };
    if (tokens_used != null) metadata.tokens_used = tokens_used;
    if (cost_usd != null) metadata.cost_usd = cost_usd;
    return this.emit('agent.task_completed', { target_id: task_id, metadata });
  }

  taskFailed({ task_id, error_class, recoverable, ...extra }) {
    const metadata = { task_id, error_class, ...extra };
    if (recoverable != null) metadata.recoverable = recoverable;
    return this.emit('agent.task_failed', { target_id: task_id, metadata });
  }

  // --- approvals ---------------------------------------------------------
  approvalRequested({ request_id, action_class, risk_tier, ...extra }) {
    const metadata = { request_id, action_class, ...extra };
    if (risk_tier != null) metadata.risk_tier = risk_tier;
    return this.emit('agent.approval_requested', { target_id: request_id, metadata });
  }

  approvalResolved({ request_id, granted, decision_latency_ms, ...extra }) {
    const metadata = { request_id, granted, ...extra };
    if (decision_latency_ms != null) metadata.decision_latency_ms = decision_latency_ms;
    return this.emit('agent.approval_resolved', { target_id: request_id, metadata });
  }

  // --- messaging ---------------------------------------------------------
  messageSent({ recipient, message_class, message_id, ...extra }) {
    return this.emit('agent.message_sent', {
      target_id: message_id ?? null,
      metadata: { recipient, message_class, ...extra },
    });
  }

  messageReceived({ sender, message_class, message_id, ...extra }) {
    return this.emit('agent.message_received', {
      target_id: message_id ?? null,
      metadata: { sender, message_class, ...extra },
    });
  }

  // --- reasoning ---------------------------------------------------------
  decision({ decision_id, branch, ...extra }) {
    return this.emit('agent.decision', {
      target_id: decision_id,
      metadata: { decision_id, branch, ...extra },
    });
  }

  escalated({ task_id, to, ...extra }) {
    return this.emit('agent.escalated', {
      target_id: task_id,
      metadata: { task_id, to, ...extra },
    });
  }

  // --- error -------------------------------------------------------------
  error({ error_class, ...extra }) {
    return this.emit('agent.error', { metadata: { error_class, ...extra } });
  }
}

export const agentEventConstants = {
  AGENT_REQUIRED_KEYS,
  VALID_TASK_OUTCOMES,
  VALID_USED_KINDS,
  VALID_RUNTIMES,
};
