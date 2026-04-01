/**
 * Simple async mutex for serializing filesystem writes.
 * Prevents YAML corruption from simultaneous read-modify-write.
 */
export class AsyncMutex {
  private queue: Array<() => void> = []
  private locked = false

  async acquire(): Promise<() => void> {
    if (!this.locked) {
      this.locked = true
      return () => this.release()
    }

    return new Promise<() => void>((resolve) => {
      this.queue.push(() => {
        resolve(() => this.release())
      })
    })
  }

  private release(): void {
    const next = this.queue.shift()
    if (next) {
      next()
    } else {
      this.locked = false
    }
  }

  async run<T>(fn: () => Promise<T>): Promise<T> {
    const release = await this.acquire()
    try {
      return await fn()
    } finally {
      release()
    }
  }
}
