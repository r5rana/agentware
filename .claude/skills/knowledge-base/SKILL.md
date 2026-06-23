# Knowledge Base Management Skill

## Overview

The knowledge base is agentware's long-term memory across sessions. It does NOT
live in this repo — it lives in an EXTERNAL directory the operator chose at
onboarding. Resolve that directory at runtime:

```bash
KDIR="$(scripts/agentware config --knowledge-dir-only)"
```

It holds context about the user, their projects, technical learnings, and
configurations. AI agents consult it before starting work and update it after
completing work so future agents inherit the context. NEVER commit knowledge
into this repo; it stays in `$KDIR`.

## Directory layout (inside `$KDIR`)

```
$KDIR/
├── MAIN.md              # Active work entry point — read first; injected on every session
├── index.json           # Searchable metadata index (entries, tags)
├── FEATURES.md          # Generated table of contents (scripts/agentware features)
├── .initialized         # Sentinel written at the end of onboarding
├── learnings/           # Technical knowledge (one .md per topic)
├── projects/            # Active project context (one folder per project)
├── configurations/      # Service / environment configs
├── prompts/             # Reusable prompts
├── references/          # External references / pointers
├── skills/              # User/agent-created reusable procedures (category: skills)
├── templates/           # Entry templates installed at init (self-contained)
├── work/                # Per-feature plans/worklogs/state (<feature>/plan.md, .loop/)
└── logs/                # prompts.log + sessions/<id>/{main,full}.md + per-subagent transcripts
```

Everything mutable lives here. The orchestrator package stays read-only during
normal use; only an explicit, !! WARNING !!-gated self-extension changes it.

`scripts/agentware init` scaffolds this. Onboarding seeds the first entries based
on the interview; the loop adds to it over time.

## index.json schema

Every entry is registered in `$KDIR/index.json`. Paths are stored RELATIVE to
`$KDIR` so the index stays portable:

```json
{
  "id": "learn-topic-name",
  "title": "Human-readable title",
  "category": "learnings",
  "path": "learnings/topic-name.md",
  "tags": ["tag1", "tag2"],
  "created": "YYYY-MM-DD",
  "summary": "One-line description"
}
```

`index.json` has two top-level keys: `entries` (array) and `tags`
(map of `tag-name → [entry-id, ...]`). Valid categories: `learnings`,
`projects`, `configurations`, `prompts`, `references`.

NEVER hand-edit `index.json`. The `scripts/agentware` toolkit is the ONLY writer
— it appends the entry, wires the tag map, keeps sorted order, and writes valid
formatted JSON atomically. Add an entry with ONE command:

```bash
scripts/agentware index add \
  --id <id> \
  --title "<title>" \
  --category <category> \
  --path <path-relative-to-KDIR> \
  --tags "<tag1,tag2>" \
  --summary "<one-line summary>"
```

The command validates the path exists (relative to `$KDIR`), rejects duplicate
IDs/paths and invalid categories, and updates the tag map for you.

## Entry conventions

### Learnings
- Path: `learnings/<topic>.md`. ID format: `learn-<topic>`.
- Created via `scripts/agentware learn` (writes file + registers atomically).
- End with a back-reference link to `./index.md`.

### Project entries
- Path: `projects/<project>/<NNN>-<title>.md` (numbered sequentially).
- The project's `index.md` is the overview with the running plan + links.
- Numbered entries are for **completed** phases. Active plans live under
  `$KDIR/work/<feature>/plan.md` (the work area), not as project knowledge entries.

## Markdown conventions

- Use `>` blockquote for the metadata header (created date, tags, status).
- Use `---` horizontal rules to separate sections.
- Use tables for structured data; fenced code blocks with language tags.
- Cross-reference related entries with relative markdown links.

## Resource / environment documentation

agentware assumes no particular cloud or platform — capture what is relevant to
*this* user's work: hostnames/endpoints, ports, container/image identifiers,
cluster/namespace/region, config file paths, and the command(s) to verify the
service is healthy. The point: future agents need to find the thing again.

## Tags

Tags are short, lowercased, hyphenated (`setup`, `database`, `gotcha`,
`language-rust`). Pass every relevant tag to `--tags "a,b,c"`; the toolkit adds
the entry id to each tag array. NEVER edit the tag map by hand.

## Looking up entries

Query through the toolkit — NEVER grep `index.json` directly. All lookups O(1):

```bash
scripts/agentware query --id <id>          # full entry by ID
scripts/agentware query --path <path>      # entry by file path (KDIR-relative)
scripts/agentware query --category <cat>   # all entries in a category
scripts/agentware query --tag <tag>        # all entries with a tag
```

Output is a JSON array of matching entries (empty `[]` on a miss).

## Validating the index

After any knowledge-base change, run `scripts/agentware index validate` (exit 0
= valid). It checks that every path exists (relative to `$KDIR`), the tag map is
bidirectionally consistent, there are no duplicate IDs or paths, and every
category is valid. For a full sweep run `scripts/agentware audit`.

## Anti-patterns

- ❌ Creating entries for things not yet built.
- ❌ Updating `MAIN.md` before work is actually done.
- ❌ Documenting failed attempts as entries — fix and retry instead.
- ❌ Hand-editing `index.json` or the tag map — always use the toolkit.
- ❌ Committing knowledge into the repo — it belongs in `$KDIR`.

## Promotion path: learning → skill → steering

See `.claude/skills/self-improvement/SKILL.md`. Short version:

- **Learnings** are observations and project-specific facts. Auto-write them to
  the external dir via `scripts/agentware learn`. No permission needed.
- **Skills** are reusable procedures (≥2 steps, applies across tasks). Auto-write
  them to the EXTERNAL `$KDIR/skills/<topic>/SKILL.md` and register with
  `scripts/agentware index add --category skills`. No permission needed (it's the
  user's own space). Discover them later with
  `scripts/agentware query --category skills` — deterministic, not by re-reading.
- **Package/steering changes** (`AGENTS.md`, `.claude/**`, `steering/**`,
  `agentware.sh`, `scripts/**`) are self-extension. Only on the user's EXPLICIT
  request, behind a `!! WARNING !!` that it may destabilize the system.

Templates are installed into `$KDIR/templates/` at init (also available in the
package `templates/`): `learning-template.md`, `project-template.md`,
`skill-template.md`.
