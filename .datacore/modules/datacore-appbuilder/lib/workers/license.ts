/**
 * License Server — Cloudflare Worker
 *
 * Endpoints:
 *   POST /webhook    — Payment provider webhook (creates license)
 *   POST /verify     — App calls on launch (validates license)
 *   POST /activate   — Exchange purchase token for license
 *
 * D1 table: licenses
 *   id TEXT PRIMARY KEY,
 *   app_id TEXT NOT NULL,
 *   email TEXT NOT NULL,
 *   signature TEXT NOT NULL,
 *   created_at INTEGER NOT NULL,
 *   revoked INTEGER DEFAULT 0
 *
 * Environment bindings:
 *   DB: D1Database
 *   SIGNING_KEY: string (Ed25519 private key, base64)
 *   WEBHOOK_SECRET: string (payment provider webhook secret)
 */

export interface Env {
  DB: D1Database
  SIGNING_KEY: string
  WEBHOOK_SECRET: string
}

interface LicenseRow {
  id: string
  app_id: string
  email: string
  signature: string
  created_at: number
  revoked: number
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)

    if (request.method !== "POST") {
      return json({ error: "Method not allowed" }, 405)
    }

    try {
      switch (url.pathname) {
        case "/webhook":
          return handleWebhook(request, env)
        case "/verify":
          return handleVerify(request, env)
        case "/activate":
          return handleActivate(request, env)
        default:
          return json({ error: "Not found" }, 404)
      }
    } catch (err) {
      console.error("Worker error:", err)
      return json({ error: "Internal error" }, 500)
    }
  },
}

/**
 * Payment webhook — creates a license record.
 * Supports LemonSqueezy and Gumroad webhook formats.
 */
async function handleWebhook(request: Request, env: Env): Promise<Response> {
  const signature = request.headers.get("x-signature") ?? request.headers.get("x-lemon-signature") ?? ""
  const body = await request.text()

  if (!await verifyWebhookSignature(body, signature, env.WEBHOOK_SECRET)) {
    return json({ error: "Invalid signature" }, 401)
  }

  const payload = JSON.parse(body)

  // Extract email + app_id from webhook payload
  // Adapt these fields based on your payment provider
  const email = payload.data?.attributes?.user_email
    ?? payload.email
    ?? payload.purchaser?.email
  const appId = payload.data?.attributes?.product_name
    ?? payload.product_name
    ?? "com.datacore.megaphone"

  if (!email) {
    return json({ error: "No email in webhook payload" }, 400)
  }

  const id = crypto.randomUUID()
  const createdAt = Date.now()
  const licenseData = JSON.stringify({ app_id: appId, email, timestamp: createdAt })
  const sig = await sign(licenseData, env.SIGNING_KEY)

  await env.DB.prepare(
    "INSERT INTO licenses (id, app_id, email, signature, created_at) VALUES (?, ?, ?, ?, ?)"
  ).bind(id, appId, email, sig, createdAt).run()

  return json({ ok: true, license_id: id })
}

/**
 * Verify — app calls on launch with its license file contents.
 * Returns { valid: true/false, email, app_id }.
 */
async function handleVerify(request: Request, env: Env): Promise<Response> {
  const { app_id, email, timestamp, signature } = await request.json<{
    app_id: string; email: string; timestamp: number; signature: string
  }>()

  if (!app_id || !email || !signature) {
    return json({ valid: false, reason: "Missing fields" })
  }

  // Verify signature
  const licenseData = JSON.stringify({ app_id, email, timestamp })
  const valid = await verify(licenseData, signature, env.SIGNING_KEY)

  if (!valid) {
    return json({ valid: false, reason: "Invalid signature" })
  }

  // Check revocation
  const row = await env.DB.prepare(
    "SELECT revoked FROM licenses WHERE email = ? AND app_id = ? AND signature = ?"
  ).bind(email, app_id, signature).first<LicenseRow>()

  if (row?.revoked) {
    return json({ valid: false, reason: "License revoked" })
  }

  return json({ valid: true, email, app_id })
}

/**
 * Activate — exchange email + purchase token for signed license JSON.
 * The app stores this locally as .license file.
 */
async function handleActivate(request: Request, env: Env): Promise<Response> {
  const { email, app_id } = await request.json<{ email: string; app_id: string }>()

  if (!email || !app_id) {
    return json({ error: "Missing email or app_id" }, 400)
  }

  const row = await env.DB.prepare(
    "SELECT * FROM licenses WHERE email = ? AND app_id = ? AND revoked = 0 ORDER BY created_at DESC LIMIT 1"
  ).bind(email, app_id).first<LicenseRow>()

  if (!row) {
    return json({ error: "No license found for this email" }, 404)
  }

  return json({
    app_id: row.app_id,
    email: row.email,
    timestamp: row.created_at,
    signature: row.signature,
  })
}

// --- Crypto helpers ---

async function sign(data: string, keyBase64: string): Promise<string> {
  const keyBytes = base64ToBytes(keyBase64)
  const key = await crypto.subtle.importKey(
    "pkcs8", keyBytes, { name: "Ed25519" }, false, ["sign"]
  )
  const sig = await crypto.subtle.sign("Ed25519", key, new TextEncoder().encode(data))
  return bytesToBase64(new Uint8Array(sig))
}

async function verify(data: string, sigBase64: string, keyBase64: string): Promise<boolean> {
  try {
    // Derive public key from private for verification
    // In production, store the public key separately
    const keyBytes = base64ToBytes(keyBase64)
    const privateKey = await crypto.subtle.importKey(
      "pkcs8", keyBytes, { name: "Ed25519" }, true, ["sign"]
    )
    const exported = await crypto.subtle.exportKey("jwk", privateKey)
    const publicKey = await crypto.subtle.importKey(
      "jwk", { ...exported, d: undefined, key_ops: ["verify"] },
      { name: "Ed25519" }, false, ["verify"]
    )
    const sigBytes = base64ToBytes(sigBase64)
    return crypto.subtle.verify("Ed25519", publicKey, sigBytes, new TextEncoder().encode(data))
  } catch {
    return false
  }
}

async function verifyWebhookSignature(body: string, signature: string, secret: string): Promise<boolean> {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" }, false, ["verify"]
  )
  const sigBytes = hexToBytes(signature)
  return crypto.subtle.verify("HMAC", key, sigBytes, new TextEncoder().encode(body))
}

function base64ToBytes(b64: string): Uint8Array {
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
}

function bytesToBase64(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
}

function hexToBytes(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2)
  for (let i = 0; i < hex.length; i += 2) {
    bytes[i / 2] = parseInt(hex.substring(i, i + 2), 16)
  }
  return bytes
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}
