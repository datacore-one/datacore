import type { CreditBalance, CreditPackage } from "./types"

/**
 * Credit packages available for purchase.
 * Apps can customize via their config.
 */
export const DEFAULT_CREDIT_PACKAGES: CreditPackage[] = [
  { id: "starter", credits: 10, price: 5, label: "10 credits — $5" },
  { id: "standard", credits: 25, price: 10, label: "25 credits — $10" },
  { id: "pro", credits: 100, price: 30, label: "100 credits — $30" },
]

/**
 * Client-side credit balance check.
 * Calls the proxy server to get current balance.
 * Server is source of truth — local cache for display only.
 */
export async function getBalance(proxyUrl: string, token: string): Promise<CreditBalance> {
  const res = await fetch(`${proxyUrl}/v1/credits`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error("Failed to check credit balance")
  return res.json() as Promise<CreditBalance>
}

/**
 * Initiate credit purchase. Returns a Stripe Checkout URL.
 * Server creates the checkout session and returns the URL.
 */
export async function purchaseCredits(
  proxyUrl: string,
  token: string,
  packageId: string
): Promise<string> {
  const res = await fetch(`${proxyUrl}/v1/credits/purchase`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ package_id: packageId }),
  })
  if (!res.ok) throw new Error("Failed to initiate purchase")
  const data = await res.json() as { checkout_url: string }
  return data.checkout_url
}

/**
 * Check if user can make an AI call.
 * Returns true if: BYOK mode, or has credits, or has free demo.
 */
export function canGenerate(mode: string, balance?: CreditBalance): boolean {
  if (mode === "byok") return true
  if (!balance) return false
  return balance.freeDemo || balance.credits > 0
}
