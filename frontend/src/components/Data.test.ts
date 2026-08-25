import { describe, expect, it } from 'vitest'
import { formatBytes } from './Data'

describe('formatBytes', () => {
  it('formats bytes with decimal units', () => {
    expect(formatBytes(0)).toBe('0 B')
    expect(formatBytes(1_500_000_000)).toBe('1.5 GB')
    expect(formatBytes(6_820_000_000_000)).toBe('6.82 TB')
  })
})
