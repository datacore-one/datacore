// --- Providers ---

export type ProviderId = "claude" | "openai" | "gemini"

export interface ModelDef {
  id: string              // e.g. "claude-sonnet-4-20250514"
  provider: ProviderId
  label: string           // e.g. "Claude Sonnet 4"
  creditsPerCall: number  // Credits consumed per API call
  inputPer1M: number      // USD per 1M input tokens (for reference)
  outputPer1M: number     // USD per 1M output tokens (for reference)
}

// --- Configuration ---

export type AIMode = "byok" | "credits" | "proxy"

export interface AIConfig {
  mode: AIMode
  model: string           // Model ID from MODEL_CATALOG
  apiKey?: string         // BYOK mode: user's own key
  proxyUrl?: string       // Credits/proxy mode: server endpoint
  proxyToken?: string     // Credits/proxy mode: auth token
}

// --- Requests ---

export interface GenerateRequest {
  prompt: string
  systemPrompt?: string
  maxTokens?: number
  temperature?: number
  model?: string          // Override config model for this call
}

export interface GenerateResponse {
  text: string
  usage: { inputTokens: number; outputTokens: number }
  creditsUsed?: number
  creditsRemaining?: number
}

// --- Credits ---

export interface CreditBalance {
  credits: number
  freeDemo: boolean       // Has unused free demo?
}

export interface CreditPackage {
  id: string
  credits: number
  price: number           // USD
  label: string           // e.g. "10 credits — $5"
}
