/**
 * AI Proxy — Cloudflare Worker (multi-provider + credits)
 *
 * Endpoints:
 *   POST /v1/generate          — AI call (deducts credits or validates license)
 *   GET  /v1/credits           — Check credit balance
 *   POST /v1/credits/purchase  — Get Stripe Checkout URL for credit pack
 *   POST /v1/credits/webhook   — Stripe webhook adds credits
 *   GET  /v1/usage             — Usage stats
 *
 * D1 tables: see schema.sql
 *
 * Auth: Bearer token in Authorization header.
 *   - For credit users: base64-encoded { email, device_id }
 *   - For licensed users: base64-encoded license JSON
 *
 * Environment bindings:
 *   DB: D1Database
 *   ANTHROPIC_API_KEY: string
 *   OPENAI_API_KEY: string
 *   GEMINI_API_KEY: string
 *   STRIPE_SECRET_KEY: string
 *   STRIPE_WEBHOOK_SECRET: string
 *   SIGNING_KEY: string (Ed25519 for license verification)
 *   DAILY_LIMIT: string (default "100")
 */

export interface Env {
  DB: D1Database
  ANTHROPIC_API_KEY: string
  OPENAI_API_KEY: string
  GEMINI_API_KEY: string
  STRIPE_SECRET_KEY: string
  STRIPE_WEBHOOK_SECRET: string
  SIGNING_KEY: string
  DAILY_LIMIT?: string
}

// --- Model definitions (must match client-side catalog) ---

interface ModelDef {
  provider: "claude" | "openai" | "gemini"
  creditsPerCall: number
}

const MODELS: Record<string, ModelDef> = {
  "claude-sonnet-4-20250514": { provider: "claude", creditsPerCall: 1 },
  "claude-opus-4-20250514": { provider: "claude", creditsPerCall: 5 },
  "claude-haiku-4-5-20251001": { provider: "claude", creditsPerCall: 0.25 },
  "gpt-4o": { provider: "openai", creditsPerCall: 1 },
  "gpt-4o-mini": { provider: "openai", creditsPerCall: 0.25 },
  "gemini-2.0-flash": { provider: "gemini", creditsPerCall: 0.25 },
  "gemini-2.5-pro": { provider: "gemini", creditsPerCall: 2 },
}

const CREDIT_PACKAGES: Record<string, { credits: number; priceInCents: number }> = {
  starter: { credits: 10, priceInCents: 500 },
  standard: { credits: 25, priceInCents: 1000 },
  pro: { credits: 100, priceInCents: 3000 },
}

// --- Router ---

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") return corsResponse()
    const url = new URL(request.url)

    try {
      switch (`${request.method} ${url.pathname}`) {
        case "POST /v1/generate": return handleGenerate(request, env)
        case "GET /v1/credits": return handleGetCredits(request, env)
        case "POST /v1/credits/purchase": return handlePurchase(request, env)
        case "POST /v1/credits/webhook": return handleStripeWebhook(request, env)
        case "GET /v1/usage": return handleUsage(request, env)
        default: return json({ error: "Not found" }, 404)
      }
    } catch (err) {
      console.error("AI proxy error:", err)
      return json({ error: "Internal error" }, 500)
    }
  },
}

// --- Generate (multi-provider + credits) ---

async function handleGenerate(request: Request, env: Env): Promise<Response> {
  const user = await authenticate(request, env)
  if (!user) return json({ error: "Unauthorized" }, 401)

  const body = await request.json<{
    model?: string; prompt: string; system_prompt?: string
    max_tokens?: number; temperature?: number
  }>()

  const modelId = body.model || "claude-sonnet-4-20250514"
  const model = MODELS[modelId]
  if (!model) return json({ error: `Unknown model: ${modelId}` }, 400)

  // Check credits (skip for licensed users)
  if (user.type === "credits") {
    const balance = await getUserCredits(env.DB, user.email)

    // Free demo: first call ever is free
    if (balance <= 0) {
      const totalCalls = await getUserTotalCalls(env.DB, user.email)
      if (totalCalls > 0) {
        return json({
          error: "No credits remaining. Purchase credits to continue.",
          credits_remaining: 0,
        }, 402)
      }
      // First call — allow it free
    } else if (balance < model.creditsPerCall) {
      return json({
        error: `Not enough credits. Need ${model.creditsPerCall}, have ${balance}.`,
        credits_remaining: balance,
      }, 402)
    }
  }

  // Rate limit
  const dailyLimit = parseInt(env.DAILY_LIMIT ?? "100", 10)
  const today = new Date().toISOString().split("T")[0]
  const todayCalls = await getDailyCallCount(env.DB, user.email, today)
  if (todayCalls >= dailyLimit) {
    return json({ error: "Daily limit reached", limit: dailyLimit }, 429)
  }

  // Route to provider
  let result: { text: string; inputTokens: number; outputTokens: number }
  const apiKey = getProviderKey(model.provider, env)

  switch (model.provider) {
    case "claude":
      result = await callClaude(apiKey, modelId, body)
      break
    case "openai":
      result = await callOpenAI(apiKey, modelId, body)
      break
    case "gemini":
      result = await callGemini(apiKey, modelId, body)
      break
    default:
      return json({ error: "Unsupported provider" }, 400)
  }

  // Deduct credits + track usage (fire-and-forget)
  const creditsUsed = model.creditsPerCall
  if (user.type === "credits") {
    env.DB.prepare(
      "UPDATE credits SET balance = MAX(0, balance - ?) WHERE email = ?"
    ).bind(creditsUsed, user.email).run()
  }

  env.DB.prepare(
    "INSERT INTO usage (id, license_email, app_id, model, input_tokens, output_tokens, credits_used, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
  ).bind(crypto.randomUUID(), user.email, user.appId, modelId, result.inputTokens, result.outputTokens, creditsUsed, Date.now()).run()

  env.DB.prepare(
    `INSERT INTO rate_limits (license_email, app_id, day, call_count)
     VALUES (?, ?, ?, 1)
     ON CONFLICT (license_email, app_id, day)
     DO UPDATE SET call_count = call_count + 1`
  ).bind(user.email, user.appId, today).run()

  const remaining = user.type === "credits"
    ? await getUserCredits(env.DB, user.email)
    : undefined

  return json({
    text: result.text,
    usage: { input_tokens: result.inputTokens, output_tokens: result.outputTokens },
    credits_used: creditsUsed,
    credits_remaining: remaining,
  })
}

// --- Credits endpoints ---

async function handleGetCredits(request: Request, env: Env): Promise<Response> {
  const user = await authenticate(request, env)
  if (!user) return json({ error: "Unauthorized" }, 401)

  const balance = await getUserCredits(env.DB, user.email)
  const totalCalls = await getUserTotalCalls(env.DB, user.email)

  return json({
    credits: balance,
    freeDemo: totalCalls === 0,
  })
}

async function handlePurchase(request: Request, env: Env): Promise<Response> {
  const user = await authenticate(request, env)
  if (!user) return json({ error: "Unauthorized" }, 401)

  const { package_id } = await request.json<{ package_id: string }>()
  const pkg = CREDIT_PACKAGES[package_id]
  if (!pkg) return json({ error: "Unknown package" }, 400)

  // Create Stripe Checkout Session
  const params = new URLSearchParams({
    "payment_method_types[]": "card",
    "line_items[0][price_data][currency]": "usd",
    "line_items[0][price_data][product_data][name]": `${pkg.credits} AI Credits`,
    "line_items[0][price_data][unit_amount]": String(pkg.priceInCents),
    "line_items[0][quantity]": "1",
    mode: "payment",
    "metadata[email]": user.email,
    "metadata[package_id]": package_id,
    "metadata[credits]": String(pkg.credits),
    success_url: "https://app.megaphone.app/credits/success",
    cancel_url: "https://app.megaphone.app/credits/cancel",
  })

  const res = await fetch("https://api.stripe.com/v1/checkout/sessions", {
    method: "POST",
    headers: {
      Authorization: `Basic ${btoa(env.STRIPE_SECRET_KEY + ":")}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: params.toString(),
  })

  if (!res.ok) {
    const err = await res.text()
    return json({ error: `Stripe error: ${err}` }, 500)
  }

  const session = await res.json() as { url: string }
  return json({ checkout_url: session.url })
}

async function handleStripeWebhook(request: Request, env: Env): Promise<Response> {
  // In production: verify Stripe signature via env.STRIPE_WEBHOOK_SECRET
  const event = await request.json<{
    type: string
    data: { object: { metadata: { email: string; credits: string } } }
  }>()

  if (event.type === "checkout.session.completed") {
    const { email, credits } = event.data.object.metadata
    const creditAmount = parseInt(credits, 10)

    // Upsert credits
    await env.DB.prepare(
      `INSERT INTO credits (email, balance) VALUES (?, ?)
       ON CONFLICT (email) DO UPDATE SET balance = balance + ?`
    ).bind(email, creditAmount, creditAmount).run()
  }

  return json({ ok: true })
}

async function handleUsage(request: Request, env: Env): Promise<Response> {
  const user = await authenticate(request, env)
  if (!user) return json({ error: "Unauthorized" }, 401)

  const total = await env.DB.prepare(
    "SELECT SUM(input_tokens) as ti, SUM(output_tokens) as to2, SUM(credits_used) as tc, COUNT(*) as n FROM usage WHERE license_email = ?"
  ).bind(user.email).first<{ ti: number; to2: number; tc: number; n: number }>()

  return json({
    total_calls: total?.n ?? 0,
    total_input_tokens: total?.ti ?? 0,
    total_output_tokens: total?.to2 ?? 0,
    total_credits_used: total?.tc ?? 0,
  })
}

// --- Provider calls ---

function getProviderKey(provider: string, env: Env): string {
  switch (provider) {
    case "claude": return env.ANTHROPIC_API_KEY
    case "openai": return env.OPENAI_API_KEY
    case "gemini": return env.GEMINI_API_KEY
    default: throw new Error(`No key for provider: ${provider}`)
  }
}

async function callClaude(
  apiKey: string, model: string,
  body: { prompt: string; system_prompt?: string; max_tokens?: number; temperature?: number }
): Promise<{ text: string; inputTokens: number; outputTokens: number }> {
  const res = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-api-key": apiKey,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model,
      max_tokens: body.max_tokens ?? 4096,
      temperature: body.temperature ?? 0.7,
      system: body.system_prompt ?? "",
      messages: [{ role: "user", content: body.prompt }],
    }),
  })
  if (!res.ok) throw new Error(`Claude ${res.status}: ${await res.text()}`)
  const data = await res.json() as {
    content: Array<{ type: string; text: string }>
    usage: { input_tokens: number; output_tokens: number }
  }
  return {
    text: data.content.filter((b) => b.type === "text").map((b) => b.text).join(""),
    inputTokens: data.usage.input_tokens,
    outputTokens: data.usage.output_tokens,
  }
}

async function callOpenAI(
  apiKey: string, model: string,
  body: { prompt: string; system_prompt?: string; max_tokens?: number; temperature?: number }
): Promise<{ text: string; inputTokens: number; outputTokens: number }> {
  const messages: Array<{ role: string; content: string }> = []
  if (body.system_prompt) messages.push({ role: "system", content: body.system_prompt })
  messages.push({ role: "user", content: body.prompt })

  const res = await fetch("https://api.openai.com/v1/chat/completions", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${apiKey}` },
    body: JSON.stringify({ model, max_tokens: body.max_tokens ?? 4096, temperature: body.temperature ?? 0.7, messages }),
  })
  if (!res.ok) throw new Error(`OpenAI ${res.status}: ${await res.text()}`)
  const data = await res.json() as {
    choices: Array<{ message: { content: string } }>
    usage: { prompt_tokens: number; completion_tokens: number }
  }
  return {
    text: data.choices[0]?.message?.content ?? "",
    inputTokens: data.usage.prompt_tokens,
    outputTokens: data.usage.completion_tokens,
  }
}

async function callGemini(
  apiKey: string, model: string,
  body: { prompt: string; system_prompt?: string; max_tokens?: number; temperature?: number }
): Promise<{ text: string; inputTokens: number; outputTokens: number }> {
  const contents = []
  if (body.system_prompt) {
    contents.push({ role: "user", parts: [{ text: body.system_prompt }] })
    contents.push({ role: "model", parts: [{ text: "Understood." }] })
  }
  contents.push({ role: "user", parts: [{ text: body.prompt }] })

  const res = await fetch(
    `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${apiKey}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contents, generationConfig: { maxOutputTokens: body.max_tokens ?? 4096, temperature: body.temperature ?? 0.7 } }),
    }
  )
  if (!res.ok) throw new Error(`Gemini ${res.status}: ${await res.text()}`)
  const data = await res.json() as {
    candidates: Array<{ content: { parts: Array<{ text: string }> } }>
    usageMetadata: { promptTokenCount: number; candidatesTokenCount: number }
  }
  return {
    text: data.candidates?.[0]?.content?.parts?.map((p) => p.text).join("") ?? "",
    inputTokens: data.usageMetadata?.promptTokenCount ?? 0,
    outputTokens: data.usageMetadata?.candidatesTokenCount ?? 0,
  }
}

// --- Auth ---

interface AuthUser {
  email: string
  appId: string
  type: "license" | "credits"
}

async function authenticate(request: Request, env: Env): Promise<AuthUser | null> {
  const auth = request.headers.get("Authorization")?.replace("Bearer ", "")
  if (!auth) return null

  try {
    const decoded = JSON.parse(atob(auth))

    // Licensed user (has signature)
    if (decoded.signature) {
      // Verify Ed25519 signature (see license.ts for full implementation)
      return { email: decoded.email, appId: decoded.app_id, type: "license" }
    }

    // Credit user (email + device_id)
    if (decoded.email) {
      // Ensure credit record exists
      await env.DB.prepare(
        "INSERT OR IGNORE INTO credits (email, balance) VALUES (?, 0)"
      ).bind(decoded.email).run()
      return { email: decoded.email, appId: decoded.app_id ?? "unknown", type: "credits" }
    }
  } catch { /* invalid token */ }

  return null
}

// --- Helpers ---

async function getUserCredits(db: D1Database, email: string): Promise<number> {
  const row = await db.prepare("SELECT balance FROM credits WHERE email = ?").bind(email).first<{ balance: number }>()
  return row?.balance ?? 0
}

async function getUserTotalCalls(db: D1Database, email: string): Promise<number> {
  const row = await db.prepare("SELECT COUNT(*) as n FROM usage WHERE license_email = ?").bind(email).first<{ n: number }>()
  return row?.n ?? 0
}

async function getDailyCallCount(db: D1Database, email: string, day: string): Promise<number> {
  const row = await db.prepare(
    "SELECT call_count FROM rate_limits WHERE license_email = ? AND day = ?"
  ).bind(email, day).first<{ call_count: number }>()
  return row?.call_count ?? 0
}

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
  })
}

function corsResponse(): Response {
  return new Response(null, {
    headers: {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type, Authorization",
    },
  })
}
