#!/usr/bin/env node
/**
 * Read the licence of every package `npm ci` actually installs.
 *
 * `package.json` states intent; `package-lock.json` states what is resolved,
 * and it is the resolution that ends up in a container. So the tree is walked
 * from `node_modules`, where the licence text and the package metadata that
 * names it actually live.
 *
 * This is an **engineering gate, not legal advice**. It answers one question --
 * "is every licence here one this repository has already decided it accepts?"
 * -- and it reports everything else for a human to look at rather than claiming
 * a verdict it is not qualified to give.
 *
 *   allowed          on the allowlist, or reviewed under this very licence
 *   review required  a real licence, not on the list
 *   relicensed       reviewed once, and no longer under the licence that was read
 *   unknown          no licence stated at all
 *
 * `relicensed` exists because a manual exception is a statement about a
 * *licence*. Recorded against a package name alone it would survive that
 * package being relicensed, which is the one moment somebody needs to look
 * again.
 *
 * Exits non-zero when anything is not `allowed`, so a dependency that arrives
 * with a surprise is noticed when it arrives rather than at a release.
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const MODULES = join(ROOT, 'node_modules')
// One policy for the whole repository: the Python audit reads the same file,
// so a licence decision is made once rather than once per language.
const POLICY = JSON.parse(readFileSync(join(ROOT, '..', 'license-policy.json'), 'utf8'))

const ALLOWED = new Set(POLICY.allowed)
// A reviewed package is accepted under the licence somebody read, and under no
// other. The value is the expected identifier, not a free-text note.
const REVIEWED = new Map(
  Object.entries(POLICY.reviewed ?? {}).map(([name, entry]) => [name, entry.license]),
)
// This repository's own packages are not third-party dependencies.
const OWN = new Set(POLICY.self ?? [])

/** Every installed package, by name, with the version and licence it declares. */
function installed() {
  const found = new Map()
  const walk = (directory, scope = null) => {
    if (!existsSync(directory)) return
    for (const entry of readdirSync(directory)) {
      if (entry === '.bin' || entry === '.package-lock.json') continue
      const path = join(directory, entry)
      if (!statSync(path).isDirectory()) continue
      if (entry.startsWith('@') && scope === null) {
        walk(path, entry)
        continue
      }
      const name = scope === null ? entry : `${scope}/${entry}`
      const manifest = join(path, 'package.json')
      if (existsSync(manifest)) {
        const meta = JSON.parse(readFileSync(manifest, 'utf8'))
        found.set(name, { version: meta.version ?? '?', license: declared(meta) })
      }
      walk(join(path, 'node_modules'))
    }
  }
  walk(MODULES)
  return found
}

/** What a package's own metadata says its licence is. */
function declared(meta) {
  if (typeof meta.license === 'string') return meta.license
  if (meta.license && typeof meta.license.type === 'string') return meta.license.type
  if (Array.isArray(meta.licenses) && meta.licenses.length > 0) {
    return meta.licenses.map((entry) => entry.type ?? '?').join(' OR ')
  }
  return null
}

/**
 * Split an SPDX expression into the identifiers it names.
 *
 * `(MIT OR Apache-2.0)` is allowed when either side is: a package offered under
 * a choice may be taken under the one this repository accepts. `AND` is
 * different -- both apply -- so every part has to pass.
 */
function verdict(expression) {
  if (!expression) return 'unknown'
  const cleaned = expression.replace(/[()]/g, ' ').trim()
  if (/\bAND\b/i.test(cleaned)) {
    const parts = cleaned.split(/\s+AND\s+/i).map((part) => part.trim())
    return parts.every((part) => ALLOWED.has(part)) ? 'allowed' : 'review'
  }
  const parts = cleaned.split(/\s+OR\s+/i).map((part) => part.trim())
  return parts.some((part) => ALLOWED.has(part)) ? 'allowed' : 'review'
}

const packages = [...installed().entries()]
  .filter(([name]) => !OWN.has(name))
  .sort(([a], [b]) => a.localeCompare(b))
const problems = []
const counts = new Map()

/**
 * What the policy makes of one package.
 *
 * A reviewed package is allowed under the licence it was reviewed under and
 * under no other: the exception describes a document somebody read, so the same
 * package arriving under a different licence has not been reviewed at all.
 */
function decide(name, license) {
  if (!REVIEWED.has(name)) return verdict(license)
  return license === REVIEWED.get(name) ? 'allowed' : 'relicensed'
}

for (const [name, { version, license }] of packages) {
  const decided = decide(name, license)
  counts.set(license ?? 'unstated', (counts.get(license ?? 'unstated') ?? 0) + 1)
  if (decided !== 'allowed') {
    problems.push({
      name,
      version,
      license: license ?? 'unstated',
      decided,
      reviewed: REVIEWED.get(name) ?? null,
    })
  }
}

process.stdout.write(`${packages.length} installed packages\n`)
for (const [license, count] of [...counts.entries()].sort((a, b) => b[1] - a[1])) {
  process.stdout.write(`  ${String(count).padStart(4)}  ${license}\n`)
}

if (problems.length > 0) {
  process.stdout.write('\nNot covered by the repository policy:\n')
  for (const problem of problems) {
    const note = problem.decided === 'relicensed' ? ` (reviewed as ${problem.reviewed})` : ''
    process.stdout.write(
      `  ${problem.decided.padEnd(10)} ${problem.name}@${problem.version} — ${problem.license}${note}\n`,
    )
  }
  process.stdout.write(
    '\nAdd the identifier to license-policy.json only after somebody has read the licence.\n',
  )
  process.exit(1)
}

process.stdout.write('\nEvery installed package carries a licence this repository accepts.\n')
