import { useEffect, useRef, useState } from 'react'
import { readResourceCache, removeResourceCache, resourceCacheGeneration, writeResourceCache } from './resourceCache'

export interface ResourceState<T> {
  data: T
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useResource<T>(load: () => Promise<T>, initial: T, reloadKey?: unknown, cacheKey?: string): ResourceState<T> {
  const loadRef = useRef(load)
  const initialRef = useRef(initial)
  const reloadKeyRef = useRef(reloadKey)
  const cacheKeyRef = useRef(cacheKey)
  loadRef.current = load
  initialRef.current = initial
  const [data, setData] = useState(() => {
    const cached = readResourceCache<T>(cacheKey)
    return cached ? cached.data : initial
  })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    const generation = resourceCacheGeneration()
    if (!Object.is(reloadKeyRef.current, reloadKey) || cacheKeyRef.current !== cacheKey) {
      reloadKeyRef.current = reloadKey
      cacheKeyRef.current = cacheKey
      const cached = readResourceCache<T>(cacheKey)
      setData(cached ? cached.data : initialRef.current)
      setError(null)
    }
    setLoading(true)

    loadRef.current()
      .then(value => {
        if (cancelled) return
        if (cacheKey) writeResourceCache(cacheKey, value, generation)
        setData(value)
        setError(null)
      })
      .catch(reason => {
        if (cancelled) return
        if (cacheKey) {
          removeResourceCache(cacheKey)
          if (reason && [401, 403, 404].includes(reason.status)) setData(initialRef.current)
        }
        setError(reason instanceof Error ? reason.message : 'The API is unavailable')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [attempt, reloadKey, cacheKey])

  return { data, loading, error, refresh: () => setAttempt(value => value + 1) }
}
