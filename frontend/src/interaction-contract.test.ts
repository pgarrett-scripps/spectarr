import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

function tsxFiles(root: string): string[] {
  return readdirSync(root).flatMap(name => {
    const path = join(root, name)
    return statSync(path).isDirectory() ? tsxFiles(path) : path.endsWith('.tsx') ? [path] : []
  })
}

describe('dashboard interaction contract', () => {
  it('does not render enabled buttons without an action', () => {
    const sourceRoot = join(process.cwd(), 'src')
    const violations = tsxFiles(sourceRoot).flatMap(path => {
      const source = readFileSync(path, 'utf8')
      return [...source.matchAll(/<button\b[^>]*>/g)]
        .filter(match => !/onClick=|type="submit"/.test(match[0]))
        .map(match => `${relative(sourceRoot, path)}: ${match[0]}`)
    })
    expect(violations).toEqual([])
  })

  it('does not render forms without submit behavior', () => {
    const sourceRoot = join(process.cwd(), 'src')
    const violations = tsxFiles(sourceRoot).flatMap(path => {
      const source = readFileSync(path, 'utf8')
      return [...source.matchAll(/<form\b[^>]*>/g)]
        .filter(match => !/onSubmit=/.test(match[0]))
        .map(match => `${relative(sourceRoot, path)}: ${match[0]}`)
    })
    expect(violations).toEqual([])
  })

  it('does not render disconnected form controls', () => {
    const sourceRoot = join(process.cwd(), 'src')
    const violations = tsxFiles(sourceRoot).flatMap(path => {
      const source = readFileSync(path, 'utf8')
      return [...source.matchAll(/<(input|select|textarea)\b[^>]*>/g)]
        .filter(match => !/name=|onChange=/.test(match[0]))
        .map(match => `${relative(sourceRoot, path)}: ${match[0]}`)
    })
    expect(violations).toEqual([])
  })

  it('does not contain demo data or preview fallbacks', () => {
    const sourceRoot = join(process.cwd(), 'src')
    const violations = tsxFiles(sourceRoot).flatMap(path => {
      const source = readFileSync(path, 'utf8')
      return /demoProjects|demoRuns|demoJobs|demoStorage|demoOverview|Preview mode/.test(source)
        ? [relative(sourceRoot, path)]
        : []
    })
    expect(violations).toEqual([])
  })

  it('does not render static disabled settings controls', () => {
    const sourceRoot = join(process.cwd(), 'src')
    const violations = tsxFiles(sourceRoot).flatMap(path => {
      const source = readFileSync(path, 'utf8')
      return [...source.matchAll(/<(input|select|textarea)\b[^>]*\sdisabled(?:\s|\/?>)/g)]
        .map(match => `${relative(sourceRoot, path)}: ${match[0]}`)
    })
    expect(violations).toEqual([])
  })
})
