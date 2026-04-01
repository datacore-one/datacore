import { readTextFile, writeTextFile, exists } from "@tauri-apps/plugin-fs"

export interface License {
  app_id: string
  email: string
  timestamp: number
  signature: string
}

export interface LicenseStatus {
  isLicensed: boolean
  isTrial: boolean
  trialDaysLeft: number
  email?: string
}

export interface LicenseConfig {
  appId: string
  trialDays: number
}

/**
 * Check current license status.
 * Phase 1: Simple file-based verification. No crypto yet — just checks file exists and is valid JSON.
 * Ed25519 verification added when license server is deployed.
 */
export async function checkLicense(dataDir: string, config: LicenseConfig): Promise<LicenseStatus> {
  const licensePath = `${dataDir}/.license`
  const trialPath = `${dataDir}/.trial`

  // Check for valid license
  if (await exists(licensePath)) {
    try {
      const raw = await readTextFile(licensePath)
      const license = JSON.parse(raw) as License
      if (license.app_id === config.appId && license.email) {
        return {
          isLicensed: true,
          isTrial: false,
          trialDaysLeft: 0,
          email: license.email,
        }
      }
    } catch {
      // Corrupted license — fall through to trial
    }
  }

  // Trial mode
  let trialStart: number
  if (await exists(trialPath)) {
    try {
      const raw = await readTextFile(trialPath)
      trialStart = parseInt(raw.trim(), 10)
    } catch {
      trialStart = Date.now()
      await writeTextFile(trialPath, String(trialStart))
    }
  } else {
    trialStart = Date.now()
    await writeTextFile(trialPath, String(trialStart))
  }

  const elapsed = Date.now() - trialStart
  const daysLeft = Math.max(0, config.trialDays - Math.floor(elapsed / 86_400_000))

  return {
    isLicensed: daysLeft > 0,
    isTrial: true,
    trialDaysLeft: daysLeft,
  }
}

export async function activateLicense(dataDir: string, appId: string, licenseData: string): Promise<boolean> {
  try {
    const license = JSON.parse(licenseData) as License
    if (license.app_id !== appId) return false
    await writeTextFile(`${dataDir}/.license`, licenseData)
    return true
  } catch {
    return false
  }
}
