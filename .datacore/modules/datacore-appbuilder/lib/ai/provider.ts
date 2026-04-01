import type { AIConfig, GenerateRequest, GenerateResponse, ProviderId } from "./types"
import { getModel, DEFAULT_MODEL } from "./models"

const TIMEOUT_MS = 30_000
const MAX_RETRIES = 3
const RETRY_BASE_MS = 1_000

/**
 * Unified AI service supporting multiple providers and modes.
 *
 * Modes:
 *   byok     — User's own API key, calls provider directly. Free, unlimited.
 *   credits  — No API key. Calls our proxy which deducts credits per call.
 *   proxy    — Like credits but for flat-rate/licensed users (legacy compat).
 */
export class AIService {
  private config: AIConfig

  constructor(config: AIConfig) {
    this.config = config
  }

  get modelId(): string {
    return this.config.model || DEFAULT_MODEL
  }

  get provider(): ProviderId {
    return getModel(this.modelId)?.provider ?? "claude"
  }

  get isConfigured(): boolean {
    if (this.config.mode === "byok") return !!this.config.apiKey
    return !!this.config.proxyUrl
  }

  async generate(req: GenerateRequest): Promise<GenerateResponse> {
    const modelId = req.model || this.modelId

    if (this.config.mode === "byok") {
      return this.generateDirect(req, modelId)
    }
    return this.generateProxy(req, modelId)
  }

  private async generateDirect(req: GenerateRequest, modelId: string): Promise<GenerateResponse> {
    if (!this.config.apiKey) {
      throw new Error("API key not configured. Add your key in Settings.")
    }

    const model = getModel(modelId)
    if (!model) throw new Error(`Unknown model: ${modelId}`)

    switch (model.provider) {
      case "claude":
        return this.callClaude(req, modelId)
      case "openai":
        return this.callOpenAI(req, modelId)
      case "gemini":
        return this.callGemini(req, modelId)
      default:
        throw new Error(`Unsupported provider: ${model.provider}`)
    }
  }

  private async callClaude(req: GenerateRequest, modelId: string): Promise<GenerateResponse> {
    const res = await this.withTimeout(
      this.withRetry(() =>
        fetch("https://api.anthropic.com/v1/messages", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-api-key": this.config.apiKey!,
            "anthropic-version": "2023-06-01",
            "anthropic-dangerous-direct-browser-access": "true",
          },
          body: JSON.stringify({
            model: modelId,
            max_tokens: req.maxTokens ?? 4096,
            temperature: req.temperature ?? 0.7,
            system: req.systemPrompt ?? "",
            messages: [{ role: "user", content: req.prompt }],
          }),
        })
      )
    )

    if (!res.ok) throw await this.apiError(res, "Claude")
    const data = await res.json() as {
      content: Array<{ type: string; text: string }>
      usage: { input_tokens: number; output_tokens: number }
    }

    return {
      text: data.content.filter((b) => b.type === "text").map((b) => b.text).join(""),
      usage: { inputTokens: data.usage.input_tokens, outputTokens: data.usage.output_tokens },
    }
  }

  private async callOpenAI(req: GenerateRequest, modelId: string): Promise<GenerateResponse> {
    const messages: Array<{ role: string; content: string }> = []
    if (req.systemPrompt) messages.push({ role: "system", content: req.systemPrompt })
    messages.push({ role: "user", content: req.prompt })

    const res = await this.withTimeout(
      this.withRetry(() =>
        fetch("https://api.openai.com/v1/chat/completions", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${this.config.apiKey}`,
          },
          body: JSON.stringify({
            model: modelId,
            max_tokens: req.maxTokens ?? 4096,
            temperature: req.temperature ?? 0.7,
            messages,
          }),
        })
      )
    )

    if (!res.ok) throw await this.apiError(res, "OpenAI")
    const data = await res.json() as {
      choices: Array<{ message: { content: string } }>
      usage: { prompt_tokens: number; completion_tokens: number }
    }

    return {
      text: data.choices[0]?.message?.content ?? "",
      usage: { inputTokens: data.usage.prompt_tokens, outputTokens: data.usage.completion_tokens },
    }
  }

  private async callGemini(req: GenerateRequest, modelId: string): Promise<GenerateResponse> {
    const contents = []
    if (req.systemPrompt) {
      contents.push({ role: "user", parts: [{ text: req.systemPrompt }] })
      contents.push({ role: "model", parts: [{ text: "Understood." }] })
    }
    contents.push({ role: "user", parts: [{ text: req.prompt }] })

    const res = await this.withTimeout(
      this.withRetry(() =>
        fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/${modelId}:generateContent?key=${this.config.apiKey}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              contents,
              generationConfig: {
                maxOutputTokens: req.maxTokens ?? 4096,
                temperature: req.temperature ?? 0.7,
              },
            }),
          }
        )
      )
    )

    if (!res.ok) throw await this.apiError(res, "Gemini")
    const data = await res.json() as {
      candidates: Array<{ content: { parts: Array<{ text: string }> } }>
      usageMetadata: { promptTokenCount: number; candidatesTokenCount: number }
    }

    return {
      text: data.candidates?.[0]?.content?.parts?.map((p) => p.text).join("") ?? "",
      usage: {
        inputTokens: data.usageMetadata?.promptTokenCount ?? 0,
        outputTokens: data.usageMetadata?.candidatesTokenCount ?? 0,
      },
    }
  }

  private async generateProxy(req: GenerateRequest, modelId: string): Promise<GenerateResponse> {
    const url = this.config.proxyUrl
    if (!url) throw new Error("Proxy URL not configured.")

    const res = await this.withTimeout(
      this.withRetry(async () => {
        const response = await fetch(`${url}/v1/generate`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${this.config.proxyToken}`,
          },
          body: JSON.stringify({
            model: modelId,
            prompt: req.prompt,
            system_prompt: req.systemPrompt,
            max_tokens: req.maxTokens ?? 4096,
            temperature: req.temperature ?? 0.7,
          }),
        })

        if (!response.ok) {
          if (response.status === 402) {
            const body = await response.json() as { error: string; credits_remaining: number }
            throw new Error(`No credits remaining. ${body.error}`)
          }
          if (response.status === 429) throw new RetryableError("Rate limited. Waiting...")
          throw await this.apiError(response, "Proxy")
        }

        return response.json() as Promise<{
          text: string
          usage: { input_tokens: number; output_tokens: number }
          credits_used: number
          credits_remaining: number
        }>
      })
    )

    return {
      text: res.text,
      usage: { inputTokens: res.usage?.input_tokens ?? 0, outputTokens: res.usage?.output_tokens ?? 0 },
      creditsUsed: res.credits_used,
      creditsRemaining: res.credits_remaining,
    }
  }

  // --- Helpers ---

  private async apiError(res: Response, provider: string): Promise<Error> {
    const text = await res.text().catch(() => "")
    if (res.status === 401) return new Error(`Invalid ${provider} API key. Check Settings.`)
    if (res.status === 429) return new RetryableError(`${provider} rate limit. Waiting...`)
    return new Error(`${provider} error ${res.status}: ${text.slice(0, 200)}`)
  }

  private async withTimeout<T>(promise: Promise<T>): Promise<T> {
    return Promise.race([
      promise,
      new Promise<never>((_, reject) =>
        setTimeout(() => reject(new Error("AI request timed out. Try again.")), TIMEOUT_MS)
      ),
    ])
  }

  private async withRetry<T>(fn: () => Promise<T>, retries = MAX_RETRIES): Promise<T> {
    for (let attempt = 0; attempt <= retries; attempt++) {
      try {
        return await fn()
      } catch (err) {
        if (attempt === retries || !(err instanceof RetryableError)) throw err
        await new Promise((r) => setTimeout(r, RETRY_BASE_MS * Math.pow(2, attempt)))
      }
    }
    throw new Error("Unreachable")
  }
}

class RetryableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "RetryableError"
  }
}
