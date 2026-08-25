import { useEffect, useRef, useState } from 'react'

export interface ResourceState<T> {
  data: T
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useResource<T>(load: () => Promise<T>, initial: T): ResourceState<T> {
  const loadRef = useRef(load)
  const initialRef = useRef(initial)
  loadRef.current = load
  initialRef.current = initial
  const [data, setData] = useState(initial)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    loadRef.current()
      .then(value => {
        if (cancelled) return
        setData(value)
        setError(null)
      })
      .catch(reason => {
        if (cancelled) return
        setData(initialRef.current)
        setError(reason instanceof Error ? reason.message : 'The API is unavailable')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [attempt])

  return { data, loading, error, refresh: () => setAttempt(value => value + 1) }
}
