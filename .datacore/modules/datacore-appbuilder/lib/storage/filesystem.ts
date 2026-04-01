import {
  readTextFile,
  readFile,
  writeTextFile,
  writeFile,
  remove,
  exists,
  stat,
  readDir,
  rename,
  mkdir,
} from "@tauri-apps/plugin-fs"
import type { StorageAdapter, FileStat, FileEntry } from "./types"
import { AsyncMutex } from "./mutex"

const writeMutex = new AsyncMutex()

export class FilesystemAdapter implements StorageAdapter {
  constructor(private basePath: string) {}

  private resolve(path: string): string {
    return `${this.basePath}/${path}`
  }

  async read(path: string): Promise<string> {
    return readTextFile(this.resolve(path))
  }

  async readBytes(path: string): Promise<Uint8Array> {
    return readFile(this.resolve(path))
  }

  async write(path: string, data: string | Uint8Array): Promise<void> {
    return writeMutex.run(async () => {
      const full = this.resolve(path)
      // Ensure parent directory exists
      const parent = full.substring(0, full.lastIndexOf("/"))
      if (parent && !(await exists(parent))) {
        await mkdir(parent, { recursive: true })
      }
      if (typeof data === "string") {
        await writeTextFile(full, data)
      } else {
        await writeFile(full, data)
      }
    })
  }

  async delete(path: string): Promise<void> {
    return writeMutex.run(async () => {
      await remove(this.resolve(path), { recursive: true })
    })
  }

  async exists(path: string): Promise<boolean> {
    return exists(this.resolve(path))
  }

  async stat(path: string): Promise<FileStat> {
    const s = await stat(this.resolve(path))
    return {
      path,
      size: s.size,
      isDir: s.isDirectory,
      modifiedAt: s.mtime?.getTime() ?? 0,
    }
  }

  async list(dir: string): Promise<FileEntry[]> {
    const entries = await readDir(this.resolve(dir))
    return entries.map((e) => ({
      path: `${dir}/${e.name}`,
      name: e.name,
      isDir: e.isDirectory,
    }))
  }

  async move(from: string, to: string): Promise<void> {
    return writeMutex.run(async () => {
      await rename(this.resolve(from), this.resolve(to))
    })
  }

  async mkdir(path: string): Promise<void> {
    return writeMutex.run(async () => {
      await mkdir(this.resolve(path), { recursive: true })
    })
  }
}
