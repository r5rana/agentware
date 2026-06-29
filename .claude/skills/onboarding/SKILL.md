# Onboarding Skill — agentware First-Run Flow

> **When to invoke**: when the external knowledge directory is not yet
> configured, OR its `.initialized` sentinel does not exist. Resolve the dir
> with `scripts/agentware config --knowledge-dir-only`. This is a one-time
> procedure per operator. Once it finishes, the sentinel is written and this
> skill never runs again for this instance.

agentware ships as a generic, **personal-data-free** steering framework. The
repo contains ONLY the steering: methodology, agents, skills, the loop runner,
and the deterministic toolkit. It contains **no knowledge base**. Onboarding is
the flow that asks the operator where to keep their knowledge base, creates it
OUTSIDE the repo, and personalizes it. The same clone works for anyone — each
operator points it at their own knowledge directory.

It is interactive — the agent talks to the user, asks where to store knowledge,
investigates the system (read-only), introspects agentware itself, then writes
the knowledge base into the external directory.

---

## The core design (state this to the user)

- This repo is **pure steering**. Nothing personal is ever committed to it.
- The **knowledge base lives in a directory you choose**, outside this repo
  (e.g. `~/agentware-knowledge` or anywhere you like). It holds your profile,
  projects, learnings, and configs.
- The repo finds your knowledge dir via `~/.agentware/config.env` or
  the `AGENTWARE_KNOWLEDGE_DIR` environment variable.
- Because the path is external and the config lives in HOME, you can push this
  repo publicly and share it — the clone is generic and stays read-only as you work.
- Everything mutable goes to your dir: knowledge, learnings, agent-created skills,
  per-feature work (`work/`), AND a full audit log (`logs/`) — every prompt you
  send and every session transcript, timestamped, so you never lose anything.
  This logging is automatic via Claude Code hooks; no action needed from you.

## Self-extension clause (always true)

agentware owns its own codebase. If the user wants to change how agentware works
(add an agent, refine a steering rule, add a skill, edit `agentware.sh`), the
agent treats that like any other agentware task: write a plan in
`<knowledge-dir>/work/<YYMMDD-feature>/plan.md` and run `./agentware.sh <feature>`. The
3-phase loop executes the change against agentware's own files. State this clause
back to the user during onboarding so they know agentware is extensible from day one.

---

## The steps

Run these in order. Don't skip. The whole thing should take ~5–15 minutes.

### Step 1 — Welcome and explain

Tell the user what agentware is, in plain language:

> "Hi! This directory is agentware — a clone-and-go AI context + task-execution
> framework. The repo itself holds only the steering (methodology, agents,
> skills, the loop). Your actual knowledge base — your profile, projects, and
> the gotchas you've hit — lives in a separate directory you choose, so nothing
> personal ever lands in this repo and the same clone works for anyone.
>
> The way it works: you write a short plan describing a goal, then run
> `./agentware.sh <feature>` and an AI agent works through the plan iteratively,
> verifying each step and writing a worklog as it goes.
>
> First I'll ask where you want to keep your knowledge base, then interview you
> briefly, look at your system (read-only), and personalize it. After that,
> agentware is ready."

Then ask if they're ready to proceed.

### Step 2 — Choose and initialize the knowledge directory

This is the defining step of agentware. Do it before anything else.

1. Ask the user where they want their knowledge base stored:
   > "Where should I keep your knowledge base? It must be OUTSIDE this repo so
   > nothing personal gets committed. A good default is `~/agentware-knowledge`.
   > Give me an absolute path (or press enter for the default)."

   Accept any absolute or `~`-prefixed path. Default to `~/agentware-knowledge`
   if they don't care.

2. Confirm the path is outside the repo. If they pick a path inside the repo
   working tree, warn them it would risk committing personal data and ask for a
   different location.

3. Initialize it deterministically with the toolkit. This creates the full
   directory structure (knowledge categories + `work/`, `logs/`, `skills/`,
   `templates/`), seeds an empty valid `index.json`, installs the entry templates
   into the dir (so the instance is self-contained), and writes
   `~/.agentware/config.env` (in your HOME — NOT the repo, so the orchestrator
   package stays pristine) pinning the path:
   ```bash
   scripts/agentware init --knowledge-dir <path>
   ```
   Verify it succeeded:
   ```bash
   scripts/agentware config
   ```
   It should print `knowledge_dir: <path>` and `initialized: no` (the sentinel
   is written at the end of onboarding, not by `init`).

4. Note the resolved absolute path. Throughout the rest of onboarding, write all
   knowledge files INTO this directory (call it `$KDIR` below). Resolve it any
   time with `scripts/agentware config --knowledge-dir-only`.

5. **Choose the workspace mode (power-user vs team-mode).** This decides whether
   the KB is a solo local store or a shared, git-versioned store with per-user
   provenance. It is DISTINCT from the retrieval mode in Step 7b-2 (that is
   `deterministic|semantic`); this is `power|team` and resolves env
   (`AGENTWARE_KB_MODE`) → config → **default `power`**.

   Ask:
   > "Will this knowledge base be **just you** (power-user — a local KB, today's
   > default), or **shared by a team** (team-mode — a shared **git** KB where each
   > member's learnings are attributed to them)? [recommend git for teams]"

   - Persist the choice with the toolkit (the ONLY writer of the setting):
     ```bash
     scripts/agentware config --set-kb-mode power   # or: team
     ```
     Confirm it landed: `scripts/agentware config --kb-mode-only` (prints `power`
     or `team`).
   - **If team-mode**, also capture this user's **per-user provenance handle** —
     the name stamped as `author` on entries THEY create in the shared KB (distinct
     from the KB-wide `**Handle**:` in MAIN.md). Use the handle from the Step 3
     interview (ask now if you must):
     ```bash
     scripts/agentware config --set-user-handle <handle>
     ```
     Confirm: `scripts/agentware config --user-handle-only`. From now on, when you
     create team learnings with `learn`, pass `--author <handle> --source user` so
     the entry carries that member's provenance (it feeds the existing ACR
     `source_weight` prior; omitting the flags falls back to the operator handle).
   - **If power-user**, do nothing extra — this is today's flow, byte-unchanged.

   Team-mode's shared-git setup (attach or init the shared repo) happens in
   Step 7b once versioning is configured.

6. **Choose the runtime CLI (Claude Code vs OpenAI Codex).** agentware's loop is
   runtime-agnostic: it can drive either **Claude Code** (`claude`, today's
   default) or **OpenAI Codex** (`codex`). This decides which agent binary the
   loop spawns and which form the Step 7c aliases take. It resolves env
   (`AGENTWARE_CLI`) → config → **default `claude`**, so leaving it unset is
   byte-unchanged from today.

   Ask:
   > "Which AI runtime should agentware drive — **Claude Code** (`claude`,
   > today's default) or **OpenAI Codex** (`codex`)? Both run the same AGENTS.md
   > methodology; the loop adapts the spawn for whichever you pick. [recommend
   > whichever CLI you already have installed and logged in]"

   - Persist the choice with the toolkit (the ONLY writer of the setting —
     accepts only `claude|codex`, an invalid value exits 2):
     ```bash
     scripts/agentware config --set-cli claude   # or: codex
     ```
     Confirm it landed: `scripts/agentware config --cli-only` (prints `claude`
     or `codex`).
   - **Codex note:** Codex auto-loads `AGENTS.md` but has no `--agent` subagent
     selector and no SessionStart hook, so the loop injects the persona +
     session context into the prompt for you (handled in `agentware.sh` — no
     onboarding action needed). Autonomy maps to
     `--dangerously-bypass-approvals-and-sandbox` (the faithful analog of
     Claude's `--dangerously-skip-permissions`), reversible per-run via
     `AGENTWARE_CODEX_SANDBOX=1` (swaps to `--sandbox workspace-write -a never`).
   - **If `claude` (the default)**, do nothing extra — this is today's flow,
     byte-unchanged.

   Throughout the rest of onboarding, read the chosen runtime back with
   `scripts/agentware config --cli-only` (call it `$AW_CLI`); Step 7c's aliases
   and the Step 7d preflight both branch on it.

7. **(OPTIONAL) Offer the hybrid per-phase profile (local executor).** The loop
   can route each phase — **pre** (plan), **main** (execute), **post** (assess) —
   to a different runtime/model. The **default hybrid profile** keeps plan+assess
   on **cloud Claude** (so completion is never judged by a weak model) and runs
   the token-heavy **execute** phase on a **local model** (default
   `gpt-oss-20b` via LM Studio, driven through the already-installed Codex CLI
   with `--oss --local-provider lmstudio`). This is **purely opt-in**: leaving it
   unset is byte-identical to today's all-cloud flow.

   Ask (no stdin prompt — just offer and act on the answer):
   > "Want the **hybrid profile**? Plan + assess stay on cloud Claude; the
   > execute phase runs locally on `gpt-oss-20b`. It cuts cloud usage on the
   > heaviest phase, with a safety net that falls back to cloud if the local
   > model stalls. **Default = no (all-cloud).** It needs a one-time local-stack
   > PRE-FLIGHT (LM Studio + the model); skip that and it cleanly stays cloud."

   - **If they decline (the default), do nothing** — today's flow, byte-unchanged.
   - **If they accept:**
     1. Point them at the local-stack **PRE-FLIGHT** (install LM Studio, `lms get`
        the pinned model, `lms server start`, confirm
        `curl -s http://localhost:1234/v1/models`) — see the feature's
        `PREFLIGHT.md` / `docs/loop.md` runtime-adapter section. This is a manual,
        one-time host setup (it may need `sudo` for the GPU memory limit), never
        run headless inside the loop.
     2. Persist the default hybrid profile via the per-phase setters (the toolkit
        is the only writer; strict-validated):
        ```bash
        scripts/agentware config --set-main-cli codex          # execute phase → Codex
        scripts/agentware config --set-main-local lmstudio     # serve via LM Studio (never ollama)
        scripts/agentware config --set-main-model gpt-oss-20b  # pinned local executor
        # pre + post intentionally left unset → stay cloud claude
        ```
        Confirm: `scripts/agentware config --main-cli-only` (→ `codex`) and
        `scripts/agentware config --main-local-only` (→ `lmstudio`).
   - **Safety + one-command revert (always mention):** a no-progress circuit
     breaker emits `AW_NOPROGRESS_ABORT` and aborts cleanly if the local executor
     stalls; opt-in `AGENTWARE_MAIN_FALLBACK=claude` re-runs a stalled execute
     iteration on cloud Claude; config is resolved once at run start (immutable
     mid-loop, never model-controlled). Revert anytime with one command — effective
     next run: `scripts/agentware config --set-main-cli claude`.

### Step 3 — Interview the user

Keep this concise. Ask one question at a time, or batch 2–3 related questions.
The goal is enough context to write a useful `$KDIR/MAIN.md` — not an exhaustive
intake form.

Topics to cover:

- **Identity**: What handle / username should the system call you by? (Just a
  short name — no real names, emails, or other PII required.)
- **Role**: What kind of work do you do?
- **Current focus**: What are you working on now or soon? 1–3 active projects.
- **Goals for agentware**: What do you want it to help with?
- **Stacks and tools**: Which languages, frameworks, runtimes do you use?
  Which are NOT relevant?
- **Web / UI work?**: Do you build web apps or UI? (If yes, agentware will offer
  Playwright setup in Step 6.)
- **This directory's place**: Is this a sandbox, your main workspace, inside or
  beside another repo?

Capture answers verbatim where useful. Avoid storing credentials, account
numbers, secrets, or anything sensitive unless the user explicitly ACKs that
they want it stored for future use.

### Step 4 — Investigate the system (read-only)

Run these checks to build a system profile. Stop early if any check fails; note
the failure and move on.

- `uname -a` and `sw_vers` (macOS) or `cat /etc/os-release` (Linux) — OS info.
- `pwd` — confirm the current directory.
- `ls -la ~` — top-level home layout (don't recurse).
- Common workspace locations: `ls -la ~/workspace 2>/dev/null`,
  `ls -la ~/code 2>/dev/null`, `ls -la ~/projects 2>/dev/null`.
- Toolchain detection (best-effort, ignore "command not found"):
  `git --version`, `node --version`, `python3 --version`, `go version`,
  `cargo --version`, `rustc --version`, `java -version`, `docker --version`,
  `gh --version`.
- Project markers under `pwd`: `package.json`, `Cargo.toml`, `go.mod`,
  `pom.xml`, `requirements.txt`, `pyproject.toml`, `Gemfile`, `Dockerfile`,
  `Makefile`, `.git`.
- Web-app indicators (feed Step 6): a `package.json` with a frontend framework
  dep (`react`, `vue`, `@angular/core`, `svelte`, `solid-js`, `next`, `remix`,
  `astro`, `nuxt`); existing `playwright.config.*` or `cypress.config.*`.

Summarize findings back to the user and ask if anything is missing or wrong.

### Step 5 — Self-introspect, then generate the knowledge base

Read agentware's own configuration so the knowledge base documents it accurately:
`agentware.sh`, `CLAUDE.md`, `AGENTS.md`, `.claude/agents/*.md`,
`.claude/settings.json`, `steering/*.md`, the skills under `.claude/skills/`,
`docs/loop.md`, `templates/*.md`, `README.md`.

Now write files INTO `$KDIR` (use the `write` tool — never `cat`/heredoc):

1. **`$KDIR/MAIN.md`** — the entry point. Include:
   - User profile (handle, role) — from Step 3
   - Active projects table — from Step 3
   - Goals for agentware — from Step 3
   - System reference — from Step 4 (OS, key toolchains, workspace layout)
   - **What agentware is** — a short paragraph: the 3-phase loop, the external
     knowledge base, and the self-extension clause
   - "Active work" section as an empty placeholder ready for the first task

   This file is `cat`-ed into every agent's context by the spawn hook, so keep
   it focused and current — it is the always-on operator profile.

2. **`$KDIR/learnings/system-profile.md`** — capture the Step 4 findings. Use
   `templates/learning-template.md` (in the repo) as the structural starting
   point, then register it:
   ```bash
   scripts/agentware learn \
     --topic system-profile \
     --summary "Operator system + toolchain profile from onboarding" \
     --tags "system,environment,onboarding" \
     --content -    # pipe the body in, or pass it inline
   ```
   (`learn` writes the file AND registers it in the index atomically — do not
   hand-create the file.)

3. **`$KDIR/projects/<first-project>/index.md`** (optional, if the user named an
   active project) — a project entry using `templates/project-template.md`,
   then register it with `scripts/agentware index add`.

4. Validate the knowledge base:
   ```bash
   scripts/agentware index validate     # must exit 0
   scripts/agentware features            # regenerate $KDIR/FEATURES.md
   ```

### Step 6 — Optional UI verification setup (Playwright)

If — and only if — the user works with web / UI apps, offer to set up Playwright
in their web app so agentware can run E2E checks before marking UI tasks complete.
**Always ask first; never install dependencies silently.** The full procedure
(detection, install per package manager, config, recording the path into
`$KDIR/configurations/playwright.md`) lives in
`.claude/skills/ui-verification/SKILL.md` — follow its "Manual setup" section and
record the result as a `configurations` entry via `scripts/agentware index add`.
If the user is purely doing data / ML / shell work, skip this step entirely.

If any sub-step fails, capture what was done into the worklog and proceed to
Step 7. Do NOT block the rest of onboarding on Playwright setup.

### Step 6b — Install the default skill pack into the active harness

agentware ships a curated, **security-first default skill pack** — authored by us
(never downloaded), self-vetted, and portable across harnesses. Install it into
the harness the operator chose in Step 6 (`scripts/agentware config --cli-only`,
call it `$AW_CLI`) so the skills are discoverable out of the box.

**Harness → install-path map** (the canonical in-repo source is always
`.claude/skills/`):

| Harness (`$AW_CLI`) | Skill discovery path | Steering file |
|---------------------|----------------------|---------------|
| `claude` (default)  | `.claude/skills/` (already the canonical source — **discovered as-is, no copy**) and/or `~/.claude/skills/` | `CLAUDE.md` (imports `@AGENTS.md`) |
| `codex` / generic   | `.agents/skills/` (NOT `.codex/skills`) and/or `~/.agents/skills/` | `AGENTS.md` (read natively) |
| any other harness   | a configurable custom path the operator names | `AGENTS.md` |
| unknown             | **fall back** to `.agents/skills/` + `AGENTS.md` | `AGENTS.md` |

Procedure (do this generically — **never hardcode the skill list**; install
whatever skill folders exist under `.claude/skills/`):

1. Resolve the target dir from `$AW_CLI`:
   - `claude` → the skills already live in `.claude/skills/` and Claude Code
     discovers them there. **No copy needed** — confirm and move on.
   - `codex`/generic/unknown → `DEST=.agents/skills`.
   - any other harness with a custom layout → ask the operator for the path and
     use it as `DEST`.
2. For a non-`claude` harness, copy each portable skill folder into `DEST`,
   **idempotently and without clobbering operator edits** — skip any skill whose
   target `SKILL.md` already exists:
   ```bash
   DEST=.agents/skills            # or the operator's custom path
   mkdir -p "$DEST"
   for d in .claude/skills/*/; do
     name=$(basename "$d")
     [ -f "$d/SKILL.md" ] || continue          # only real skills
     if [ -e "$DEST/$name/SKILL.md" ]; then
       echo "skip (exists): $name"             # never clobber operator edits
     else
       cp -R "$d" "$DEST/$name"
       echo "installed: $name"
     fi
   done
   ```
   This is generic over whatever skills are shipped (today: the 14 default
   skills — `sast-audit`, `skill-vetter`, `dependency-supply-chain-audit`,
   `secure-by-design`, `threat-modeling`, `ci-cd-security-audit`,
   `test-authoring`, `systematic-debugging`, `git-commit-pr-workflow`,
   `skill-creator`, `env-doctor`, `frontend-design`, `backend-verification`,
   `safe-migration`), and automatically picks up any added later.
3. Confirm the install: every skill folder under `.claude/skills/` with a
   `SKILL.md` now has a counterpart under `DEST` (or, for `claude`, is discovered
   in place).

> **The skill set self-grows.** Beyond this shipped pack, **operator-specific
> skills emerge AUTOMATICALLY from your learnings** — recurring multi-step
> procedures captured during work are promoted into new skills via the
> auto-skill-promotion queue (`scripts/agentware skill candidates`/`approve`).
> So the skill set is not fixed at onboarding: it learns and self-grows as you
> use agentware. Tell the operator this so they know new skills will appear over
> time without manual authoring.

### Step 7 — Offer git/GitHub, set KB auto-commit, install aliases, then write the sentinel

Run **7a (git/GitHub for this repo)**, then **7b (knowledge-base versioning &
auto-commit)**, then **7c (aliases — install AND verify)**, then **7d
(sentinel)**. 7a and 7b are optional; 7c is a standard step that must be VERIFIED
working before onboarding completes.

#### Step 7a — Git and GitHub setup

Ask: "Would you like this agentware repo under version control? (y/n)"

If no, skip to 7b. If yes (this concerns the **repo**, not your knowledge dir):

1. Check for an existing repo: `test -d .git && echo HAS_GIT || echo NO_GIT`.
2. If `HAS_GIT`: say so and skip to the GitHub question below.
3. If `NO_GIT`: run `git init`, then write `.gitignore` if absent (the shipped
   repo already includes one — confirm it covers `.agentware/`, `.agentware-logs/`).
   Then `git add -A` and `git commit -m "Establish agentware"` (exactly this
   message — the canonical first-commit phrase). Confirm with `git log --oneline -1`.

   CRITICAL: the external knowledge dir is OUTSIDE the repo and the config lives in
   `~/.agentware/config.env` (HOME, not the repo) — neither is ever committed.
   Verify `git status` shows no personal data staged before committing.
4. Ask: "Push to GitHub now? (y/n)"
   - If `gh` is available: ask for a repo name (default: directory basename),
     confirm public vs private, then
     `gh repo create <name> --source=. --remote=origin --<public|private> --push`.
   - If `gh` is NOT available: print the manual `git remote add origin <URL>` /
     `git push -u origin main` steps; do not hardcode URLs.

#### Step 7b — Knowledge-base versioning & auto-commit

This is about the **external knowledge dir** (`$KDIR`), NOT the repo from 7a.
With versioning on, agentware can auto-commit and push your knowledge (learnings,
index, scorecard, MAIN) after each run, so you never lose context and can sync it
across machines. Transcripts in `logs/` are gitignored, so auto-commit only ever
versions knowledge — never raw session logs.

**Recommend keeping the KB in git** for versioning, backup, and team sharing.
Then ask once and persist the answer. The setting resolves env → config →
**default ON**; this step records the operator's explicit choice in
`~/.agentware/config.env`.

1. Check whether `$KDIR` is already a git work tree:
   ```bash
   KDIR=$(scripts/agentware config --knowledge-dir-only)
   git -C "$KDIR" rev-parse --is-inside-work-tree 2>/dev/null && echo TRACKED || echo UNTRACKED
   ```
2. Ask: **"Auto-commit & push your knowledge base after each run? [recommended: yes]"**
3. Persist the choice with the toolkit (the ONLY writer of the setting — accepts
   `on|off|yes|no|1|0|true|false`):
   ```bash
   scripts/agentware config --set-autocommit yes   # or: off, if they decline
   ```
   Confirm it landed:
   ```bash
   scripts/agentware config --kb-autocommit-only    # prints 1 (on) or 0 (off)
   ```
4. Handle the KB git state:
   - **If `UNTRACKED` and the user wants auto-commit**: offer to initialize it.
     With the user's OK, `git -C "$KDIR" init`, then help them add a remote
     (`git -C "$KDIR" remote add origin <URL>` — do NOT hardcode URLs; if `gh`
     is available offer `gh repo create`). Auto-commit only ACTS once the KB is a
     git work tree with an upstream — until then it is a graceful no-op even when
     the setting is on.
   - **If the user declines git entirely**: run
     `scripts/agentware config --set-autocommit off` and tell them auto-commit is
     **inert** (stored off; also a no-op because the KB isn't tracked). They can
     enable it later with `scripts/agentware config --set-autocommit on`.
5. Note the escape hatch: a per-run `AGENTWARE_KB_AUTOCOMMIT=0 ./agentware.sh …`
   overrides the persisted setting for a single run.

#### Step 7b-1 — Team-mode shared KB (ONLY when `--kb-mode-only` == `team`)

Run this branch ONLY if Step 2 set the workspace mode to **team**
(`scripts/agentware config --kb-mode-only` prints `team`). In power-user mode,
SKIP this entirely — the flow above is unchanged.

In team-mode the KB is a **shared git repo** every member clones. Default
auto-commit **ON** (so each member's learnings sync to the shared remote) and set
up the shared repo by EITHER attaching an existing one OR initializing a new one.

1. Default auto-commit ON for the team (still ask, but recommend yes):
   ```bash
   scripts/agentware config --set-autocommit yes
   ```
2. **Attaching an existing shared KB repo?** Before adopting it, VALIDATE that it
   conforms to the framework layout — a malformed KB would break recall/rebuild
   for the whole team. Point the operator's clone at it and run:
   ```bash
   scripts/agentware attach --path <path-to-cloned-shared-kb>
   ```
   - If it prints **ATTACH OK**, adopt it: set the knowledge dir to that path
     (`scripts/agentware init --knowledge-dir <path>` is idempotent and will not
     clobber existing entries) and continue.
   - If it prints **ATTACH REFUSED** for missing structure, offer migration:
     ```bash
     scripts/agentware attach --path <path> --migrate
     ```
     which idempotently fills missing dirs/rosters (never clobbers data), then
     re-validates. If it is refused for a corrupted/divergent index, tell the user
     to run `scripts/agentware index rebuild` in that repo and re-attach.
3. **Initializing a NEW shared KB repo?** Use the EXISTING git setup from Step 7b
   item 4 (the `UNTRACKED` branch): `git -C "$KDIR" init`, add the shared remote
   (`git -C "$KDIR" remote add origin <URL>` — never hardcode URLs; offer
   `gh repo create` if `gh` is present), and push. Do NOT reimplement auto-commit
   — it is already wired and now defaults ON.
4. Explain the shared-remote model: every member clones the same KB repo;
   auto-commit pushes their knowledge (learnings, index, scorecard, MAIN) after
   each run; transcripts in `logs/` stay gitignored and local. Per-user provenance
   (`author`/`source`, set in Step 2) attributes each learning to the member who
   wrote it, so the ACR trust model stays meaningful with many writers.

#### Step 7b-2 — Retrieval mode (Mode A / Mode B)

agentware retrieves knowledge with one of two modes; ask once and persist the
answer. The setting resolves env → config → **default `deterministic` (Mode A)**.

- **Mode A — "Pure Deterministic" (DEFAULT, recommended):** BM25 (+ACR), pure
  Python stdlib, **zero install**, byte-identical forever — maximum auditability
  and portability. Works with nothing installed.
- **Mode B — "Local Semantic" (opt-in, niche):** BM25 **+ a LOCAL
  embedding model you install** (hybrid BM25+embed) to catch paraphrased matches
  BM25 misses. Still deterministic (pinned model + cached vectors) and still
  non-hallucinated (embeddings only RANK; they never author memory). Costs: a
  local model (breaks zero-install) + reproducibility pinned to that model version.
  **Measured trade-off (tell the user honestly):** on the public LongMemEval
  benchmark Mode B is a **wash-to-slight-loss** (Recall@5 +0.008 within noise;
  nDCG/MRR slightly down) at **~111× the latency (16.5 ms → 1830 ms per query)**;
  the only measured win was +0.0357 Recall@5 on a small lexically-aligned set.
  **Recommend Mode A unless the KB is paraphrase-heavy and BM25 underperforms.**
  No cloud, no LLM in the retrieval path.

1. Ask: **"Retrieval mode — A (deterministic, zero-install, recommended) or B
   (local semantic, higher accuracy, needs a local model)? [recommended: A]"**
2. Persist the choice with the toolkit (the ONLY writer of the setting — accepts
   `deterministic|semantic` and friendly aliases `A|B`):
   ```bash
   scripts/agentware config --set-mode deterministic   # or: semantic
   ```
   Confirm the EFFECTIVE mode it resolves to:
   ```bash
   scripts/agentware config --retrieval-mode-only   # prints deterministic|semantic
   ```
3. **If they choose B**, set up the LOCAL semantic backend NOW, fully
   non-interactively (flags only, no stdin — `R-SHELL-01`). The default backend is
   `fastembed` (pinned, ONNX, no PyTorch). Run these in order; if ANY step fails,
   fall back to Mode A with a clear notice (never leave a half-configured semantic
   mode):
   ```bash
   # (a) Install the PINNED optional dependency (operator-approved; R-DEP-02).
   python3 -m pip install fastembed==0.8.0
   # (b) Point SETTINGS_AW at the fastembed backend (+ optional model opt-up).
   scripts/agentware config --set-embedder agentware_embedder_fastembed
   # scripts/agentware config --set-embed-model BAAI/bge-base-en-v1.5   # optional opt-up
   # (c) Trigger the one-time model fetch + VERIFY it produces a vector.
   PYTHONPATH=scripts python3 -c "import agentware_embedder_fastembed as b; v=b.get_embedder().embed(['probe'])[0]; print('embed dim', len(v))"
   # (d) Build the derived vector cache (sole writer = index rebuild).
   scripts/agentware index rebuild
   # (e) Persist the SETTINGS_AW retrieval choice.
   scripts/agentware config --set-retrieval semantic
   # Confirm the EFFECTIVE mode actually resolves to semantic:
   scripts/agentware config --retrieval-mode-only   # should print: semantic
   ```
   If `config --retrieval-mode-only` still prints `deterministic`, the model is not
   available — the effective mode **gracefully falls back** with a notice (it never
   crashes, never misleads). In that case tell the user Mode A is active and they can
   retry the steps above anytime. They can switch back to A at any time:
   `scripts/agentware config --set-retrieval bm25`.
4. **Mode A is byte-unchanged for non-opt-in users:** if they pick A (the default),
   change NOTHING about the install — zero dependencies, zero model, zero cache.
5. Escape hatch: a per-run `AGENTWARE_RETRIEVAL_MODE=semantic ./agentware.sh …`
   overrides the persisted setting for a single run.

#### Step 7b-3 — Dream mode (unattended KB maintenance, opt-in)

**Dream mode** is a scheduled, idle-gated, **deterministic** background cycle that
moves all size-scaling KB maintenance (re-index/cache, PII redact, reliability
eval, staleness detection, git backup) OFF the interactive path — the KB stays
fresh/compacted/backed-up and the operator never feels the cost. It is **default
OFF / opt-in**, never runs while a loop session is active, and runs at low
priority. Phase 1 is strictly deterministic (no LLM, no destructive
deletes/merges, no auto-promotion). Full details live in `docs/GUIDE.md`.

1. Ask once: **"Enable dream mode — nightly unattended KB maintenance (index
   refresh, PII redact, reliability snapshot, stale report, git backup)? It's
   default OFF and never competes with active work. [recommended: off until the
   KB grows]"**
2. **If they decline (default):** do NOTHING — nothing is scheduled, nothing
   installed. They can enable it later anytime.
3. **If they accept**, enable + install the nightly schedule, fully
   non-interactively (flags only, no stdin — `R-SHELL-01`):
   ```bash
   scripts/agentware config --set-dream on
   scripts/agentware config --set-dream-schedule 03:30   # 24h HH:MM, or a 5-field cron expr
   scripts/agentware dream --install-schedule            # launchd (macOS) / cron (else)
   ```
   Confirm it resolves on and a schedule is set:
   ```bash
   scripts/agentware config --dream-only            # prints: on
   scripts/agentware config --dream-schedule-only   # echoes the schedule token
   ```
4. They can disable + remove it anytime:
   `scripts/agentware config --set-dream off` then
   `scripts/agentware dream --uninstall-schedule` (both idempotent). A per-run
   `AGENTWARE_DREAM=on` env var overrides the persisted setting for a single run.

#### Step 7c — Install the two workflow aliases (and VERIFY them)

These two aliases are the whole interface. Each launches the matching agent
persona with permissions pre-granted so the user is never asked to approve
commands mid-flow:

- `PLAN_AW`   → `agentware-planner` (drafts plans, never executes)
- `WORK_AW`   → `agentware-execution` (runs the work; the loop's POST phase self-assesses via this agent)

The **exact alias body depends on the runtime CLI chosen in Step 2** — resolve it
first with `AW_CLI=$(scripts/agentware config --cli-only)`. The Claude form uses
native `--agent` subagent selection; the Codex form (no `--agent`, no
SessionStart hook) bootstraps the persona via an initial prompt that tells codex
to read the matching `.claude/agents/<persona>.md` and adopt it.

**Always ask before writing to a shell rc; never append silently.** But do treat
this as a required, verified step — don't finish onboarding until the aliases
resolve.

1. Ask: "Install the two workflow aliases (`PLAN_AW`, `WORK_AW`)
   into your shell rc? They make the system zero-friction. (y/n)". If the user
   declines, note it and skip to 7d.
2. Detect the shell rc: `echo "$SHELL"` → `~/.zshrc` (zsh) or `~/.bashrc` /
   `~/.bash_profile` (bash). Confirm the target with the user.
3. Idempotency: `grep -q '# >>> agentware aliases >>>' <target-rc>`. If present,
   tell the user and skip to verification (step 6). If absent, proceed.
4. Resolve the absolute repo path with `pwd` (call it `$AW_REPO`) and the chosen
   runtime with `AW_CLI=$(scripts/agentware config --cli-only)`. Bake the literal
   repo path into each alias with a subshell `cd` so the aliases work from ANY
   directory and leave the user's current directory unchanged. Append the block
   matching `$AW_CLI` with the `write` tool (insert mode, end of file) — NOT
   `cat`/heredoc/`echo >>`, and substitute the real absolute path for `$AW_REPO`:

   **If `$AW_CLI` == `claude` (default):**
   ```
   # >>> agentware aliases >>>
   alias PLAN_AW='(cd "$AW_REPO" && claude --agent agentware-planner --dangerously-skip-permissions)'
   alias WORK_AW='(cd "$AW_REPO" && claude --agent agentware-execution --dangerously-skip-permissions)'
   # <<< agentware aliases <<<
   ```

   **If `$AW_CLI` == `codex`:** codex has no `--agent` selector, so launch codex
   in the repo with a persona-bootstrap initial prompt — `PLAN_AW` adopts the
   planner persona read-only (`--sandbox read-only`, never executes), `WORK_AW`
   adopts the execution persona with autonomy
   (`--dangerously-bypass-approvals-and-sandbox`, the analog of Claude's
   skip-permissions):
   ```
   # >>> agentware aliases >>>
   alias PLAN_AW='(cd "$AW_REPO" && codex --sandbox read-only "Read .claude/agents/agentware-planner.md and fully adopt that persona. You are the agentware PLANNER: design and write plans only, NEVER execute them.")'
   alias WORK_AW='(cd "$AW_REPO" && codex --dangerously-bypass-approvals-and-sandbox "Read .claude/agents/agentware-execution.md and fully adopt that persona. You are the agentware EXECUTION agent: implement the next plan task, verify it, and log progress.")'
   # <<< agentware aliases <<<
   ```
   Write the LITERAL absolute path (e.g. `/Users/you/agentware`), not the
   `$AW_REPO` variable, so the alias is self-contained. The `(cd … && …)` subshell
   means the runtime loads agentware's `AGENTS.md` (and, for claude, `CLAUDE.md`,
   agents, settings, and hooks) from the repo no matter where the user's terminal
   is, and their shell stays in whatever directory they were in. (Only ONE
   `# >>> agentware aliases >>>` block is written — the one matching `$AW_CLI`.)
5. Confirm the append landed: re-run the grep from step 3 (must match now).
6. **VERIFY the aliases actually work before completing onboarding:**
   - `command -v "$AW_CLI"` must succeed (the chosen runtime is on PATH). If not,
     tell the user to install/login to that runtime (Claude Code or Codex) and
     that the aliases will work once it is.
   - Confirm each alias resolves in a fresh interactive shell of the user's type:
     - zsh: `zsh -ic 'alias PLAN_AW; alias WORK_AW'`
     - bash: `bash -ic 'type PLAN_AW WORK_AW'`
     Both must print the alias definition. If they don't (e.g. the rc isn't
     auto-sourced), tell the user to run `source <rc>` or open a new terminal.
   - Report the verification result to the user explicitly (✅/❌ per alias).
7. Tell the user: "`source <rc>` (or open a new terminal), then just run
   `PLAN_AW` to plan and `WORK_AW` to execute."

#### Step 7d — Write the sentinel and announce

1. Resolve the knowledge dir: `KDIR=$(scripts/agentware config --knowledge-dir-only)`.
2. Use the `write` tool to create `$KDIR/.initialized` with:
   ```
   Onboarded: <YYYY-MM-DD HH:MM>
   Handle: <user handle from Step 3>
   Onboarded by: <current agent name, e.g. agentware-execution>
   ```
3. Verify: `scripts/agentware config` prints `initialized: yes`.
4. **If team-mode** (`scripts/agentware config --kb-mode-only` == `team`), record
   the onboarding completion event for the dashboard's provenance-mix panel:
   ```bash
   scripts/agentware config --record-onboarding
   ```
   This appends one `{ts, event:"onboarding", mode:"team", user_handle:"<h>"}`
   line to `$KDIR/logs/metrics.jsonl` (gitignored — never pushed). Power-user mode
   skips this.
5. Announce:
   > "agentware is initialized. Your knowledge base + logs live at `<KDIR>`; the
   > orchestrator package stays read-only. Every session loads your MAIN.md
   > automatically and logs your prompts + transcripts to `<KDIR>/logs/`.
   >
   > Day-to-day: run `PLAN_AW` to design a feature, then `WORK_AW` to execute it
   > (or `./agentware.sh <feature>` for the autonomous loop); the loop's POST phase
   > self-assesses via the execution agent. Plans live in `<KDIR>/work/<feature>/`.
   >
   > To change agentware itself, ask explicitly — I'll warn you first, since that
   > can destabilize the system."
6. If the user originally asked for a task before onboarding kicked in, resume it.

---

## Idempotency

If the knowledge dir is configured AND its `.initialized` sentinel exists when
this skill is invoked, do nothing and return immediately. Onboarding never
re-runs against an initialized instance. To re-onboard, the user deletes the
sentinel themselves.

## Tone

Onboarding is a conversation, not a form. Reflect the user's words back where
helpful. Don't lecture, don't pad. The user came here to do work; onboarding is
the cost of admission, paid once.
