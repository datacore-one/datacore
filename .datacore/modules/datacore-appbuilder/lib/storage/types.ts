export interface FileStat {
  path: string
  size: number
  isDir: boolean
  modifiedAt: number
}

export interface FileEntry {
  path: string
  name: string
  isDir: boolean
}

export interface StorageAdapter {
  read(path: string): Promise<string>
  readBytes(path: string): Promise<Uint8Array>
  write(path: string, data: string | Uint8Array): Promise<void>
  delete(path: string): Promise<void>
  exists(path: string): Promise<boolean>
  stat(path: string): Promise<FileStat>
  list(dir: string): Promise<FileEntry[]>
  move(from: string, to: string): Promise<void>
  mkdir(path: string): Promise<void>
}
