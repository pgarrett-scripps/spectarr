const MAX_ENTRIES = 50
const MAX_AGE_MS = 60_000
const entries = new Map<string, { data: unknown, savedAt: number }>()
let generation = 0

export function clearResourceCache(): void {
  generation += 1
  entries.clear()
}

export const resourceCacheGeneration = (): number => generation

export function readResourceCache<T>(key?: string): { data: T } | undefined {
  if (!key) return undefined
  const entry = entries.get(key)
  if (!entry) return undefined
  if (Date.now() - entry.savedAt >= MAX_AGE_MS) {
    entries.delete(key)
    return undefined
  }
  entries.delete(key)
  entries.set(key, entry)
  return { data: entry.data as T }
}

export function writeResourceCache(key: string, data: unknown, startedGeneration: number): void {
  if (generation !== startedGeneration) return
  entries.delete(key)
  entries.set(key, { data, savedAt: Date.now() })
  if (entries.size > MAX_ENTRIES) entries.delete(entries.keys().next().value!)
}

export function removeResourceCache(key: string): void {
  entries.delete(key)
}
