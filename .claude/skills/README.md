# agentware Skills — Catalog

> **Skills are written procedures the agent executes.** Each skill is a folder
> with a spec-compliant `SKILL.md` (YAML frontmatter `name` == folder + `description`,
> a portable harness-agnostic body, and optional `scripts/`/`references/`),
> following the converged open standard at [agentskills.io](https://agentskills.io).
>
> agentware ships **ZERO third-party skills**. Every skill below is **authored
> in-repo** with security baked in — a downloaded `SKILL.md` is instructions the
> agent runs and bundled `scripts/` execute with operator privileges, i.e. a
> prompt-injection + supply-chain attack surface. Any future external skill is
> gated behind `skill-vetter` + explicit confirmation (see `docs/methodology.md`).

## Two planes of skills

| Plane | Source of truth | Lives in | Discovery |
|-------|-----------------|----------|-----------|
| **Package skills** (this catalog) | the agentware repo | `.claude/skills/` (canonical) → installed per harness | the harness Skill picker + `scripts/agentware skill list` |
| **KB skills** (operator-grown) | the external knowledge base | `<knowledge-dir>/skills/` | `scripts/agentware query --category skills`; injected by the SessionStart roster |

Package skills are invoked by the harness's native skill mechanism; KB skills are
**pull-only** context the agent reads (not Skill-tool invokable). The KB skill set
grows automatically from promoted learnings (auto-skill-promotion queue).

## Install layout (any harness)

One portable source (`.claude/skills/`) installs to whatever harness is active —
see onboarding and `docs/methodology.md` for the harness→path map:

| Harness | Project path | User path | Steering file |
|---------|--------------|-----------|---------------|
| Claude | `.claude/skills/` (canonical source) | `~/.claude/skills/` | `CLAUDE.md` (imports `@AGENTS.md`) |
| Codex / generic | `.agents/skills/` | `~/.agents/skills/` | `AGENTS.md` (native) |
| Other | configurable custom path | — | `AGENTS.md` |
| Unknown | fall back to `.agents/skills/` | — | `AGENTS.md` |

## The 14 authored skills

### Security (6)

| Skill | Use when |
|-------|----------|
| **sast-audit** | "security audit", "find vulnerabilities", "SAST scan", or before shipping security-sensitive code — whole-repo static analysis by vulnerability class (SQLi, XSS, RCE, SSRF, IDOR, XXE, SSTI, JWT, authz, path traversal, upload, deserialization, business-logic). |
| **skill-vetter** | "vet a skill", "is this plugin safe", or before `skill add` installs anything — static review of an external skill/MCP/plugin for prompt-injection, exfiltration, broad perms, unpinned deps; emits a provenance + PASS/REVIEW/BLOCK verdict. |
| **dependency-supply-chain-audit** | "audit dependencies", "scan for CVEs", "generate an SBOM", or before upgrading deps — scan the transitive graph for CVEs, lockfile/pin integrity, typosquats, and suspicious maintainer changes. |
| **secure-by-design** | "design this securely", "what are the security requirements", or when scoping any new endpoint/auth flow/data model/LLM feature — a design-time OWASP Top 10:2025 / ASVS 5.0 / LLM Top 10 checklist (applied while designing, not after). |
| **threat-modeling** | "threat model this", "do a STRIDE analysis", "map the attack surface" — decompose to a data-flow diagram, draw trust boundaries, enumerate threats per element with STRIDE, rank, mitigate. |
| **ci-cd-security-audit** | "audit the CI pipeline", "review these GitHub Actions", "harden the deploy", or before shipping CI/hook/executor/MCP changes — detect script injection, unpinned actions, over-broad tokens, secret-exfil, poisoned-pipeline-execution. |

### Productivity (6)

| Skill | Use when |
|-------|----------|
| **test-authoring** | "write tests", "add coverage", "do TDD", "get this to green" — infer the project's framework, write targeted red→green tests, plus mutation + property-based techniques. |
| **systematic-debugging** | "debug this", "why is this failing", "find the root cause", "this test is flaky" — reproduce → instrument → bisect → one hypothesis → fix → confirm, wired to the R-FAIL ladder. |
| **git-commit-pr-workflow** | "write a commit message", "draft a PR", "write a changelog entry" — group the diff into Conventional Commits and PROPOSE the exact git/gh commands; never auto-commits (R-GIT-01). |
| **skill-creator** | "create a skill", "scaffold a skill", "turn this procedure into a skill" — generate spec-compliant frontmatter + a portable body, wire it into the index, verify it parses and routes. |
| **env-doctor** | "set up the dev environment", "why won't this build", "the project won't run", or a fresh clone/CI runner failing before real work — verify toolchain/runtime versions, deps, env vars, ports, services; PROPOSE remediation. |
| **frontend-design** | "design this screen", "make this look good", "make it less generic", or scaffolding any new frontend surface (web or React Native) — tokens → deliberate layout/hierarchy → responsiveness + accessibility. |

### agentware gap-fillers (2)

| Skill | Use when |
|-------|----------|
| **backend-verification** | "verify this endpoint", "test the API", "did the request succeed", or whenever a change adds/modifies an HTTP/RPC/GraphQL endpoint — call it with valid AND invalid inputs, assert status/headers/body, read-after-write for mutations, check authz negatives (R-VERIFY-03/04). |
| **safe-migration** | "run this migration", "alter the schema", "backfill this data", "move these files", or whenever a change rewrites persisted state — confirm backup + rollback first, prefer expand-contract, idempotent steps, scratch-copy verify, before/after counts in the worklog (R-VERIFY-04 / R-AUTO-02). |

> The harness also ships non-catalog package skills used by the loop itself
> (`onboarding`, `knowledge-base`, `self-improvement`, `ui-verification`).

## Managing skills

```
scripts/agentware skill list                 # installed catalog skills
scripts/agentware skill search <term>        # search the catalog
scripts/agentware skill validate <name>      # check frontmatter / spec compliance
scripts/agentware skill add <name>           # vet (skill-vetter) + install a TRUSTED in-repo source, then index add
scripts/agentware skill remove <name>        # uninstall + index remove
```

`skill add` is **never an internet fetcher**: it resolves only trusted in-repo
catalog sources by default and runs `skill-vetter` + a provenance/pinned-ref
report before copying. External sources are gated behind `skill-vetter` + explicit
confirmation.

## Inspiration (design references — we author our own)

[trailofbits/skills](https://github.com/trailofbits/skills) ·
[utkusen/sast-skills](https://github.com/utkusen/sast-skills) ·
[2026 most-popular skills](https://composio.dev/content/top-claude-skills) ·
[awesome-openclaw-skills](https://github.com/VoltAgent/awesome-openclaw-skills) ·
[agentskills.io](https://agentskills.io) ·
[Codex skills](https://developers.openai.com/codex/skills).
