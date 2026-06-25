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
  thing that writes structured data, and agents look things up with ranked
  `recall` (BM25, token-budgeted) plus exact `query` / `audit` instead of
  re-reading the whole base — so memory doesn't cost a fortune in tokens. Quality
  is **measured**, not assumed: `eval` benchmarks recall against a gold set and
  records a commit-stamped trend. See "Recall, benchmarking & metrics" below.

---

## One-time setup

1. Clone the package and `cd` into it.
2. Run `claude`. On first launch Claude Code asks you to **trust this folder's
   hooks/settings** — approve it once (a security step).
3. Onboarding starts automatically. It will:
   - ask **where to store your knowledge base** (e.g. `~/agentware-knowledge`),
   - run `scripts/agentware init` to build that directory,
   - interview you briefly + look at your system,
   - install the two aliases below and **verify they work**,
   - write the `.initialized` sentinel.

After that you never run onboarding again.

> Your knowledge dir is pinned in `~/.agentware/config.env` (in your HOME, not the
> package). Override per-shell anytime with `export AGENTWARE_KNOWLEDGE_DIR=...`.

---

## The three commands (run from anywhere)

| Command | Agent | What it does |
|---------|-------|--------------|
| `PLAN_AW` | planner | Designs a feature plan with you, using `scripts/agentware recall` to surface relevant prior learnings. Writes only `plan.md`. **Never executes.** |
| `WORK_AW` | execution | Implements the work: `recall` at task start, verifies each step, promotes learnings before the completion promise, runs `audit --stale` before KB writes. The loop's POST phase self-assesses via this agent. |

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
3. The loop's POST phase self-assesses via the execution agent (PASS/FAIL + any
   learnings to capture).
4. Anything learned along the way is saved to your knowledge base automatically.

---

## How the memory is built (and how to use it)

Everything lives under your knowledge dir (`scripts/agentware config` shows where):

```
<knowledge-dir>/
├── MAIN.md          # your profile + active work — injected into every session
├── index.json       # DERIVED cache — regenerable from entry frontmatter (toolkit-only)
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

### The index is a derived cache (frontmatter is the source of truth)

Every entry file carries a machine-readable **YAML frontmatter** block at the top
— the canonical source of truth for that entry:

```yaml
---
id: learn-some-topic
title: Some Topic
category: learnings
tags: [alpha, beta]
created: 2026-06-25
summary: One-line summary.
author: <your-handle>
source: agent
last_verified: 2026-06-25
---
```

Because of this, `index.json` (plus the per-section `index.md` rosters and
`FEATURES.md`) is a **derived cache** — 100% regenerable from the entry files:

```bash
scripts/agentware index rebuild   # reconstruct index.json + rosters + FEATURES.md
```

`rebuild` is deterministic and idempotent (pure stdlib, no network/LLM): on a
clean tree a second run changes nothing. This is what makes the knowledge base
safe to sync over git — a conflict in the derived `index.json` is resolved by
**rebuilding from the entry frontmatter**, never by a fragile hand-merge.
`learn` / `index add` / `index rebuild` remain the **only** writers of the index;
hand-editing `index.json` is still forbidden. (One-time, after upgrading a KB that
predates frontmatter: `scripts/agentware index migrate-frontmatter` backfills it.)

---

## Syncing the knowledge base over git (team mode)

Your knowledge dir is plain files, so you can keep it in **its own private git
repo** and share it across machines or teammates. agentware makes that safe with a
deterministic sync model: the only file that two agents can realistically collide
on — the derived `index.json` (and the per-section rosters + `FEATURES.md`) — is
**never hand-merged**; it is **regenerated from the entry frontmatter** (the source
of truth). Everything below is scoped to your knowledge repo only — it never
touches the agentware package or your code projects.

### On by default (with a persisted opt-out)

Auto-commit + push is **ON by default**. After a run that wrote to the KB,
agentware commits the change and pushes it — **but only when your knowledge dir is
actually a git work tree with an upstream**. A knowledge dir that isn't git-tracked
(or is offline / has no upstream) is a **clean no-op**: nothing is committed, the
run is never blocked. So fresh clones whose KB isn't a repo see no behavior change
until you opt into git; once you do, versioning + backup just happen.

The setting resolves with this precedence:

1. **Per-run env** — `AGENTWARE_KB_AUTOCOMMIT=0|1` set on the command line wins for
   that one run (the escape hatch).
2. **Config file** — `AGENTWARE_KB_AUTOCOMMIT=0|1` persisted in
   `~/.agentware/config.env` (the same file that stores your knowledge dir).
3. **Default** — **ON (`1`)** when neither is set.

Persist a choice (writes the config file without clobbering your knowledge-dir
setting), and read the resolved value the loop uses:

```bash
scripts/agentware config --set-autocommit off   # persist opt-out (or: on|yes)
scripts/agentware config --kb-autocommit-only    # prints the resolved 1 / 0
```

Disable it for a single run without changing your saved preference:

```bash
AGENTWARE_KB_AUTOCOMMIT=0 ./agentware.sh <feature>
```

Onboarding asks once whether to enable auto-commit (recommended: yes) and persists
your answer here, so most operators never touch these flags by hand.

> **`logs/` is never committed or pushed.** Your knowledge dir's `logs/` (full
> session transcripts) is **gitignored and untracked**, so auto-commit only ever
> versions *knowledge* — learnings, the index, the scorecard, `MAIN.md` — never
> your transcripts. New knowledge dirs get this `.gitignore` at init; an existing
> KB is fixed once with `git -C "$KDIR" rm -r --cached logs/`.

The same plumbing is available as direct commands if you'd rather drive it by hand
(all default to the resolved knowledge dir; `--path` points at any other KB repo):

```bash
scripts/agentware kb-git status     # is_work_tree / has_upstream / is_clean
scripts/agentware kb-git pull        # fast-forward from upstream at a safe point
scripts/agentware kb-git commit      # one feat|chore(tag): commit, KB repo only
scripts/agentware kb-git push        # push, auto-resolving derived-file conflicts
```

### The cadence

1. **Pull at the start (safe-point fast-forward).** When the tree is a clean work
   tree with an upstream, the loop fast-forwards your KB from upstream before doing
   any work, so the run builds on the latest shared knowledge. If the tree is dirty,
   offline, or has no upstream, the pull is a **graceful skip** — it prints a notice
   and never blocks the run, and it never pulls onto uncommitted writes
   (`--ff-only`, so it never invents a merge commit).

2. **Commit at the end (after learnings are promoted).** When a run wrote to the KB,
   the commit fires at the **tail of the post-phase, only after the zero-knowledge-
   loss gate passes** — i.e. every `> LEARNED:` marker has been promoted and the
   index validates. It stages **only** files under your knowledge dir and makes
   **one** conventional commit: `feat|chore(<tag>): <message>` (the `<tag>` is
   derived from the feature/dominant category; the message summarizes the change).
   It can never stage your code project or the agentware package — a scope guard
   refuses if the target's work-tree root is the package repo.

3. **Push, resolving derived-file conflicts deterministically.** On a push that the
   remote rejects because upstream moved, agentware pulls with `--rebase`. If the
   only conflicts are in **derived files** (`index.json`, the `*/index.md` rosters,
   `FEATURES.md`), it discards the textual conflict and **rebuilds those files from
   the entry frontmatter** (`index rebuild`) — *no agent, no hand-merge*. This is
   why two agents each adding a *different* learning merge cleanly: their entry files
   live at distinct paths, and the only collision (the index) is regenerated.

### The rare case: the same entry edited two ways

If two sides edited the **same entry's prose**, that can't be regenerated — it needs
judgment. Here, and only here, agentware reconciles with a curated **`MERGE_PROMPT`**
that reuses the execution agent (no new agent type): it is told to merge **only** the
listed entry files, preserve the facts from **both** sides and the YAML frontmatter,
and **never** touch derived files. After the agent reconciles the prose, the derived
files are **unconditionally rebuilt** from frontmatter — so the agent literally
cannot corrupt the index — and the merge continues.

### Nothing-lost guarantee

Before any resolved merge is pushed, a **mechanical nothing-lost gate** runs (no
LLM): it rebuilds + validates the index, then checks that the merged set of entry
IDs is a **superset of the union** of both parents' entry IDs (read straight from
each parent commit's frontmatter, no checkout). If any entry ID was dropped or the
index is invalid, the merge is **aborted and fails loud** — a lossy merge can never
reach the remote. Re-push races (upstream moving again mid-resolve) retry the whole
pull→resolve→push cycle a bounded number of times (default 3) and then fail loud
rather than loop forever.

> **Determinism first, agent last, nothing silently lost.** Structured/derived files
> are *always* regenerated, never merged. An agent is involved *only* for same-entry
> prose, and even then it can't touch the derived files. Every merge is gated on an
> entry-ID superset check before it's allowed to leave your machine.

---

## Recall, benchmarking & metrics (deterministic — you own the numbers)

`query`/`audit` find entries by **exact** tag, id, or category. On top of that,
agentware ships a deterministic, **stdlib-only** retrieval + measurement spine.
Every command below is **read-only** over your knowledge base (it never writes
`index.json` or any entry — the toolkit's `learn`/`index add` stay the sole
writers), and every ranking is reproducible: identical inputs produce
byte-identical output. There is **no LLM, no embedding model, and no network** in
any of it.

### `recall` — ranked relevance retrieval

```bash
scripts/agentware recall "<free-text query>" \
  [--top-k 5] [--token-budget 1500] [--category learnings] [--format text|json]
```

`recall` ranks every entry (built from its title + summary + tags + file body)
with a hand-rolled **BM25** scorer (`k1=1.5`, `b=0.75`) over a fixed tokenizer
(lowercase, split on non-alphanumeric, no stemming), then trims the result so the
cumulative estimated context (≈ chars/4) stays under `--token-budget`. Ordering is
deterministic: score desc → `created` desc → `id` asc. This is what the execution
agent runs at task start (`R-CTX-05`) to inject a **small, focused** set instead of
dumping all of `MAIN.md` — it reduces context cost, it never increases it. Use
`--format json` for a stable machine-readable schema (`id`, `path`, `score`,
`summary`, `estimated_tokens`).

### `eval` — measure recall quality against a gold set

```bash
scripts/agentware eval [--strategy tag|bm25] [--top-k 5] \
  [--gold <path>] [--ablate] [--record] [--gate] [--tolerance 0.02] \
  [--format text|json]
```

`eval` scores a retrieval strategy against an operator-owned gold set
(`<knowledge-dir>/benchmarks/recall-gold.json`, a list of
`{ "query": …, "expected_ids": [ … ] }`) and reports **Recall@k**, **precision@k**,
**nDCG@k**, **MRR**, mean/p50 **latency**, and the **context-token footprint** the
returned set injects. `--strategy tag` scores the legacy exact-tag path (the
untouched-agentware baseline); `--strategy bm25` scores `recall`.

- `--ablate` runs the same gold set through **both** strategies and prints the
  per-metric lift — the concrete proof BM25 recall beats tag-only retrieval.
- `--record` appends one **immutable, commit-SHA + UTC-date-stamped** row (with a
  0–100 composite reliability score) to `<knowledge-dir>/benchmarks/history.jsonl`.
  The ledger is strictly **append-only**: rows are never edited or deleted.
- `--gate [--tolerance T]` compares this run to the best prior comparable row and
  **exits non-zero** if any headline metric (or the reliability score) regresses
  beyond tolerance; it implies `--record`, and seeds + passes on the first run.

### `bench scorecard` — human-readable trend

```bash
scripts/agentware bench scorecard
```

Regenerates `<knowledge-dir>/benchmarks/SCORECARD.md` from the ledger: a table of
commit · date · Recall@5 · nDCG@5 · MRR · p50 latency · token footprint ·
reliability, newest first, plus the latest ablation delta. The `.md` is a derived
**view**; `history.jsonl` is the source of truth.

### `audit --stale` — freshness & conflict flagging (advisory)

```bash
scripts/agentware audit --stale [--max-age-days 120]
```

Lists entries in volatile categories (`learnings`, `configurations`) whose
`last_verified` is older than the window, plus same-category near-duplicate/conflict
pairs (token-set Jaccard ≥ 0.6). It is **advisory only** — it reports, never deletes
or rewrites, and never flips the audit exit code.

### `metrics` — execution observability

```bash
scripts/agentware metrics [--session <sid> | --feature <name> | --since YYYY-MM-DD] \
  [--format text|json]
```

Parses your own session transcripts (`logs/sessions/<sid>/main.jsonl` plus
`subagents/*.jsonl`) into per-session + aggregate rows: turn count, wall time, token
usage (input / output / cache-creation / cache-read), tool-call counts by tool, and
subagent count. Read-only; tolerant of malformed or missing fields.

### Every package update is benchmarked

`scripts/agentware audit --with-tests` runs the full `unittest` suite **and** the
benchmark gate, so a package change cannot ship a beyond-tolerance regression
(`R-PKG-05` makes `eval --record --gate` a mandatory post-edit step). That is how
"reliability" stays a **recorded, trended number** rather than a claim.

> **v1 is BM25; embeddings are a future, pluggable boundary.** Ranking is
> deterministic BM25 by deliberate design — it protects the non-hallucinated,
> reproducible, git-versioned memory that is agentware's edge. A vector/embedding
> or graph backend, if ever added, stays **optional and pluggable behind the same
> `recall` interface**, with the deterministic stdlib path remaining the default;
> the package never hard-requires a model or network service.

---

## The audit log (never lose a prompt or a session)

Five hooks record everything to your dir, timestamped. Two are **boundary**
captures (they fire at lifecycle edges and write the lossless record), one is a
**streaming** capture (it fires after every tool call and writes the live view):

| Hook | Fires when | Writes |
|---|---|---|
| `UserPromptSubmit` (`log-prompt.sh`) | a prompt is submitted | `logs/prompts.log` |
| `PostToolUse` (`log-tool.sh`) | **after every single tool call** (live) | `sessions/<sid>/live.{md,jsonl}` |
| `SubagentStop` (`log-subagent.sh`) | a subagent finishes | `sessions/<sid>/subagents/<agent-id>.{jsonl,md}` |
| `Stop` (`log-stop.sh`) | an assistant turn finishes | `sessions/<sid>/main.{jsonl,md}` + `full.md` |
| `SessionStart` (`session-start.sh`) | a session starts | injects your `MAIN.md` context |

- `logs/prompts.log` — **every prompt you submit**, appended immediately, so you
  never lose something you typed.
- `logs/sessions/<session-id>/` — one folder per session:
  - `live.md` / `live.jsonl` — the **streaming view**: one line appended **per
    tool call, as it happens** (so you can `tail -f` a run in real time, before
    the turn ends). `live.md` is human-readable
    (`[ts] 🔧 <tool> <input summary> → ok|ERR`); `live.jsonl` is its
    machine-readable twin. Large tool input/response is truncated (~1500 chars)
    for size; the lossless copy lives in `main.jsonl`.
  - `main.jsonl` — the **complete, lossless** transcript of the main agent
    (prompts, assistant text, thinking, every tool call with file names, results).
  - `main.md` — readable, timestamped render of the above.
  - `subagents/<agent-id>.jsonl` + `.md` — the **full transcript of every
    subagent** the session spawned (its own thinking + tool calls), one per agent.
  - `full.md` — the main transcript with **every subagent appended at the end**,
    so one file shows the entire session including all delegated work.
- `logs/activity.log` — one line per turn / tool call / subagent (quick index).

Go back and read any session anytime. It's your data; nothing is hidden.

> **Boundary vs streaming.** `main.{jsonl,md}` and the `subagents/` snapshots are
> written at lifecycle *edges* (turn / subagent end) — lossless, but they only
> land once the turn finishes. `live.{md,jsonl}` is written *as each tool runs*,
> so you can watch progress mid-turn; it is a truncated **view**, not the record
> of truth. `PostToolUse` fires for subagent tool calls too (attributed to the
> parent session), so `live.*` reflects full-depth activity. To watch a run live,
> see "Watch a run live" in `docs/loop.md`.

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
