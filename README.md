# agentware

> ai agent that is aware and learns from your experience and creates custom knowledge bases

**A clone-and-go steering framework for AI agents. The repo is pure steering —
your knowledge base lives in a directory you choose, so the same clone works for
anyone and nothing personal is ever committed.**

agentware turns any AI agent runtime into a self-aware workspace where agents:

- **Remember** what you're working on across sessions — in an **external
  knowledge base** at a path you pick (your profile, projects, learnings, configs).
- **Execute** multi-step tasks iteratively from short plans you write (the
  **3-phase loop** in `agentware.sh`).
- **Onboard themselves** the first time they run — they ask where to store your
  knowledge, interview you, investigate your system, then personalize.
- **Extend themselves** — you can ship features into agentware the same way you
  ship features into any other project.

It is **cloud- and language-agnostic**. agentware does not assume which cloud,
language, framework, or runtime you use. Verification of every step is expressed
in *your* project's own build / test / health commands.

---

## The core idea: steering here, knowledge elsewhere

```
agentware/                 ← this repo: PURE STEERING (generic, shareable, no PII)
  CLAUDE.md, AGENTS.md, .claude/, steering/, docs/, scripts/, agentware.sh, templates/

<your-knowledge-dir>/      ← OUTSIDE the repo: YOUR knowledge (chosen at onboarding)
  MAIN.md, index.json, learnings/, projects/, configurations/, prompts/, references/
```

The repo finds your knowledge dir from (in order):
1. the `AGENTWARE_KNOWLEDGE_DIR` environment variable, then
2. `AGENTWARE_KNOWLEDGE_DIR=...` in `~/.agentware/config.env` (written by `init`, gitignored).

Because the knowledge dir is external and the config is gitignored, **you can push
this repo publicly** — the clone is generic and personal-data-free by design.

---

## Getting started

1. Clone this repo wherever you want your agentware instance to live.
2. Run **Claude Code** inside the directory (`claude`). It auto-loads `CLAUDE.md`,
   and the `SessionStart` hook detects the workspace is uninitialized and runs
   **onboarding**: it asks where to store your knowledge base, runs
   `scripts/agentware init`, interviews you, and personalizes everything. (To use
   a different runtime, set `AGENTWARE_CLI=<your-cli>`.)
3. Once onboarded, write a plan and run the loop:
   ```bash
   ./agentware.sh <YYMMDD-feature>
   ```

You can also initialize the knowledge dir yourself first:

```bash
scripts/agentware init --knowledge-dir ~/agentware-knowledge
scripts/agentware config        # shows the resolved dir + initialized state
```

📖 **New here? Read the [User Guide](docs/GUIDE.md)** — daily workflow with the
three agents, how the persistent memory layer works, and how you own all your data.

---

## The Plan → Execute loop

The runtime is `agentware.sh`. You write a short plan, then fire-and-forget:

```
<knowledge-dir>/work/<YYMMDD-feature-name>/
└── plan.md          # phases, tasks, acceptance criteria
```

```bash
./agentware.sh <YYMMDD-feature-name>
```

Three phases run automatically:

1. **Pre-phase** (3 tasks max) — review and sharpen the plan without changing
   acceptance criteria.
2. **Main phase** (capped by `--max-iterations`) — execute tasks one at a time,
   verifying each with the project's own checks, writing a `worklog.md`.
3. **Post-phase** (1 task) — assess the result and write `assessment.md`.

The plan format is explained in [`docs/loop.md`](docs/loop.md). Preview a run
without spawning anything with `./agentware.sh <feature> --dry-run`.

---

## The deterministic toolkit

`scripts/agentware` is the ONLY writer of structured knowledge data (the index,
learning files, `FEATURES.md`). The agent decides *what*; the toolkit guarantees
*how* (valid JSON, consistent tag map, no duplicates, paths relative to your
knowledge dir).

```bash
scripts/agentware init --knowledge-dir <path>   # scaffold + write config
scripts/agentware config                         # show resolved knowledge dir
scripts/agentware index add --id ... --title ... --category ... --path ... --tags ... --summary ...
scripts/agentware index validate                 # integrity check (exit 0 = ok)
scripts/agentware query --tag <tag>              # O(1) lookups by id/path/tag/category
scripts/agentware learn --topic ... --summary ... --tags ... --content -
scripts/agentware features                        # regenerate FEATURES.md
scripts/agentware audit                           # full sweep of all checks
scripts/agentware steering lint                   # enforce the Deterministic Steering Format
```

---

## Layout

```
agentware/
├── README.md                          # this file
├── CLAUDE.md                          # auto-loaded by Claude Code; imports AGENTS.md + steering
├── AGENTS.md                          # canonical methodology (DSF) — imported by CLAUDE.md
├── agentware.sh                       # 3-phase task-execution loop (claude -p)
├── scripts/
│   ├── agentware                      # deterministic toolkit (Python)
│   ├── aw-knowledge-dir               # resolves the external knowledge dir (bash)
│   └── hooks/                         # SessionStart / UserPromptSubmit / Stop hook scripts
├── docs/
│   ├── loop.md                        # the 3-phase loop + plan format
│   ├── methodology.md                 # rationale + examples (NOT agent-loaded)
│   └── design/                        # feature plans + worklogs (gitignored)
├── steering/                          # always-loaded steering imported by CLAUDE.md
│   ├── common-problems.md
│   └── project-context.md
├── .claude/
│   ├── settings.json                  # default model + hooks
│   ├── agents/                        # agentware-planner / -execution / -reviewer (subagents)
│   ├── skills/                        # onboarding, knowledge-base, ui-verification, self-improvement
│   └── commands/                      # /agentware-plan slash command
├── templates/                         # learning / project / skill entry templates
└── .gitignore

# config + ALL your data live OUTSIDE the package:
~/.agentware/config.env                # points at your knowledge dir (HOME, gitignored)
<your-knowledge-dir>/                   # knowledge, learnings, skills, work/, logs/, templates/
```

The multi-runtime bridge files at the root (`.cursorrules`, `.windsurfrules`,
`.clinerules`, `.antigravity`, `.google.agy`, `.github/copilot-instructions.md`)
all point other runtimes at `AGENTS.md`.

---

## Using the agents (zero-prompt)

The three roles are Claude Code **subagents** in `.claude/agents/`. Onboarding
installs three aliases (and verifies they work) so the whole system is one word:

```bash
# >>> agentware aliases >>>   (onboarding writes your real absolute path in place of /path/to/agentware)
alias PLAN_AW='(cd /path/to/agentware && claude --agent agentware-planner --dangerously-skip-permissions)'
alias WORK_AW='(cd /path/to/agentware && claude --agent agentware-execution --dangerously-skip-permissions)'
alias REVIEW_AW='(cd /path/to/agentware && claude --agent agentware-reviewer --dangerously-skip-permissions)'
# <<< agentware aliases <<<
```

- `PLAN_AW` — draft a feature plan (writes only `plan.md`, never executes).
- `WORK_AW` — execute the work.
- `REVIEW_AW` — read-only PASS/FAIL assessment.

The `(cd … && …)` subshell means you can run these from **any directory** — they
load agentware's agents/steering/hooks from the repo and leave your terminal's
current directory unchanged. `--dangerously-skip-permissions` means the session
never stops to ask you to approve a command. The autonomous loop is run from the
repo: `cd /path/to/agentware && ./agentware.sh <feature>`.

> First run only: Claude Code asks you once to trust this folder's hooks/settings
> (a security step). Approve it, and everything after is frictionless.

---

## Guarantees

- **The package never changes as you work.** Knowledge, learnings, agent-created
  skills, per-feature plans/worklogs (`work/`), and logs all live in your external
  dir. The orchestrator is read-only — changing it (steering/skills/loop) requires
  an explicit request and shows a `!! WARNING !!` first (self-extension).
- **Deterministic knowledge.** `scripts/agentware` is the only writer of the index
  and learnings; agents find things via `query`/`audit`/`steering lint` — not by
  re-reading the whole base and burning tokens.
- **Full audit trail in your space.** Every prompt → `<knowledge-dir>/logs/prompts.log`.
  Every session → `<knowledge-dir>/logs/sessions/<id>/`: the lossless main
  transcript (prompts, text, thinking, tool calls with file names, results), the
  full transcript of **every subagent it spawned**, and a `full.md` with all of
  them appended — timestamped, so you can replay exactly what was said and done.

---

## Requirements / platform support

- **Claude Code** (`claude` CLI) — the native runtime.
- **POSIX shell + `bash` + `jq` + Python 3** — the loop, hooks, and toolkit are
  bash/Python. This means **macOS, Linux, or Windows via WSL/Git-Bash**. Native
  Windows (PowerShell, no WSL) is not yet supported.
- Git is optional (onboarding offers `git init` + push via `gh`).
- Node.js ≥ 18 — only if you want Playwright UI verification.
