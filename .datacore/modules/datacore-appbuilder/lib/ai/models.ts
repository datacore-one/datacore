import type { ModelDef } from "./types"

/**
 * Model catalog — all supported models with credit costs.
 *
 * Credit costs are proportional to actual API pricing.
 * 1 credit ≈ $0.05 of API cost (baseline: Sonnet = 1 credit).
 * Apps can override credit pricing via their config.
 */
export const MODEL_CATALOG: ModelDef[] = [
  // Claude
  {
    id: "claude-sonnet-4-20250514",
    provider: "claude",
    label: "Claude Sonnet 4",
    creditsPerCall: 1,
    inputPer1M: 3,
    outputPer1M: 15,
  },
  {
    id: "claude-opus-4-20250514",
    provider: "claude",
    label: "Claude Opus 4",
    creditsPerCall: 5,
    inputPer1M: 15,
    outputPer1M: 75,
  },
  {
    id: "claude-haiku-4-5-20251001",
    provider: "claude",
    label: "Claude Haiku 4.5",
    creditsPerCall: 0.25,
    inputPer1M: 0.80,
    outputPer1M: 4,
  },
  // OpenAI
  {
    id: "gpt-4o",
    provider: "openai",
    label: "GPT-4o",
    creditsPerCall: 1,
    inputPer1M: 2.50,
    outputPer1M: 10,
  },
  {
    id: "gpt-4o-mini",
    provider: "openai",
    label: "GPT-4o Mini",
    creditsPerCall: 0.25,
    inputPer1M: 0.15,
    outputPer1M: 0.60,
  },
  // Gemini
  {
    id: "gemini-2.0-flash",
    provider: "gemini",
    label: "Gemini 2.0 Flash",
    creditsPerCall: 0.25,
    inputPer1M: 0.10,
    outputPer1M: 0.40,
  },
  {
    id: "gemini-2.5-pro",
    provider: "gemini",
    label: "Gemini 2.5 Pro",
    creditsPerCall: 2,
    inputPer1M: 1.25,
    outputPer1M: 10,
  },
]

export function getModel(id: string): ModelDef | undefined {
  return MODEL_CATALOG.find((m) => m.id === id)
}

export function getModelsByProvider(provider: string): ModelDef[] {
  return MODEL_CATALOG.filter((m) => m.provider === provider)
}

export const DEFAULT_MODEL = "claude-sonnet-4-20250514"
