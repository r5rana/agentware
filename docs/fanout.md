# Parallel-lane execution — `agentware fanout`

The loop (`docs/loop.md`) runs **one** feature at a time. `fanout` runs **many**
features at once — safely — and then integrates them through a serial, gate-checked
merge queue that never drops a feature.

It is pure orchestration: it drives `git` + spawns the **existing** loop + runs the
**existing** gates. It never edits knowledge-base entry content and never touches
ranking/retrieval. Stdlib-only (subprocess `git`), zero new dependencies.

---

## Why two isolations per lane

agentware has **two write surfaces** that collide under naïve parallelism:

1. **The package repo** funnels almost every feature into one ~10k-line file
   (`scripts/agentware`). Concurrent branches collide on the argparse dispatch
   table + shared helpers, and a careless rebase can *silently drop a subcommand*.
2. **The external KB** is shared state: every loop writes `index.json`, the
   `*/index.md` rosters, `FEATURES.md`, the append-only
   `benchmarks/history.jsonl` / `SCORECARD.md`, and worklog/plan markers.

So each lane gets **two** isolated worktrees:

- a **package** git worktree on `feat/<f>` (code isolation), and
- an **isolated KB** git worktree on `kb/<f>`, with the loop pinned to it via
  `AGENTWARE_KNOWLEDGE_DIR` (the env var that wins config precedence).

No two lanes share a working tree or a KB. All lane state (the registry, the
per-lane env files, the worktrees) lives **outside** both repos under
`~/.agentware/fanout/` (override with `AGENTWARE_FANOUT_DIR`), so neither repo's
`git status` is ever perturbed.

---

## The workflow

### 1. Spin up the lanes

```bash
scripts/agentware fanout spin-up worklog-promotion-matching-fix kb-dependency-graph \
    --base main
```

Per feature this creates `feat/<f>` + `kb/<f>` worktrees and a per-lane env file
pinning `AGENTWARE_KNOWLEDGE_DIR` to the lane's KB worktree. It is **idempotent**
(an intact lane is a no-op), refuses to clobber a branch/worktree it does not own,
and `--dry-run` prints the plan and writes nothing.

### 2. Run each lane's loop, pinned to its isolated KB

From each lane's package worktree, with the lane env sourced:

```bash
cd ~/.agentware/fanout/worktrees/<f>/pkg
set -a; . ~/.agentware/fanout/env/<f>.env; set +a   # pins AGENTWARE_KNOWLEDGE_DIR
./agentware.sh <f>
```

Each loop reads/writes only its own KB worktree (`<...>/work/<f>/plan.md`, etc.)
and finishes by writing `<...>/work/<f>/.loop/.done`. Lanes never fight over
`index.json` or the ledgers because each has its own KB.

### 3. Check lane status

```bash
scripts/agentware fanout list --format json
```

Each lane reports `{feature, feat_branch, kb_path, outcome}`. `outcome` is inferred
read-only from the lane's KB worktree:

| outcome | meaning |
|---------|---------|
| `provisioned` | spun up, plan not seeded yet |
| `in_progress` | open plan markers remain, no `.loop/.done` |
| `blocked` | finished but unpromoted `> LEARNED:` / `> DECISION:` markers remain |
| `completed` | finished and zero-knowledge-loss clean |
| `merged` | already integrated by the merge queue |
| `missing` | a worktree is gone |

### 4. Install the KB merge policy (once per KB)

```bash
scripts/agentware fanout merge-policy --kb /path/to/agentware-knowledge
```

This installs an idempotent `.gitattributes` giving `merge=union` to the
append-only/derived files (`benchmarks/history.jsonl`, `benchmarks/SCORECARD.md`,
`index.json`, `FEATURES.md`, `**/index.md`). Concurrent-lane conflicts in those
files then resolve **deterministically, never by LLM**: append-only files union,
and derived files are regenerated from the frontmatter union by `index rebuild`
(so a union'd-but-stale derived file is overwritten with the canonical result).

> This step is an **optional optimization** (it gives git a native `union`
> driver). `fanout merge-queue` does **not** depend on it: it already resolves
> append-only ledger conflicts by an in-code union and regenerates derived files
> via `index rebuild`, so correctness is the same whether or not the policy is
> installed. Install it if you also drive KB merges with plain `git`.

### 5. Integrate through the gated merge queue

```bash
scripts/agentware fanout merge-queue        # all completed lanes, in order
```

`fanout merge-queue` integrates lanes **one at a time** onto an `integration`
branch (in both repos):

- **Package side** — the monolith is the serial chokepoint, embraced. Each lane's
  `feat/<f>` is merged into `integration`. On a `scripts/agentware` conflict the
  queue **HALTS deterministically** — non-zero exit + an actionable message naming
  the lane/branch/region — and aborts the in-progress merge so the repo is left
  clean. It **never** runs a blanket `-X ours/theirs` (the exact mode that
  silently drops a subcommand). The operator resolves the conflict by hand and
  re-runs; the queue skips already-merged lanes (it is resumable). This is an
  `R-EXEC-06` operator handoff, not an interactive prompt.
- **KB side** — `kb/<f>` is merged into the KB `integration`. Derived/append-only
  conflicts resolve via the merge policy + `index rebuild`; the nothing-lost
  ID-superset gate runs **before** the commit, so a lossy merge is rejected. A
  same-entry **prose** conflict (two lanes edited the same entry two ways) halts
  for the curated prose-merge path — it is never auto-resolved here.
- **Gates after each lane** — `steering lint` (`R-PKG-04`) + `eval --record --gate`
  against the **integration** KB (`R-PKG-05`) + the test suite. Any FAIL halts the
  queue with a non-zero exit. Override the gates with `--gate "<cmd>"` (repeatable)
  or skip them with `--no-gates`; `--dry-run` reports the plan without integrating.

### 6. Tear down merged lanes

```bash
scripts/agentware fanout teardown worklog-promotion-matching-fix kb-dependency-graph
```

`teardown` removes a merged lane's worktrees **before** its branches (a branch
checked out by a live worktree cannot be deleted). It **refuses** an unmerged or
dirty lane deterministically (non-zero + message) unless you pass `--force`. It
never runs `reset --hard` or a force-push (R-GIT-02).

---

## Region grouping is guidance, not automation

`fanout` parallelizes whatever lanes you pass; it does **not** auto-decide which
features are conflict-safe — that is judgment. Group lanes so their `scripts/agentware`
edits touch **disjoint** regions (different commands/helpers); two lanes editing the
same dispatch block will conflict at `merge-queue` time and halt (by design).

Keep these **serial** (do not fan them out against each other):

- retrieval-core changes that touch ranking/recall internals,
- anything that rewrites large shared regions of the monolith,
- the README/overhaul-style capstones.

Everything else — additive subcommands, new skills, new KB connectors, separate
repos — parallelizes cleanly.

---

## Safety summary

- No two lanes share a working tree or a KB.
- Lane state lives outside both repos; neither repo's `git status` is perturbed.
- The merge queue is serial + gated; a monolith conflict or a gate FAIL halts it
  deterministically rather than dropping or mis-merging a feature.
- Destructive git only ever runs behind an explicit `--force` (R-GIT-02).

See `docs/loop.md` for the single-feature loop this builds on, and `AGENTS.md`
for the canonical methodology.
