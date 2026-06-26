/**
 * Task 24 — frontend security gates (static assertions, no DOM needed).
 *
 *   1. index.html ships a Content-Security-Policy meta tag whose directives lock
 *      the app down to same-origin: default-src 'self', script-src 'self' (no
 *      'unsafe-eval' anywhere), connect-src 'self' (same-origin network only),
 *      object-src 'none', base-uri 'self'. NOTE: `frame-ancestors` is deliberately
 *      NOT in the meta — browsers IGNORE it there (and log a console error), so it
 *      is delivered as a real HTTP header (`Content-Security-Policy:
 *      frame-ancestors 'none'` + `X-Frame-Options: DENY`) by the dashboard server
 *      (scripts/agentware_dashboard.py `_safe_headers`), where it is enforceable.
 *   2. index.html references NO external origin — every resource (src=/href=) is
 *      relative/same-origin, so a fresh clone makes zero cross-origin requests.
 *
 * This is the test the Task-24 acceptance criterion requires ("a test asserts a
 * CSP meta tag is present and no external origins are referenced"). It reads the
 * SOURCE index.html (the template Vite copies into dist/ verbatim, rewriting only
 * the bundled entry asset URLs to relative ./assets/* paths), so it guards the
 * shipped contract without depending on a build step.
 */
// @vitest-environment node
import { readFileSync } from 'node:fs'
import { fileURLToPath, URL } from 'node:url'
import { describe, expect, it } from 'vitest'

const indexHtml = readFileSync(
  fileURLToPath(new URL('../index.html', import.meta.url)),
  'utf8',
)

/** Extract the CSP policy string from the http-equiv meta tag, if present. */
function extractCsp(html: string): string | null {
  const meta = html.match(
    /<meta[^>]*http-equiv=["']Content-Security-Policy["'][^>]*>/i,
  )?.[0]
  if (!meta) return null
  // Capture the full content value between its OWN quote char — the policy
  // embeds single quotes ('self'), so a naive [^"'] class would truncate it.
  return meta.match(/content=("([^"]*)"|'([^']*)')/i)?.slice(2).find((g) => g != null) ?? null
}

/** Parse a CSP policy string into a directive -> sources map. */
function parseCsp(policy: string): Record<string, string[]> {
  const out: Record<string, string[]> = {}
  for (const part of policy.split(';')) {
    const [name, ...sources] = part.trim().split(/\s+/)
    if (name) out[name.toLowerCase()] = sources
  }
  return out
}

describe('frontend security — CSP meta tag', () => {
  it('ships a Content-Security-Policy meta tag', () => {
    expect(extractCsp(indexHtml)).not.toBeNull()
  })

  it('locks default-src, script-src and connect-src to same-origin', () => {
    const csp = parseCsp(extractCsp(indexHtml)!)
    expect(csp['default-src']).toEqual(["'self'"])
    expect(csp['script-src']).toEqual(["'self'"])
    expect(csp['connect-src']).toEqual(["'self'"])
  })

  it('hardens object-src and base-uri', () => {
    const csp = parseCsp(extractCsp(indexHtml)!)
    expect(csp['object-src']).toEqual(["'none'"])
    expect(csp['base-uri']).toEqual(["'self'"])
  })

  it('omits frame-ancestors from the meta (inert there — served as an HTTP header)', () => {
    // frame-ancestors is unenforceable via <meta> and only produces a console
    // error; the dashboard server sends it as a real header instead.
    const csp = parseCsp(extractCsp(indexHtml)!)
    expect(csp['frame-ancestors']).toBeUndefined()
  })

  it("never permits 'unsafe-eval' in any directive", () => {
    expect(extractCsp(indexHtml)!.toLowerCase()).not.toContain('unsafe-eval')
  })
})

describe('frontend security — no external origins referenced', () => {
  it('index.html loads no absolute or protocol-relative resource', () => {
    // Every src=/href= the browser fetches must be relative/same-origin.
    const resourceUrls = [...indexHtml.matchAll(/(?:src|href)=["']([^"']+)["']/gi)].map(
      (m) => m[1],
    )
    const external = resourceUrls.filter(
      (u) => /^[a-z]+:\/\//i.test(u) || u.startsWith('//'),
    )
    expect(external).toEqual([])
  })

  it('CSP content itself names no external origin host', () => {
    // The only tokens allowed are keywords ('self', 'none', 'unsafe-inline'),
    // schemes terminating a source (data:), and directive names — never a host.
    const policy = extractCsp(indexHtml)!
    expect(policy).not.toMatch(/https?:\/\//i)
    expect(policy).not.toMatch(/\/\/[a-z0-9.-]+/i)
  })
})
