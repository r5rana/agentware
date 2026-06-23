# agentware — User Guide

How to use agentware day-to-day: the three agents, the persistent memory layer
that you own completely, and how everything is tracked deterministically.

---

## What you get

- **A persistent memory layer that is 100% yours.** Your knowledge, learnings,
  project context, agent-created skills, work plans, and a full log of every
  session all live as plain **Markdown + JSON files in a directory you choose**,
  on your own disk. No service, no account, no fee, nothing phoned home. You can
  read it, edit it, grep it, back it up, or put it in your own private git repo.
  If you delete the agentware package tomorrow, your knowledge base still stands
  on its own.
- **An orchestrator that never changes as you work.** The cloned package is
  read-only during normal use. Everything mutable goes to *your* directory.
- **Deterministic recall.** A small toolkit (`scripts/agentware`) is the only
  thing that writes structured data, and agents look things up with `query` /
  `audit` instead of re-reading the whole base — so memory doesn't cost a fortune
  in tokens.

---

## One-time setup

1. Clone the package and `cd` into it.
2. Run `claude`. On first launch Claude Code asks you to **trust this folder's
   hooks/settings** — approve it once (a security step).
3. Onboarding starts automatically. It will:
   - ask **where to store your knowledge base** (e.g. `~/agentware-knowledge`),
   - run `scripts/agentware init` to build that directory,
   - interview you briefly + look at your system,
   - install the three aliases below and **verify they work**,
   - write the `.initialized` sentinel.

After that you never run onboarding again.

> Your knowledge dir is pinned in `~/.agentware/config.env` (in your HOME, not the
> package). Override per-shell anytime with `export AGENTWARE_KNOWLEDGE_DIR=...`.

---

## The three commands (run from anywhere)

| Command | Agent | What it does |
|---------|-------|--------------|
| `PLAN_AW` | planner | Designs a feature plan with you. Writes only `plan.md`. **Never executes.** |
| `WORK_AW` | execution | Implements the work, verifying each step, logging as it goes. |
| `REVIEW_AW` | reviewer | Read-only PASS/FAIL assessment of completed work. |

Each alias is `(cd /your/agentware && claude --agent … --dangerously-skip-permissions)`:
- The `cd` subshell means you can run them from **any directory** — they always
  load agentware's agents/steering/hooks from the package, and your terminal's
  current directory is left unchanged.
- `--dangerously-skip-permissions` means the session never stops to ask you to
  approve commands. (Plain `claude` in the repo is also low-friction: the project
  `settings.json` pre-allows the toolkit and auto-accepts edits.)

**Autonomous loop** (fire-and-forget, multi-iteration) is run from the package:
```bash
cd /your/agentware
./agentware.sh <feature>          # runs pre → main → post phases
./agentware.sh <feature> --dry-run   # preview without spawning anything
```

---

## A typical session

1. `PLAN_AW` → "I want to add X to project Y." The planner researches, asks a few
   questions, and saves a plan to `<knowledge-dir>/work/<YYMMDD-feature>/plan.md`.
2. Execute it, either way:
   - Interactive: `WORK_AW`, then point it at the plan; or
   - Autonomous: `cd /your/agentware && ./agentware.sh <YYMMDD-feature>`.
   The agent works task-by-task, verifies with your project's own commands, and
   writes a `worklog.md` next to the plan.
3. `REVIEW_AW` → a PASS/FAIL assessment + any learnings to capture.
4. Anything learned along the way is saved to your knowledge base automatically.

---

## How the memory is built (and how to use it)

Everything lives under your knowledge dir (`scripts/agentware config` shows where):

```
<knowledge-dir>/
├── MAIN.md          # your profile + active work — injected into every session
├── index.json       # the searchable index (managed by the toolkit only)
├── FEATURES.md      # generated table of contents
├── learnings/       # gotchas + facts (one file per topic)
├── projects/        # per-project context
├── configurations/  # service/env configs
├── prompts/ references/
├── skills/          # reusable procedures the agent learns as you work
├── work/<feature>/  # plans, worklogs, assessments, loop state
├── logs/            # full audit trail (see below)
└── templates/       # entry templates (installed for you)
```

You don't have to manage any of this — agents do it as they work. When you want
to look something up yourself:

```bash
scripts/agentware query --tag <tag>          # find entries by tag
scripts/agentware query --category learnings # everything in a category
scripts/agentware index validate             # check integrity
scripts/agentware audit                      # full consistency sweep
```

You may freely read or hand-edit the Markdown files. If you change which entries
exist, run `scripts/agentware index validate` afterward. Never hand-edit
`index.json` — use `scripts/agentware index add|remove` so it stays consistent.

---

## The audit log (never lose a prompt or a session)

Hooks record everything to your dir, timestamped:

- `logs/prompts.log` — **every prompt you submit**, appended immediately, so you
  never lose something you typed.
- `logs/sessions/<session-id>/` — one folder per session:
  - `main.jsonl` — the **complete, lossless** transcript of the main agent
    (prompts, assistant text, thinking, every tool call with file names, results).
  - `main.md` — readable, timestamped render of the above.
  - `subagents/<agent-id>.jsonl` + `.md` — the **full transcript of every
    subagent** the session spawned (its own thinking + tool calls), one per agent.
  - `full.md` — the main transcript with **every subagent appended at the end**,
    so one file shows the entire session including all delegated work.
- `logs/activity.log` — one line per turn / per subagent (quick index).

Go back and read any session anytime. It's your data; nothing is hidden.

> The lossless `.jsonl` files are the source of truth; the `.md` renders truncate
> only individual very-long blocks for readability. (Very large tool outputs that
> Claude Code spills to its own `tool-results/` cache are referenced, not copied.)

---

## You own your data

- It's plain files on your disk — back it up like anything else (`cp -r`,
  `rsync`, Time Machine, or your own private git repo inside the knowledge dir).
- It's portable — point a new machine's `~/.agentware/config.env` (or the
  `AGENTWARE_KNOWLEDGE_DIR` env var) at the same directory and you're back.
- The agentware package never stores any of it; you can update or re-clone the
  package without touching your knowledge.

---

## Changing agentware itself (advanced)

The package is read-only by default. If you explicitly want to change how
agentware behaves (its steering, agents, skills, or the loop), just say so — the
agent will show a **`!! WARNING !!`** that self-extension can destabilize the
system, then make the change on your confirmation. This is intentional: your
day-to-day work can never silently alter the orchestrator.

---

## Troubleshooting

- **It tries to onboard again / says FIRST_RUN** — the knowledge dir isn't
  configured or its `.initialized` sentinel is missing. Run
  `scripts/agentware config` to check; re-run onboarding if needed.
- **An alias "command not found"** — run `source ~/.zshrc` (or your rc) or open a
  new terminal; confirm the block between `# >>> agentware aliases >>>` markers
  exists.
- **Claude keeps asking permission in a plain session** — use the `*_AW` aliases
  (they skip permissions), or approve the one-time folder-trust prompt.
- **Hooks/logging not running** — make sure you launched Claude Code from the
  package directory (the aliases handle this) and that `jq` + `python3` are on
  your PATH.

---

## Requirements

Claude Code (`claude`), a POSIX shell with `bash` + `jq` + Python 3
(macOS / Linux / WSL). See the [README](../README.md) for the full list.
