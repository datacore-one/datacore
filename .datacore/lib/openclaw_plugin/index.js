// Datacore tool gate for OpenClaw (Data's runtime on plur-claw, 2026-09-30).
//
// OpenClaw runs its tools itself, so no Claude hook or Hermes plugin sees a
// call Data makes. This plugin puts the Datacore tool policy in front of every
// tool call: a `before_tool_call` handler at the top priority pipes the event
// to gate.py (tool_policy.evaluate_hook, the same decision as the Claude hook
// and the Hermes plugin) and blocks unless the gate answers a clear
// {"block": false}. A gate that crashes, hangs, is missing or answers anything
// else refuses the call. On the Codex harness, Codex-native tools reach this
// hook through OpenClaw's native PreToolUse relay, which honours a block.
//
// This is a policy check, not an OS sandbox.
//
// Install on a host (the runner checkout, so a `git pull` there updates it):
//   openclaw plugins install --link ~/.datacore/v2-runner/.datacore/lib/openclaw_plugin --force
//   openclaw plugins enable datacore      # and add "datacore" to plugins.allow
//   systemctl --user restart openclaw-gateway
// Config (plugins.entries.datacore.config, all optional): gate, python, timeoutMs.
import { spawn } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_TIMEOUT_MS = 10_000;

function refuse(reason) {
  return { block: true, blockReason: `Datacore tool policy: ${reason}` };
}

export function runGate(event, ctx, cfg) {
  const gate = cfg.gate || join(HERE, "gate.py");
  const python = cfg.python || "python3";
  const timeoutMs = Number(cfg.timeoutMs) > 0 ? Number(cfg.timeoutMs) : DEFAULT_TIMEOUT_MS;
  const request = JSON.stringify({
    toolName: event?.toolName,
    params: event?.params ?? {},
    ...(event?.derivedPaths ? { derivedPaths: event.derivedPaths } : {}),
    ...(event?.toolKind ? { toolKind: event.toolKind } : {}),
  });
  const env = { ...process.env };
  if (ctx?.sessionKey && !env.DATACORE_POLICY_TASK) env.DATACORE_POLICY_TASK = `openclaw:${ctx.sessionKey}`.slice(0, 120);
  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => { if (!done) { done = true; clearTimeout(timer); resolve(value); } };
    let child;
    try {
      child = spawn(python, [gate], { env, stdio: ["pipe", "pipe", "pipe"] });
    } catch (err) {
      finish(refuse(`gate could not start (${err?.code || err?.name || "error"}); call refused`));
      return;
    }
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch { /* already gone */ }
      finish(refuse("gate timed out; call refused"));
    }, timeoutMs);
    let out = "";
    child.stdout.on("data", (d) => { out += d; if (out.length > 65536) out = out.slice(-65536); });
    child.stderr.on("data", () => {});
    child.on("error", (err) => finish(refuse(`gate could not start (${err?.code || "error"}); call refused`)));
    child.on("close", (code) => {
      if (code !== 0) return finish(refuse(`gate exited ${code}; call refused`));
      let answer;
      try {
        answer = JSON.parse(out.trim().split("\n").pop() || "");
      } catch {
        return finish(refuse("gate gave no readable answer; call refused"));
      }
      if (answer && answer.block === false) return finish(undefined);
      if (answer && answer.block === true) {
        return finish({ block: true, blockReason: String(answer.blockReason || "refused by the Datacore tool policy") });
      }
      finish(refuse("gate gave no decision; call refused"));
    });
    child.stdin.on("error", () => {});
    child.stdin.end(request);
  });
}

export default {
  id: "datacore",
  name: "Datacore tool gate",
  description: "Checks every tool call against the Datacore tool policy (tool_effects.yaml, approvals_policy.yaml).",
  configSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      gate: { type: "string" },
      python: { type: "string" },
      timeoutMs: { type: "number" },
    },
  },
  register(api) {
    const cfg = api.pluginConfig || {};
    api.on("before_tool_call", async (event, ctx) => {
      try {
        const result = await runGate(event, ctx, cfg);
        if (result?.block) api.logger?.warn?.(`datacore gate refused ${event?.toolName}: ${result.blockReason}`);
        return result;
      } catch (err) {
        return refuse(`gate failed (${err?.name || "error"}); call refused`);
      }
    }, { priority: 1000, timeoutMs: 14_000 });
    api.logger?.info?.("datacore gate: every tool call is checked against the Datacore tool policy");
  },
};
