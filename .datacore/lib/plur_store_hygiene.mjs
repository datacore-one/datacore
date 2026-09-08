#!/usr/bin/env node
/**
 * PLUR store hygiene — find and retire engrams that are already dead but still
 * being served.
 *
 * WHY THIS EXISTS
 * ---------------
 * 2026-09-07 audit of a 5,477-engram local store found 74 active engrams that
 * should not have been: 62 carrying `superseded_by` while still status:active,
 * five stacked deck-version snapshots, three explicitly corrected by a later
 * engram, four completed merges/incidents kept as if they were rules. Together
 * ~41,700 tokens of injection budget spent re-teaching things that had already
 * been replaced.
 *
 * Retiring them one at a time through the CLI costs ~11s of cold start each.
 * This does the whole set in one process.
 *
 * CATEGORIES
 *   superseded   relations.superseded_by set, status still active
 *   corrected    a later engram opens "CORRECTION to <id>"; the corrector stays
 *   versioned    "<NAME> deck v<N>" chains — newest kept, older retired
 *   history      completed merge / INCIDENT records that are not rules
 *
 * Pinned engrams are NEVER auto-retired. Pinning is a deliberate always-load
 * decision; if a pinned engram is also dead, that is a judgement call for a
 * human, so it is reported and skipped.
 *
 * Usage:
 *   node plur_store_hygiene.mjs                 # audit only, writes plan JSON
 *   node plur_store_hygiene.mjs --apply         # retire everything in the plan
 *   node plur_store_hygiene.mjs --apply --only superseded,corrected
 *   node plur_store_hygiene.mjs --plan <file>   # apply a reviewed plan file
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const REPO = join(homedir(), 'Data/5-plur/2-projects/plur')
const STORE = join(homedir(), '.plur/engrams.yaml')

const argv = process.argv.slice(2)
const APPLY = argv.includes('--apply')
const onlyArg = argv[argv.indexOf('--only') + 1]
const ONLY = argv.includes('--only') ? new Set(onlyArg.split(',')) : null
const planArg = argv.includes('--plan') ? argv[argv.indexOf('--plan') + 1] : null
const PLAN_OUT = join(homedir(), '.plur/hygiene-plan.json')

function loadEngrams() {
  const YAML = require(join(REPO, 'node_modules/yaml'))
  return YAML.parse(readFileSync(STORE, 'utf8')).engrams
}

function buildPlan(engrams) {
  const byId = new Map(engrams.map(e => [e.id, e]))
  const active = new Set(engrams.filter(e => e.status === 'active').map(e => e.id))
  const plan = new Map()
  const add = (id, category, reason) => {
    if (!active.has(id) || plan.has(id)) return
    if (byId.get(id)?.pinned) return          // never auto-retire a pinned engram
    plan.set(id, { category, reason })
  }

  for (const e of engrams) {
    if (e.status !== 'active') continue
    const sb = e.relations?.superseded_by
    if (sb?.length) add(e.id, 'superseded', `superseded_by ${sb.join(',')}`)
  }

  for (const e of engrams) {
    const m = /^CORRECTION to (ENG-[A-Za-z0-9-]+)/i.exec((e.statement ?? '').trim())
    if (m) add(m[1], 'corrected', `explicitly corrected by ${e.id}, which stays`)
  }

  const chains = new Map()
  for (const e of engrams) {
    if (e.status !== 'active') continue
    const m = /\b([A-Z][A-Za-z]{3,12})\s+(?:deck|one-pager|proposal)\b.*?\bv(\d+)\b/.exec(e.statement ?? '')
    if (!m) continue
    const key = m[1].toUpperCase()
    if (!chains.has(key)) chains.set(key, [])
    chains.get(key).push({ v: Number(m[2]), id: e.id })
  }
  for (const [name, vs] of chains) {
    if (vs.length < 2) continue
    vs.sort((a, b) => a.v - b.v)
    const keep = vs[vs.length - 1]
    for (const v of vs.slice(0, -1)) add(v.id, 'versioned', `${name} v${v.v} — superseded by ${keep.id} (v${keep.v})`)
  }

  for (const e of engrams) {
    if (e.status !== 'active') continue
    if (/\b(merged into .*? on 2026|PR #\d+ .*?merged|^INCIDENT 2026)/i.test(e.statement ?? '')) {
      add(e.id, 'history', 'completed merge/incident — a record, not a rule')
    }
  }
  return plan
}

const engrams = loadEngrams()
const byId = new Map(engrams.map(e => [e.id, e]))

let plan
if (planArg) {
  plan = new Map(Object.entries(JSON.parse(readFileSync(planArg, 'utf8'))))
} else {
  plan = buildPlan(engrams)
  if (ONLY) for (const [id, v] of [...plan]) if (!ONLY.has(v.category)) plan.delete(id)
}

const counts = {}
for (const { category } of plan.values()) counts[category] = (counts[category] ?? 0) + 1
console.log(`store   : ${STORE}`)
console.log(`engrams : ${engrams.length} (${engrams.filter(e => e.status === 'active').length} active)`)
console.log(`plan    : ${plan.size} to retire`, counts)

const skippedPinned = engrams.filter(e =>
  e.status === 'active' && e.pinned &&
  (e.relations?.superseded_by?.length ||
   engrams.some(o => new RegExp(`^CORRECTION to ${e.id}\\b`, 'i').test((o.statement ?? '').trim()))))
if (skippedPinned.length) {
  console.log(`\nPINNED but dead — skipped, decide by hand (${skippedPinned.length}):`)
  for (const e of skippedPinned) console.log(`  ${e.id}  ${(e.statement ?? '').replace(/\s+/g, ' ').slice(0, 90)}`)
}

if (!APPLY) {
  writeFileSync(PLAN_OUT, JSON.stringify(Object.fromEntries(plan), null, 1))
  console.log(`\nAudit only. Plan written to ${PLAN_OUT}`)
  console.log('Re-run with --apply (or --plan <file> after editing) to retire.')
  process.exit(0)
}

const { createPlur } = require(join(REPO, 'packages/cli/dist/plur.js'))
const plur = createPlur({})
let ok = 0
const failed = []
for (const [id, { category, reason }] of plan) {
  try {
    const e = byId.get(id)
    await plur.forget(id, `store hygiene ${new Date().toISOString().slice(0, 10)}: ${reason}`,
      { force: true, ...(e?.scope ? { scope: e.scope } : {}) })
    ok++
    if (ok % 10 === 0) console.log(`  retired ${ok}/${plan.size}...`)
  } catch (err) {
    failed.push([id, category, String(err?.message ?? err)])
  }
}
console.log(`\nretired: ${ok}/${plan.size}`)
if (failed.length) {
  console.log(`failed : ${failed.length}`)
  for (const [id, cat, msg] of failed.slice(0, 15)) console.log(`  ${id} [${cat}] ${msg.slice(0, 110)}`)
}
