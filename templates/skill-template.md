---
name: <skill-folder-name>
description: <One or two sentences. WHAT the skill does AND WHEN to use it — this
  is the only text a harness sees when deciding whether to auto-invoke the skill,
  so lead with concrete trigger conditions and keywords. "When X happens, do Y."
  Avoid vague verbs like "helps with" or "sometimes".>
---

# <Skill Title>

> **Portable Agent Skill** (agentskills.io open standard). The YAML frontmatter
> above is the spec-compliant contract: `name` MUST equal the containing folder
> name; `description` is the routing text every harness reads. Keep the body
> below HARNESS-AGNOSTIC — no hardcoded invocation syntax (`/skill`, `$skill`,
> `/skills`), no harness-only frontmatter (e.g. Claude `allowed-tools`) in the
> shared body, and no harness-specific features. Put runnable logic in portable
> `scripts/` so it behaves identically on Claude, Codex, or any agentskills.io
> harness.

> **When to invoke**: <a specific, concrete trigger condition. "When X happens"
> is good. "Sometimes" is bad. Mirror this in the `description` frontmatter so
> auto-routing harnesses pick it up.>

## Why this skill exists

<Why is this a skill and not a learning? Why does the agent need a written
procedure rather than reasoning from first principles each time?>

## Prerequisites

- <What must be true before running this skill>

## Procedure

### Step 1 — <name>

<Numbered steps with concrete commands and expected output. Specific enough that
a different agent could follow it without re-deriving anything. Prefer portable
shell over harness-specific tooling; assume a restrictive workspace-write sandbox
(no network, repo-scoped writes) unless the skill explicitly needs more.>

### Step 2 — <name>

...

## Failure handling

<What does the agent do when the procedure fails partway? Mark incomplete? Roll
back? Surface specific evidence to the user?>

## Gotchas

- <Failure mode 1 and how to handle it>

## See also

- <Related skill: `.claude/skills/<other-skill>/SKILL.md`>
- <Related learning in the external knowledge dir: `learnings/<topic>.md`>

---

## Authoring notes (delete this section before finalizing)

A skill earns its place when the procedure has ≥2 steps AND applies across
multiple tasks (not a one-off fix). If your draft fails either, it should be a
learning instead — see `.claude/skills/self-improvement/SKILL.md`.

### Portable format (agentskills.io standard)

Every skill is a folder containing `SKILL.md` plus optional `scripts/` and
`references/`. The `SKILL.md` MUST open with YAML frontmatter carrying at least
`name` (== folder name) and `description`. This is the converged open standard
(agentskills.io) adopted by Anthropic, OpenAI Codex, Gemini, Cursor and ~40 other
tools, so one authored source installs to any harness.

### Two kinds of skill — PACKAGE vs KB

- **PACKAGE skills** live in this repo under `.claude/skills/<name>/SKILL.md`.
  They are the canonical authored source and ARE registered in the per-operator
  knowledge index under the `skills` category, so the agent can discover them by
  query. Register with:

  ```sh
  scripts/agentware index add --category skills \
    --id skill-<name> --title "<name>" \
    --path skills/<name>.md --tags "<a,b>" --summary "<one-liner>"
  ```

  and look them up with `scripts/agentware query --category skills` (or
  `--id skill-<name>`). (Correction to older guidance: skills ARE indexed — under
  the `skills` category — they are NOT silently excluded from the index.)

- **KB skills** are operator-specific procedures that emerge in the external
  knowledge dir (e.g. promoted from learnings). Same portable format; they live
  in the knowledge dir, not the package.

### Harness-to-install-path map

One portable source installs to whichever harness is active (resolved at
onboarding via `config --cli-only`):

| Harness            | Project path            | User path             | Steering file              |
|--------------------|-------------------------|-----------------------|----------------------------|
| claude             | `.claude/skills/`       | `~/.claude/skills/`   | `CLAUDE.md` (`@AGENTS.md`)  |
| codex / generic    | `.agents/skills/`       | `~/.agents/skills/`   | `AGENTS.md` (native)       |
| other (custom)     | configured custom path  | configured custom path| `AGENTS.md`                |
| unknown (fallback) | `.agents/skills/`       | —                     | `AGENTS.md`                |

The canonical in-repo source is `.claude/skills/`; installers copy the identical
portable folders to `.agents/skills/` (or a configured custom path) for non-Claude
harnesses. Unknown harnesses fall back to `.agents/skills/` + `AGENTS.md`.

### Wiring

After writing, wire the skill in:
- Trigger applies on every task of a kind → one-liner in `AGENTS.md`.
- Trigger applies in one situation → add to a related skill's "See also".
- Register it in the index (`index add --category skills`) so it is discoverable.
