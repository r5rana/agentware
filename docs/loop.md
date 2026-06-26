# The agentware loop

The loop is the runtime of an agentware workspace. It implements features
iteratively from structured plans. It reads a `plan.md`, works through tasks one
iteration at a time, verifies each step against the project's own checks, and
updates a worklog as it goes. The runner is `agentware.sh`.

## Three Phases

`agentware.sh` runs three phases automatically:

1. **Pre-phase** (3 tasks max). Analyze and improve the plan against the
   project's steering rules and knowledge-base conventions. Cannot change
   acceptance criteria or functional outcomes — only sharpen wording, add missing
   verifications, and reorder dependencies.

2. **Main phase** (N iterations, capped by `--max-iterations`). Execute the plan
   task by task. Tasks may span infrastructure, code, configuration, and
   knowledge-base updates. Each iteration verifies its own work using the
   project's own build / test / health commands.

3. **Post-phase** (1 task). Assess the completed work. Reviews the worklog and
   results, producing a PASS / FAIL report. Writes to `assessment.md`.

```bash
# Fire and forget — runs all three phases.
./agentware.sh my-feature

# Skip phases if needed.
./agentware.sh my-feature --skip-pre    # plan already reviewed
./agentware.sh my-feature --skip-post   # skip assessment

# Cap iterations or override the agent / preview without spawning.
./agentware.sh my-feature --max-iterations 30
./agentware.sh my-feature --dry-run

# Inject extra prompt fragments.
./agentware.sh my-feature --main-prompt "Use the dev environment, not prod."
```

By default the loop spawns Claude Code headlessly
(`claude -p --agent agentware-execution --dangerously-skip-permissions`). Set
`AGENTWARE_CLI=<your-cli>` to use a different runtime, and `AGENTWARE_MODEL` to
override the model.

## Why iterations?

The primary goal is to **prevent context rot**. LLMs accumulate errors and drift
from intent as context grows. By breaking work into iterations with fresh
context, each iteration starts clean with only the plan and the worklog as
carry-over.

The iteration boundary is about **logical completeness**, not complexity limits.
One task can span multiple commands; what matters is that the task finishes
verifiable before the next iteration begins.

## Knowledge is external

agentware ships with no knowledge base. The operator's knowledge lives in a
directory chosen at onboarding, resolved at runtime via
`scripts/agentware config --knowledge-dir-only`. Nothing personal is committed to
this repo. The deterministic toolkit (`scripts/agentware`) is the only writer of
structured knowledge data.

Work artifacts and logs are external too: each feature's plan/worklog/state lives
in `<knowledge-dir>/work/<feature>/`, and hooks record every prompt plus the full
transcript of each session AND every subagent it spawns to `<knowledge-dir>/logs/`.
The orchestrator package stays read-only across projects.

## Git-syncing the knowledge base (on by default)

If you keep your knowledge dir in its own git repo, the loop syncs it
automatically. It is **ON by default** and resolves env → config → default-ON:
the per-run env var `AGENTWARE_KB_AUTOCOMMIT=0|1` wins, then a persisted choice in
`~/.agentware/config.env`, then the default `1`. It only ever **acts** when the KB
is a git work tree with an upstream — a non-tracked / offline KB is a clean no-op.

```bash
scripts/agentware config --set-autocommit off   # persist an opt-out (or on|yes)
AGENTWARE_KB_AUTOCOMMIT=0 ./agentware.sh <feature>   # disable for one run
```

`logs/` (session transcripts) and `.loop/` (ephemeral per-feature loop state —
iteration counters, `.done`, `live-stream.log`) are both **gitignored and
untracked**, so auto-commit only ever versions knowledge, never transcripts or
loop bookkeeping. When set, the loop folds a deterministic git cadence into its
phases — scoped to **your knowledge repo only** (never your code project, never
the agentware package):

- **Pre-phase** fast-forwards the KB from upstream at a safe point (clean tree with
  an upstream). Dirty/offline/no-upstream → a graceful skip that never blocks the run.
- **After the post-phase** (not before it), the commit+push runs as a separate
  `run_kb_sync` step so the assessment the post-phase just wrote — `assessment.md`
  plus its `benchmarks/history.jsonl` + `SCORECARD.md` ledger rows — is captured in
  the SAME commit as the main work, leaving no uncommitted tail. It runs on BOTH the
  post-ran and `--skip-post` paths. Only after the zero-knowledge-loss gate passes
  (every `> LEARNED:` promoted, index valid — re-checked here in case the post-phase
  added a marker) does it stage only files under the knowledge dir into **one**
  `feat|chore(<tag>): <message>` commit, then push. The `<message>` is derived
  **deterministically** (no LLM/network) so the subject says *what was worked on*,
  not a generic file list: `run_kb_sync` reads the plan's one-line title
  (`# Plan: <title>` in `<knowledge-dir>/work/<feature>/plan.md`) and passes it as
  `--type feat --message "<title>"`. `cmd_kb_git_commit` then appends a compact
  ` — learnings: <topics>` suffix naming the changed knowledge entries (basenames
  of staged `learnings/*.md` + `skills/**`, capped at 5 with `+N more`). Full
  subject form: **`feat|chore(<feature>): <plan title> [— learnings: …]`**, e.g.
  `feat(autocommit-message-fix): Autocommit Message Fix — learnings: loop-state, msg-derivation`.
  Robust fallbacks (never crashes/empty): no plan title → the changed-knowledge
  topics become the summary; no knowledge either → the legacy `sync <dirs> (N files)`
  dir-list. The whole subject is truncated to one valid line (≤100 chars) that still
  matches the `feat|chore(<tag>): …` format check.
- **On push conflict**, conflicts in the *derived* files (`index.json`, the
  `*/index.md` rosters, `FEATURES.md`) are resolved by **rebuilding them from entry
  frontmatter** (`index rebuild`) — never by an agent. Only a same-entry *prose*
  conflict invokes a curated `MERGE_PROMPT` (reusing the execution agent), and even
  then the derived files are rebuilt afterward, not merged.
- **Before every push**, a mechanical *nothing-lost* gate verifies the merged set of
  entry IDs is a superset of both parents' and that the index validates; otherwise it
  aborts and fails loud (with a bounded retry on re-push races).

Full details — the `feat/chore(tag)` convention, the `MERGE_PROMPT`, and the
nothing-lost gate — are in `docs/GUIDE.md` ("Syncing the knowledge base over git").
The same steps are available as `scripts/agentware kb-git status|pull|commit|push`
for driving the sync by hand.

## Watch a run live

A `PostToolUse` hook (`scripts/hooks/log-tool.sh`) streams **one line per tool
call** to a per-session live log as the agent works — so you can watch progress in
real time instead of waiting for the turn to end. Two ways to view it:

**1. Auto-stream in the `agentware.sh` terminal (default).** Every `./agentware.sh`
run prints each tool action right in its own window as it happens (main phase
included). Opt out with `--no-stream` or `AGENTWARE_NO_STREAM=1`.

**2. Manual `tail -f` (headless / detached runs).** When the auto-stream terminal
isn't attached (e.g. a backgrounded or remote run), follow a session's live log
directly. Tail the newest session:

```bash
KDIR="$(scripts/aw-knowledge-dir)"
sid="$(ls -t "$KDIR/logs/sessions" | head -1)"
tail -f "$KDIR/logs/sessions/$sid/live.md"
```

`live.md` is the human-readable stream (`[ts] 🔧 <tool> <input summary> → ok|ERR`);
`live.jsonl` next to it is the machine-readable equivalent. Both are a streaming
**view** — the lossless per-turn snapshot still lands in `main.{jsonl,md}` at `Stop`.

## Multi-domain tasks

- **Infrastructure**: any environment the user manages (containers, VMs, cloud,
  on-prem). agentware does not assume which.
- **Code**: changes to the user's source code, in any language.
- **Configuration**: configs the user deploys to running services.
- **Knowledge capture**: documenting learnings, resource references, project context.

**Priorities (in order):**
1. Working code / completed tasks.
2. Verified results — the project's own checks pass.
3. Knowledge base updated with what was built and any new learnings.
4. No partial implementations or TODOs left behind.

## Creating work for the loop

Each feature gets a subdirectory in the EXTERNAL knowledge dir:

```
<knowledge-dir>/work/<YYMMDD-feature-name>/
├── design.md         # Feature design document (optional)
├── plan.md           # Implementation plan
├── .loop/
│   └── .iteration    # Current iteration number (managed by agentware.sh)
└── worklog.md        # Progress notes (created during the main phase)
```

## Writing effective plans

A good plan tells the loop **what** to build and **when it's done**. The agent
figures out implementation details.

### Three layers

1. **Context** — background, references to relevant knowledge-base entries,
   workspace info, dependencies on other plans.
2. **Tasks** — concrete deliverables.
3. **Acceptance Criteria** — verifiable conditions stated in the project's own
   commands (file exists, test passes, health endpoint returns 200, etc.).

### Task format

```markdown
## Tasks

### Phase 1: Foundation

- ⬜ **1.1** Set up the local development database
  - Postgres 16 reachable on `localhost:5432`
  - Schema migrated via `./scripts/migrate.sh`
  - Verify with `psql -c '\dt'` returning the expected tables

- ⬜ **1.2** Configure the application config file
  - `config/local.yaml` written with the database URL
  - `npm run start` boots without errors

### Phase 2: Knowledge base update

- ⬜ **2.1** Document the new local-dev setup in the knowledge base
  - New entry created under `projects/<project>/` (in the external knowledge dir)
  - `index.json` updated via `scripts/agentware index add`
  - `MAIN.md` updated with a brief active-work line
```

### Status markers

| Marker | Meaning |
|--------|---------|
| ⬜ | Not started |
| 🟡 | In progress |
| ✅ | Complete |

## Plan-format contract (R1–R9) — enforced by `plan lint`

The loop counts open tasks with ONE regex
(`^[[:space:]]*-[[:space:]]*(⬜|🟡)[[:space:]]*\*\*[0-9]`). If a plan's tasks are
written as GitHub checkboxes (`- [ ]`) or letter ids (`**T1**`) instead of the
canonical `- ⬜ **N**` markers, that regex matches **zero** lines and the run
silently no-ops with "nothing to do". To make a malformed plan fail **loudly and
specifically before any agent is spawned**, the deterministic linter
`scripts/agentware plan lint --path <plan.md> [--strict] [--format json]` asserts
a fixed set of **structural** invariants (NOT prose/wording — that stays the
pre-phase agent's and planner's job). It is **read-only** and never edits the plan.

`run_pre_hooks` runs `plan lint --path "$DOCS_DIR/plan.md" --strict` right after
`index validate`, so a malformed plan aborts the run before the pre-phase (an
absent `plan.md` skips cleanly).

| Rule | Severity | Checks |
|------|----------|--------|
| **R1** file | hard-fail | the plan file exists and is readable |
| **R2** sections | hard-fail | both a `## Tasks` and a `## Acceptance criteria` section are present |
| **R3** markers | hard-fail | ≥1 task line matches the canonical marker regex, and every task marker in `## Tasks` is well-formed (`⬜`/`🟡`/`✅` + `**<digit>**`); `- [ ]` / `**<letter>` lines are flagged with the `- ⬜ **N**` fix hint |
| **R4** numbering | hard-fail | task numbers increase monotonically from 1 (no dups, no gaps) so `set-status <N>` is unambiguous |
| **R5** per-task verify | hard-fail | every task has a `*Verify:*` line (encodes `R-VERIFY-01`) |
| **R6** end-to-end task | hard-fail | ≥1 task carries the literal `[e2e]` token AND its `*Verify:*` line is non-empty |
| **R7** kb-update task | hard-fail | ≥1 task references a KB update (mentions `R-KB`, `index validate`, `learn`, or `features`) |
| **R8** promise | hard-fail | exactly one content-form promise tag `<promise>[A-Z0-9_]+</promise>` is present (a bare/prose `<promise>` mention does not count) |
| **R9** autonomy | warn (hard-fail under `--strict`) | flags any task whose body has BLOCKING/interactive phrasing ("STOP and ask", "await confirmation", "ask the user", …) UNLESS the same task states a deterministic resolution or cites an `R-PKG-03`/`R-EXEC-06` carve-out |

Exit 0 on pass; non-zero with a precise, line-numbered, actionable message on any
hard failure. `--format json` emits `{ok, errors[], warnings[]}` where each entry
is `{rule, line, message}`. The R3 regex is defined ONCE as `PLAN_TASK_MARKER_RE`
in `scripts/agentware` and pinned byte-identical to the loop's `grep` pattern by a
contract test, so the linter and the loop can never drift.

The `[e2e]` token (R6) is a literal tag the **planner** places on the final
verification task; the linter only checks its presence + a non-empty `*Verify:*`,
never the semantic quality of the step (that stays the agent's judgment). The
planner runs `plan lint --strict` before handoff so format violations are caught
at authoring time rather than at run time.

## Running features in parallel

The loop runs one feature at a time. To run **many** features at once — each in an
isolated package + KB worktree, then integrated through a serial, gate-checked
merge queue that never drops a feature — see [`docs/fanout.md`](./fanout.md)
(`scripts/agentware fanout spin-up | list | merge-policy | merge-queue | teardown`).

## Agent steering & rules

For execution methodology, knowledge-base rules, commit behavior, and naming
conventions, refer to `AGENTS.md` at the root — the single, canonical,
always-loaded methodology (Deterministic Steering Format). Human-facing rationale
and worked examples live in `docs/methodology.md` (not agent-loaded).
