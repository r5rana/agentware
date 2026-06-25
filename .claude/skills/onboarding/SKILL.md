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

#### Step 7c — Install the two workflow aliases (and VERIFY them)

These two aliases are the whole interface. Each launches the matching subagent
with permissions pre-granted (`--dangerously-skip-permissions`) so the user is
never asked to approve commands mid-flow:

- `PLAN_AW`   → `agentware-planner` (drafts plans, never executes)
- `WORK_AW`   → `agentware-execution` (runs the work; the loop's POST phase self-assesses via this agent)

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
4. Resolve the absolute repo path with `pwd` (call it `$AW_REPO`). Bake it into
   each alias with a subshell `cd` so the aliases work from ANY directory and
   leave the user's current directory unchanged. Append this block with the
   `write` tool (insert mode, end of file) — NOT `cat`/heredoc/`echo >>`, and
   substitute the real absolute path for `$AW_REPO`:
   ```
   # >>> agentware aliases >>>
   alias PLAN_AW='(cd "$AW_REPO" && claude --agent agentware-planner --dangerously-skip-permissions)'
   alias WORK_AW='(cd "$AW_REPO" && claude --agent agentware-execution --dangerously-skip-permissions)'
   # <<< agentware aliases <<<
   ```
   Write the LITERAL absolute path (e.g. `/Users/you/agentware`), not the
   `$AW_REPO` variable, so the alias is self-contained. The `(cd … && …)` subshell
   means Claude Code loads agentware's `CLAUDE.md`, agents, settings, and hooks
   from the repo no matter where the user's terminal is, and their shell stays in
   whatever directory they were in. (If the runtime binary is not `claude`, swap it.)
5. Confirm the append landed: re-run the grep from step 3 (must match now).
6. **VERIFY the aliases actually work before completing onboarding:**
   - `command -v claude` must succeed (the runtime is on PATH). If not, tell the
     user to install/login to Claude Code and that the aliases will work once it is.
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
4. Announce:
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
5. If the user originally asked for a task before onboarding kicked in, resume it.

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
