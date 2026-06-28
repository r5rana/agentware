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
2. Run your agent runtime — `claude` (Claude Code) or `codex` (OpenAI Codex).
   On first launch Claude Code asks you to **trust this folder's hooks/settings** —
   approve it once (a security step).
3. Onboarding starts automatically. It will:
   - ask **which runtime** to use (Claude Code or OpenAI Codex) and persist it via
     `scripts/agentware config --set-cli`,
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
   derived from the feature/dominant category). The `<message>` is built
   **deterministically** (no LLM) to say *what was worked on*: the plan's one-line
   title (`# Plan: <title>`) plus a ` — learnings: <topics>` suffix naming the
   changed knowledge entries — i.e. `feat|chore(<feature>): <plan title>
   [— learnings: …]`, truncated to one valid ≤100-char line. With no plan title it
   falls back to the changed-knowledge topics, and with neither to a
   `sync <dirs> (N files)` summary (never empty). See `docs/loop.md`.
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
byte-identical output. In the **default mode (Mode A)** there is **no LLM, no
embedding model, and no network** in any of it. An **opt-in** local-semantic mode
(Mode B) adds a LOCAL embedding model you install yourself — still **no LLM in the
retrieval path and no remote API** (see *Retrieval modes* below). Mode A stays the
default and is never affected by Mode B.

### `recall` — ranked relevance retrieval

```bash
scripts/agentware recall "<free-text query>" \
  [--top-k 5] [--token-budget 1500] [--category learnings] [--format text|json] \
  [--strategy bm25|bm25+acr] [--acr] [--as-of YYYY-MM-DD]
```

`recall` ranks every entry (built from its title + summary + tags + file body)
with a hand-rolled **BM25** scorer (`k1=1.5`, `b=0.75`) over a fixed tokenizer
(lowercase, split on non-alphanumeric, no stemming), then trims the result so the
cumulative estimated context (≈ chars/4) stays under `--token-budget`. Ordering is
deterministic: score desc → `created` desc → `id` asc. This is what the execution
agent runs at task start (`R-CTX-05`) to inject a **small, focused** set instead of
dumping all of `MAIN.md` — it reduces context cost, it never increases it. Use
`--format json` for a stable machine-readable schema (`id`, `path`, `score`,
`summary`, `estimated_tokens`, `strategy`, `as_of`).

`--strategy` chooses the ranker: `bm25` is the plain keyword baseline; `bm25+acr`
layers the **ACR provenance + freshness prior** over it (see *ACR re-ranking* below).
`--acr` is shorthand for `--strategy bm25+acr`. When you pass neither, bare `recall`
inherits the **gated default**: `bm25+acr` iff the most recent recorded ACR win gate
passed, else plain `bm25` — so agents get the better ranker for free the moment it
earns it, with zero steering edits. `--as-of YYYY-MM-DD` pins the date ACR uses for
freshness decay (default: today); pin it for byte-identical reproducible runs.

### `eval` — measure recall quality against a gold set

```bash
scripts/agentware eval [--strategy tag|bm25|bm25+acr] [--top-k 5] \
  [--gold <path>] [--ablate] [--baseline tag|bm25|bm25+acr] [--treatment tag|bm25|bm25+acr] \
  [--record] [--gate] [--tolerance 0.02] [--as-of YYYY-MM-DD] \
  [--acr-gate] [--acr-margin 0.01] [--acr-alpha 0.05] [--format text|json]
```

`eval` scores a retrieval strategy against an operator-owned gold set
(`<knowledge-dir>/benchmarks/recall-gold.json`, a list of
`{ "query": …, "expected_ids": [ … ] }`) and reports **Recall@k**, **precision@k**,
**nDCG@k**, **MRR**, mean/p50 **latency**, and the **context-token footprint** the
returned set injects. `--strategy tag` scores the legacy exact-tag path (the
untouched-agentware baseline); `--strategy bm25` scores `recall`; `--strategy
bm25+acr` scores the ACR-re-ranked path.

- `--ablate` runs the same gold set through **both** `--baseline` and `--treatment`
  strategies and prints the per-metric lift. Defaults are `tag` vs `bm25` (the proof
  BM25 beats tag-only); pass `--baseline bm25 --treatment bm25+acr` to measure the
  ACR lift over plain BM25.
- `--as-of YYYY-MM-DD` pins the date ACR uses for freshness decay (default: today).
  **Pin it for reproducible benchmarks** — the same gold set + same `as_of` yields
  byte-identical numbers.
- `--record` appends one **immutable, commit-SHA + UTC-date-stamped** row (with a
  0–100 composite reliability score) to `<knowledge-dir>/benchmarks/history.jsonl`.
  The ledger is strictly **append-only**: rows are never edited or deleted.
- `--gate [--tolerance T]` compares this run to the best prior comparable row and
  **exits non-zero** if any headline metric (or the reliability score) regresses
  beyond tolerance; it implies `--record`, and seeds + passes on the first run.
- `--acr-gate` runs the bm25-vs-bm25+acr ablation at `--as-of` and applies the
  deterministic **5-part ACR win gate** (see below); it **exits non-zero on FAIL**
  and is distinct from `--gate`. `--acr-margin` (default `0.01`) tunes the primary
  nDCG-lift threshold and `--acr-alpha` (default `0.05`; table supports `0.05`,
  `0.01`) the paired-t significance level. Add `--record` to log the decision +
  evidence and auto-flip the `recall` default on a PASS.

### ACR re-ranking — provenance + freshness prior (benchmark-gated default)

`bm25+acr` layers a gentle, deterministic **ACR** (Authority · Confidence ·
Recency) prior over the BM25 score so trusted, fresher entries get a nudge —
**without** overriding relevance and **without** breaking reproducibility.

- **Formula.** `acr(entry, as_of) = source_weight(entry.source) × freshness(entry.last_verified, as_of)`.
  `source_weight` is a provenance prior (`user=1.0`, `agent=0.9`, `imported=0.8`;
  unknown/missing → `0.9`). `freshness = max(FLOOR, 0.5 ** (age_days / H))` with
  half-life `H=90` days and `FLOOR=0.85`, where `age_days = as_of − last_verified`
  (clamped ≥ 0; `last_verified` falls back to `created`). All constants are tunable.
- **Blend.** `final = bm25_relevance × acr`. The blend is **relevance-dominant** and
  multiplicative: a near-zero BM25 entry stays near-zero, so ACR only **reorders
  within the set BM25 already surfaced** — it never injects a zero-relevance entry.
- **The `as_of` determinism contract.** Decay needs a "now", but a wall-clock would
  break byte-identical output. So `as_of` is an **explicit parameter** of `recall`
  and `eval` (default = today; **pin it** in tests/benchmarks). ACR is then a pure
  stdlib function of `(last_verified, as_of)` — no hidden clock, no LLM, no network.
  At a fixed `as_of`, `recall --strategy bm25+acr` is **byte-identical across runs**.
  Date-awareness (ranking shifts as entries age) is an intended feature, not a
  regression — the input is explicit, so reproducibility is preserved.
- **The win gate (`eval --acr-gate`) decides the default.** ACR becomes the default
  **only if** it wins a rigorous gate; **all five** parts must hold:
  1. **Primary** — mean `nDCG@k(bm25+acr) − nDCG@k(bm25) ≥ MARGIN` (default `+0.01`).
  2. **Significant** — a stdlib **paired t-test** on per-query nDCG deltas
     (`df = n−1`, `|t| ≥ t_crit(α)` from a critical-value table; no scipy, no bootstrap).
  3. **Win-rate** — queries improved > queries regressed (descriptive sign check).
  4. **No-harm guardrails** — `Recall@k` and `MRR` not worse (within ε), p50 latency
     within budget, and determinism holds (byte-identical at fixed `as_of`).
  5. **Age-stratified no-regression** — split the gold set by whether the correct
     answer is a RECENT vs OLD entry; ACR must **not** regress nDCG on the
     **OLD-answer** stratum (so it can't "win" merely by boosting recent entries).
- **Ledger-driven auto-flip.** `eval --acr-gate --record` writes one immutable
  `mode="acr-gate"` row (full decision + both strategies' numbers, t-stat,
  per-stratum) to `history.jsonl` and regenerates `SCORECARD.md`. Bare `recall` then
  derives its default by reading the **most recent** acr-gate row: PASS → `bm25+acr`,
  FAIL → `bm25`. There is no config flag to drift — one append-only source of truth.
  Plain `--strategy bm25` always stays reachable for baselining / eval reproducibility.

> On a provenance/freshness-**homogeneous** KB (e.g. mostly recent, agent-authored
> entries) ACR is a near-constant multiplier, so the gate honestly **FAILs** and the
> default stays `bm25` — the flip earns its way in only when ACR delivers measurable
> lift. The reorder + flip behaviour is proven on diverse synthetic fixtures in
> `tests/test_recall.py` and `tests/test_acr_gate.py`.

### Retrieval modes — A (deterministic, default) / B (local semantic, opt-in)

agentware ships **two** retrieval modes. You pick one at onboarding (or switch any
time); the choice is a product decision, not a quality compromise — Mode A is a
genuinely strong, top-competitive baseline (see the numbers below), and Mode B
trades zero-install portability for extra paraphrase recall.

| | **Mode A — Pure Deterministic** (default) | **Mode B — Local Semantic** (opt-in) |
|---|---|---|
| Ranker | BM25 (+ ACR), pure stdlib | BM25 **+** a LOCAL embedding model, fused by **RRF** (`bm25+embed`) |
| Install | **Zero** — works with nothing installed | You install a LOCAL embedding model yourself |
| Reproducibility | **Byte-identical forever** | Reproducible given the **pinned model id + derived vector cache** |
| LLM in retrieval | **None** | **None** (an embedding model is not an LLM; no generation, no prompts) |
| Network | **None** | **None at query time** — LOCAL model only, no remote API |
| Best for | Max auditability, portability, determinism | Max accuracy / paraphrase recall |

- **Picking / switching.** Onboarding asks once. To change later, use the
  **SETTINGS_AW** aliases: `scripts/agentware config --set-retrieval bm25|semantic`
  (or the equivalent `--set-mode deterministic|semantic`). It persists to
  `~/.agentware/config.env`; the `AGENTWARE_RETRIEVAL_MODE` env var overrides
  per-run. Inspect the resolved state with `scripts/agentware config --format json`
  (`retrieval_mode` = what you chose, `effective_retrieval_mode` = what actually
  runs) or the bare `scripts/agentware config --retrieval-mode-only` for shell use.
  The switch takes effect on the **next** recall/eval (config is read per-run — no
  daemon, no restart).
- **The local embedder is pluggable and LAZILY imported.** Mode B reads
  `AGENTWARE_EMBEDDER_BACKEND` — a dotted module (or a path to one) exposing
  `embed(texts) -> vectors`. Two backends ship: the default real backend
  `scripts/agentware_embedder_fastembed.py` (pinned **`fastembed==0.8.0`**, ONNX /
  no PyTorch, default model `BAAI/bge-small-en-v1.5`, opt up to
  `BAAI/bge-base-en-v1.5`) and a reference `scripts/agentware_embedder_ollama.py`
  (talks to a LOCAL Ollama serving e.g. `nomic-embed-text`). The module is imported
  **only** when Mode B is active, so Mode A carries **zero** import cost and never
  depends on anything being installed.
- **The determinism contract (A unconditional / B within-machine, honestly stated).**
  Mode A is byte-identical for all time — same inputs, same bytes, no exceptions.
  Mode B is deterministic **GIVEN a pinned model id + cached vectors ON A GIVEN
  MACHINE**: the backend rounds every component to a fixed precision and RRF fuses on
  **integer ranks**, so residual float jitter can never reorder results, and
  delete+rebuild yields a byte-identical vector cache file on that machine. We do NOT
  claim Mode B is unconditionally cross-machine deterministic: ONNX float math can
  differ across CPUs/architectures, so the vector cache + the recorded benchmark
  numbers are **reproduced per-machine** (the pinned model id + `VECTOR_CACHE_VERSION`
  are recorded so a mismatch invalidates the cache rather than silently mixing). The
  vector cache is a **derived, gitignored, regenerable** artifact written **only** by
  `index rebuild` (INV-2) — never a hand-edited source of truth; change the model and
  you must rebuild it.
- **Honest fallback (Mode B never crashes).** If you configure `semantic` but **no
  local model is reachable**, the effective mode degrades to **Mode A** and a notice
  is printed to **stderr** — `retrieval_mode=semantic requested but no local
  embedding model is available; falling back to deterministic (Mode A / BM25).
  Install a local model and rebuild the vector cache to enable semantic retrieval.`
  Stdout/JSON stays Mode-A byte-identical, so capture pipelines are unaffected.
- **No new HARD dependency.** Nothing about Mode B is required to use agentware; it
  is strictly additive and off the Mode-A path.

### SETTINGS_AW — the single, extensible settings layer

All retrieval-strategy choices live in **one** config-backed store —
`~/.agentware/config.env` — that `recall`/`eval` consult per-run. This is the
**single source of truth** for the retrieval choice (and the slot for future
flags). Every key resolves **env → `config.env` → default**:

| Key | Setter | Reader | Default | Meaning |
|---|---|---|---|---|
| `AGENTWARE_RETRIEVAL_MODE` | `config --set-retrieval bm25\|semantic` (alias of `--set-mode deterministic\|semantic`) | `config --retrieval-mode-only` (effective) | `deterministic` (Mode A) | Which retrieval mode runs |
| `AGENTWARE_EMBEDDER_BACKEND` | `config --set-embedder <dotted-name\|path>` | `config --embedder-only` | _unset_ (no semantic backend) | The LOCAL embedder backend module |
| `AGENTWARE_EMBED_MODEL` | `config --set-embed-model <id>` | `config --embed-model-only` | `nomic-embed-text` | The embedding model id passed to the backend |
| `AGENTWARE_DREAM` | `config --set-dream on\|off` | `config --dream-only` | `off` (opt-in) | Enable the unattended `dream` maintenance cycle |
| `AGENTWARE_DREAM_SCHEDULE` | `config --set-dream-schedule HH:MM\|<cron>` | `config --dream-schedule-only` | _unset_ | Nightly run time the scheduler installs |
| `AGENTWARE_DREAM_MAX_RUNTIME` | `config --set-dream-max-runtime <N\|Ns\|Nm\|Nh\|off>` | `config --dream-max-runtime-only` | `1800` (30m) | Best-effort wall-clock cap; a cycle that exceeds it stops remaining steps, records a PARTIAL cycle, and exits non-zero (`off`/`0` = disabled) |

- **Resolution order** for every key: the environment variable wins (per-run
  override), then `~/.agentware/config.env` (the persisted choice), then the
  documented default. Invalid setter tokens exit non-zero and never corrupt the
  store. `config --format json` surfaces every key (`retrieval_mode`,
  `effective_retrieval_mode`, `embedder_backend`, `embed_model`,
  `semantic_embedder_available`).
- **Effect timing.** A change takes effect on the **next** recall/eval — config is
  read per-run; setters reset the lazy embedder cache so a backend/model switch is
  picked up immediately. No daemon, no restart.
- **Extensibility contract.** A future feature flag slots into the SAME store via
  the identical get/set pattern: add a `*_KEY` constant + a strict parse helper, a
  `--set-<flag>` / `--<flag>-only` pair in `cmd_config` mirroring
  `_set_config_value`, and surface it in `config --format json`. No new storage, no
  new file — SETTINGS_AW is the one place settings live.

### Per-phase runtime routing — the hybrid local-executor profile

The same SETTINGS_AW store also carries **per-phase** runtime selection, so the
loop can run each phase (pre / main / post) on a different runtime + model. The
headline use is the **hybrid profile**: keep *plan* and *assess* on cloud Claude
and run *execute* on a **local model** (`gpt-oss-20b` via LM Studio + Codex).

| Key (each phase) | Setter | Reader | Default | Meaning |
|---|---|---|---|---|
| `AGENTWARE_{PRE,MAIN,POST}_CLI` | `config --set-<phase>-cli claude\|codex` | `config --<phase>-cli-only` | global `AGENTWARE_CLI` → `claude` | Runtime for that phase |
| `AGENTWARE_{PRE,MAIN,POST}_MODEL` | `config --set-<phase>-model <id>` | `config --<phase>-model-only` | global `AGENTWARE_MODEL` → _unset_ | Model id for that phase |
| `AGENTWARE_{PRE,MAIN,POST}_LOCAL` | `config --set-<phase>-local lmstudio\|ollama` | `config --<phase>-local-only` | _unset_ | Local provider (appends `--oss --local-provider <p>` to the codex spawn) |

- **Resolution** mirrors the rest of SETTINGS_AW — phase env → phase `config.env`
  → global (`AGENTWARE_CLI`/`AGENTWARE_MODEL`) → default — except `LOCAL`, which is
  inherently per-phase (no global fallback). **No per-phase keys ⇒ byte-identical
  all-cloud.** Routing is **resolved once at run start and immutable mid-loop**;
  invalid setter tokens exit 2 and never corrupt the store. `config --format json`
  surfaces a `phase_routing` object (`{pre,main,post}×{cli,model,local}`).
- **Enable the hybrid profile** (effective next run):
  `config --set-main-cli codex && config --set-main-local lmstudio &&
  config --set-main-model gpt-oss-20b` (pre+post left unset → cloud).
- **Safety net** for a weak local executor: a no-progress circuit breaker prints
  `AW_NOPROGRESS_ABORT` and aborts cleanly after `AGENTWARE_NOPROGRESS_LIMIT`
  (default 3) stalled main iterations; opt-in `AGENTWARE_MAIN_FALLBACK=claude`
  retries a stalled iteration on cloud; pre+post stay cloud; revert with
  `config --set-main-cli claude`.
- **One-time LM Studio PRE-FLIGHT**, **benchmark KB-isolation** (sandbox KB at
  `$HOME/.agentware-bench-sandbox`, never `init`/onboarding, env passed per-command
  only), the **cost/billing-safety invariant** (subscription-only, no API key, no
  spend cap), and the **verified 24 GB pitfalls** are all documented in
  [docs/loop.md → Per-phase routing & the hybrid local-executor profile](loop.md#per-phase-routing--the-hybrid-local-executor-profile).

### Dream mode — unattended, idle-gated KB maintenance (opt-in)

agentware's **interactive** path is flat as the KB grows: recall is ranked +
token-budgeted, so a query is O(query-terms), not O(corpus). The only work that
scales with size is **maintenance** (re-index/re-cache, PII redact, reliability
eval, staleness detection, git backup). **Dream mode** moves all of that OFF the
hot path into a scheduled, idle-gated, **deterministic** background cycle so the
KB stays fresh/compacted/backed-up and you never feel the cost.

**Phase 1 is strictly deterministic + sanctioned-mutation-only — no LLM, no
destructive deletes/merges, no auto-promotion.** It runs one cycle in fixed
order, each step idempotent and individually skippable:

| Step | What it does | Mutation |
|---|---|---|
| **a** index rebuild | regenerate index.json + rosters + FEATURES + BM25 cache | derived caches (sole writer) |
| **b** bench redact | scrub `gold_path` PII from the benchmark ledger | ledger `gold_path` only |
| **c** audit `--with-tests` | full health check (incl. unittest + gate) | none (read-only) |
| **d** eval `--record` | append ONE reliability row, then redact it | one append-only ledger row |
| **e** detect & report | `audit --stale` + worklog scan → writes an actionable `logs/dream-report-latest.md` | **none** — REPORTS only |
| **f** kb-git commit | nightly backup (+ optional push), gated on autocommit | one KB commit |

```bash
scripts/agentware dream --dry-run            # show the ordered plan; mutate nothing
scripts/agentware dream --steps a,b          # run a subset
scripts/agentware dream --force              # bypass the idle-gate (lock still honored)
scripts/agentware dream                      # one full cycle (idle-gated)
```

- **Default OFF / opt-in.** Nothing runs until you enable it:
  `config --set-dream on`.
- **Idle-gate.** The cycle **skips with a logged reason** when an agentware loop
  session is active or system load is high, and runs at low priority (`nice`) so
  it never competes with interactive work. `--force` bypasses the idle-gate for a
  manual run; a single-writer lockfile (under the gitignored `.cache/`) is always
  honored so two dreams never overlap.
- **Idempotent.** Two dreams over an unchanged KB are a no-op beyond a journal
  entry + one reliability row (index rebuild is byte-stable; redact no-ops on a
  clean ledger).
- **Journal + observability.** Each cycle appends a deterministic entry to
  `logs/dream-journal.md` (machine-local, gitignored) and emits one `dream` event
  to `logs/metrics.jsonl`. Both are **enriched with failure detail**: step c
  carries `tests_ran`, `tests_failed`, and the `failed_tests` names (parsed from
  the unittest `FAIL:`/`ERROR:` lines), and the metric event records per-step
  `failed_checks` + `failed_tests` — so "which tests failed?" is answerable the
  morning after with **zero re-runs**. A read-only `dream_health` audit check is
  **inert** when dream is OFF and **warns** when it is ON but the last cycle is
  stale **or did not finish clean** — it reports the last cycle's **age (hours)**
  and **outcome** (`ok` | `partial` | `fail`, parsed from `logs/metrics.jsonl`),
  and the dashboard's `dream_health` panel surfaces both.

**Observability artifacts (all machine-local + gitignored under `logs/`).** A
dream cycle is **self-explaining**: failures and the already-detected
duplicates/markers land on disk in actionable form so the morning-after answer
needs no re-derivation.

| Artifact | Written by | Granularity | Content |
|---|---|---|---|
| `logs/dream-journal.md` | every cycle | step | per-step status + duration, plus step c's `tests_ran`/`tests_failed`/`failed_tests` and a `triage_log` pointer; `timed_out` reason on a guard trip |
| `logs/metrics.jsonl` (`dream` event) | every cycle | step | per-step `status`/`duration_s` + `failed_checks`/`failed_tests`; top-level `timed_out`/`timeout_reason` on a trip — the source `dream_health` reads for age + outcome |
| `logs/dream-failures/<started>.log` | step c, on a **material** audit failure | full output | the COMPLETE captured audit + unittest stdout/stderr for that cycle — "which 5 of 791 failed?" answered verbatim, no re-run |
| `logs/dream-report-latest.md` | step e, every cycle | item | byte-stable enumeration of each **stale** entry (id, category, age), each **duplicate/conflict** pair (ids + jaccard + category), and every **unpromoted** worklog marker (`work/<feature>/worklog.md:<line>` + kind + text). **Report-only (INV-2)** — never promotes/merges/deletes; it just makes the existing counts self-serve |
| `logs/dream-scheduler.log` | the scheduler (launchd/cron) | raw stdout/stderr | see below |

- **Scheduler output is no longer discarded.** The installed launchd plist now
  sets `StandardOutPath` + `StandardErrorPath`, and the cron line **appends**
  (`>> <log> 2>&1`) instead of the old `>/dev/null 2>&1` — both point at
  `<knowledge-dir>/logs/dream-scheduler.log` (gitignored), whose dir is created on
  install (launchd will not create it itself). A scheduled run that fails is now
  fully recoverable from disk.
- **Best-effort max-runtime guard (fully offline).** `AGENTWARE_DREAM_MAX_RUNTIME`
  (default 30m, `off` to disable) caps the cycle's wall-clock so a hung/runaway
  step cannot stack nightly. The guard is a **between-steps** check — it never
  interrupts a step already running (interrupting a sanctioned writer mid-`index
  rebuild`/git-commit risks a torn KB). On trip it stops the remaining steps,
  records a **PARTIAL** cycle + the trip reason to the journal + metric, and exits
  non-zero.
- **Fully offline — no external monitoring.** Dream makes **no network calls**
  under any configuration. There is **no** outbound-ping / dead-man's-switch
  integration (e.g. healthchecks.io) anywhere in the code — this was considered
  and **deliberately dropped** to preserve the offline, no-new-dependency boundary
  (`R-DEP-01`). The **local** `dream_health` heartbeat (age + outcome) is the
  in-box signal; an operator who wants off-box alerting points **their own**
  monitor at the local artifacts (`logs/metrics.jsonl`, `logs/dream-failures/`).

**Install the nightly schedule (opt-in).** The portable artifact is the `dream`
command; the installer is a thin, fully non-interactive wrapper that writes ONLY
into your `HOME`:

```bash
scripts/agentware config --set-dream on
scripts/agentware config --set-dream-schedule 03:30   # or a 5-field cron expr
scripts/agentware dream --install-schedule            # launchd (macOS) / cron (else)
scripts/agentware dream --uninstall-schedule          # remove it (idempotent)
```

On macOS this writes a `LaunchAgent` plist to
`~/Library/LaunchAgents/com.agentware.dream.plist`; elsewhere it writes a crontab
fragment to `~/.agentware/dream.cron` and installs it. **Manual setup** on an
unsupported platform: schedule any task runner to invoke
`nice -n 10 <repo>/scripts/agentware dream` at your chosen time — the command is
self-contained and non-interactive. To disable entirely:
`config --set-dream off` then `dream --uninstall-schedule`.

**Making a dream cycle effective (research-informed).** Two bodies of practice
shaped this design:

- **Agent sleep-time consolidation.** The field treats agent downtime as compute:
  during idle time an agent consolidates insights, promotes recurring **episodic →
  semantic** memory, extracts reusable skills, dedups/merges, and reflects — so
  test-time needs less reasoning. agentware's `dream` is that idle window. Phase 1
  does the deterministic groundwork (re-index, redact, eval, **detect** duplicates
  + unpromoted markers) and now makes that detection **actionable** via
  `dream-report-latest.md`; the LLM-driven consolidation is Phase 2 (below). _(Letta
  — Sleep-time Compute, letta.com/blog/sleep-time-compute; "Memory for Autonomous
  LLM Agents", arxiv.org/html/2603.07670v1.)_
- **Scheduled-job observability.** Best practice for cron/launchd jobs = structured
  logs (job, run-id, duration, exit_code, **failing items**) + clean exit codes + a
  **heartbeat / dead-man's-switch** ("logs tell you what happened; a heartbeat tells
  you when a job *didn't* run") + a **timeout** so a job can't run forever. The
  artifacts above add the structured failure logs + stop discarding scheduler
  output; the max-runtime guard is the timeout; `dream_health` (age + outcome) is
  the **local** heartbeat. The off-box external monitor is intentionally left to the
  operator to keep dream offline. _(CronBeacon — Cron Job Best Practices,
  cronbeacon.dev/guides/cron-job-best-practices.)_

> **Phase 2 (deferred, NOT in Phase 1):** all LLM-driven curation lives behind a
> future review queue — dedup-MERGE of duplicate learnings, re-summarize/compaction,
> skill extraction, and AUTO-PROMOTE (episodic→semantic) of
> `> LEARNED:`/`> DECISION:` markers. Phase 1 only REPORTS those (the
> `dream-report-latest.md` enumeration is the deterministic on-ramp to that
> curation).

### Benchmark methodology & numbers (LongMemEval)

The `eval --suite longmemeval` scorer is **strategy-agnostic** and reports the one
**directly-comparable** public number the agent-memory field uses: **Recall@5** on
**LongMemEval-S (cleaned)** — same metric, same dataset variant, same "no LLM in the
retrieval loop" rule. The protocol is **session-level** (the gold unit is the
evidence session), **k = 5**, with **abstention questions scored separately** from
answerable ones (never blended into the headline). Pin `--as-of` for byte-identical
reproduction.

| Mode | LongMemEval-S Recall@5 | Notes |
|---|---|---|
| **A (BM25, default)** | **0.9140** | 470 answerable, 30 abstention separated; per-category: single-session-assistant 1.000, etc. Own gold-set Recall@5 0.9554. |
| **B (local semantic)** | _pending_ | Requires an operator-installed LOCAL embedding model; the headline is recorded by the Phase 6/8 tuning + E2E run once a model is available. With no model, Mode B falls back to A (above). |

**Reproduce from scratch (anyone, no account or token needed).** The dataset is
not shipped (it is ~277 MB); it is fetched from Hugging Face pinned to an exact
commit + sha256, so the bytes you score are provably the bytes we scored.

```bash
# 1. Fetch the cleaned variant into your knowledge dir, pinned to an exact commit.
#    KDIR="$(scripts/agentware config --knowledge-dir-only)"
mkdir -p "$KDIR/benchmarks/longmemeval" && cd "$KDIR/benchmarks/longmemeval"
curl -sS -L -o longmemeval_s_cleaned.json \
  "https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/98d7416c24c778c2fee6e6f3006e7a073259d48f/longmemeval_s_cleaned.json"

# 2. Verify the bytes (MUST match, or the comparison is not apples-to-apples).
shasum -a 256 longmemeval_s_cleaned.json
#   → d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442

# 3. Run the benchmark (pure stdlib — nothing else to install for Mode A).
cd -  # back to the agentware repo
scripts/agentware eval --suite longmemeval --strategy bm25 --top-k 5 --as-of 2026-06-25
#   → Recall@5 0.9140 · nDCG@5 0.8831 · MRR 0.9104 · 470 answerable · 30 abstention separate
```

The scorer is **deterministic and read-only**: no LLM, no network, no embeddings at
score time, and it re-scores a sample to assert byte-identical ordering
(`determinism_ok`). The 30 `_abs` abstention questions are excluded from the Recall
aggregate and reported separately (Recall@k is undefined for them). Add `--record`
to append the run to the append-only ledger (`benchmarks/history.jsonl`); commit
your working tree first so the row is pinned to a clean commit rather than a dirty
tree. The full provenance (both variants, pinned commits, sha256s, schema, category
counts) is regenerated as `benchmarks/longmemeval/DATASET.md` in your knowledge dir.

For context, agentmemory's published hybrid headline is **95.2% Recall@5** — so
Mode A's **91.4%** is already top-competitive with a pure-stdlib, zero-install,
byte-identical ranker. Mode B targets closing the remaining gap; its real number is
reported honestly when measured (never fabricated, never from a test stub).

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
  your PATH. Under **OpenAI Codex** there are no `.claude/*` hooks; the loop
  reconstructs the same log sinks by piping `codex exec --json` through
  `scripts/hooks/codex-stream.py` (see [docs/loop.md](loop.md#runtime-adapter-claude-code--openai-codex)).

---

## Requirements

An agent runtime — Claude Code (`claude`) or OpenAI Codex (`codex`), chosen at
onboarding and overridable via `AGENTWARE_CLI=claude|codex` — plus a POSIX shell
with `bash` + `jq` + Python 3 (macOS / Linux / WSL). See the
[README](../README.md) for the full list.
