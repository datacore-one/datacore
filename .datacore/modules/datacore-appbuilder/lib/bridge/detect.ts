import { exists, mkdir } from "@tauri-apps/plugin-fs"
import { homeDir } from "@tauri-apps/api/path"

export interface DataDirConfig {
  path: string
  isDatacore: boolean
}

export interface DataDirOptions {
  /** App name for standalone directory, e.g. "Megaphone" -> ~/Megaphone/ */
  appName: string
  /** Path within Datacore space, e.g. "0-personal/megaphone" */
  datacorePath: string
  /** Subdirectories to create on first launch */
  subdirs?: string[]
}

/**
 * Detect whether Datacore is installed. If so, use Datacore space.
 * Otherwise, create standalone ~/AppName/ directory.
 */
export async function detectDataDir(opts: DataDirOptions): Promise<DataDirConfig> {
  const home = await homeDir()
  const subdirs = opts.subdirs ?? []

  // Check for Datacore installation
  const datacoreRoot = `${home}Data/.datacore`
  if (await exists(datacoreRoot)) {
    const appPath = `${home}Data/${opts.datacorePath}`
    if (!(await exists(appPath))) {
      await mkdir(appPath, { recursive: true })
      for (const sub of subdirs) {
        await mkdir(`${appPath}/${sub}`, { recursive: true })
      }
    }
    return { path: appPath, isDatacore: true }
  }

  // Standalone mode
  const standalonePath = `${home}${opts.appName}`
  if (!(await exists(standalonePath))) {
    await mkdir(standalonePath, { recursive: true })
    for (const sub of subdirs) {
      await mkdir(`${standalonePath}/${sub}`, { recursive: true })
    }
  }
  return { path: standalonePath, isDatacore: false }
}
