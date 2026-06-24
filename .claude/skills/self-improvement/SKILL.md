# Self-Improvement Skill — How agentware Grows Its Own Skills

> **When to invoke**: at the end of any task or iteration, when you notice you
> just did something you'll likely do again — or when the post-phase reviewer
> extracts knowledge from a worklog. agentware treats incidental learnings as
> cheap (just write them) and skill promotion as a small, auditable negotiation
> with the user.

## Why this skill exists

Without an explicit promotion procedure, knowledge dies in three places:

1. **In conversation**, when the agent says "ah, I figured out X" but never
   writes it down.
2. **In `assessment.md`**, when the reviewer extracts a learning and nothing
   moves it into the live knowledge base.
3. **In `worklog.md`**, where worklogs grow long and the reusable bits get buried.

This skill closes the loop: every learning lands *somewhere* the next agent can
find it, with the right level of authority. Learnings auto-promote, skills ask
once, steering always asks.

## The decision tree

After completing a task (or reviewing a worklog), classify any new knowledge:

```
Did I learn something?
├── No  → continue to next task
└── Yes → Is it project-specific?
         ├── Yes → write learning to the EXTERNAL dir (auto, no permission)
         └── No  → Is it a reusable procedure (≥2 steps, applies to many tasks)?
                  ├── Yes → write skill to the EXTERNAL dir (auto, just inform the user)
                  └── No  → Is it an always-true rule for the PACKAGE itself?
                           ├── Yes → package/steering change → EXPLICIT request + !! WARNING !!
                           └── No  → write learning to the EXTERNAL dir (auto)

Writing to the user's external dir is always safe and needs no permission — it's
their own space and never touches the orchestrator. Only changing the PACKAGE
(steering/skills/loop) requires an explicit request and the !! WARNING !!.
```

### Examples

| Discovery | Type | Where it goes | Permission |
|-----------|------|---------------|------------|
| "This app's dev server runs on port 4173" | Project-specific fact | `learnings/<project>-setup.md` | None (auto) |
| "To debug a hydration mismatch in Next.js: do these 5 steps" | Reusable procedure | `<knowledge-dir>/skills/debug-hydration/SKILL.md` (external) | Ask user once |
| "Always run `npm ci` rather than `npm install` on CI" | Always-true rule | `AGENTS.md` (package — self-extension) | Explicit + !! WARNING !! |
| "Stuck Docker container? `docker rm -f X` then retry" | One-off fix | `learnings/docker-gotchas.md` | None (auto) |

## Heuristics

- **Skill vs learning** — promote to a skill if the procedure has ≥2 steps AND
  applies across multiple tasks. Otherwise it's a learning.
- **Skill vs steering** — steering is for rules that apply to EVERY task. Skills
  are for procedures invoked when a TRIGGER condition is met. "Always do X" →
  steering. "When X happens, do Y, Z" → skill.
- **Two-strike rule** — if you hit the same gotcha a second time and there's no
  skill yet, that's the moment to promote.

## Procedure

### Step 1 — Capture (during the task)

Whenever you discover something, jot it into the worklog immediately as a `>`
blockquote with the marker `LEARNED:`:

```markdown
> LEARNED: The dev server takes ~8s to become ready; tests that goto early see a
> 500. Mitigation: extend webServer.timeout to 15000 in the config.
```

This is cheap memory — it survives even if the task fails. Capture freely; you
promote before the task's completion promise (Step 4).

### Step 2 — Classify (at end of task / iteration)

Walk back through the worklog. For each `LEARNED:` line, run the decision tree.

### Step 3 — Promote

#### 3a. Learning (auto, no permission)

For project-specific facts and one-off fixes, run ONE command — the toolkit
writes the learning file into the external knowledge dir, fills the template,
and registers the index entry atomically:

```bash
scripts/agentware learn \
  --topic <topic> \
  --summary "<one-line summary>" \
  --tags "<tag1,tag2>" \
  --content "<learning body>"     # or '-' to read the body from stdin
```

Then append a one-liner to `worklog.md`:
`Captured learning: learnings/<topic>.md`.

NEVER hand-create the learning file, NEVER hand-edit `index.json` — `learn` is
the only writer. Do NOT ask the user; learnings belong on the record without ceremony.

#### 3b. Skill (reusable procedure) — auto, write to the EXTERNAL dir

For reusable procedures. New skills live in the operator's external knowledge
dir, NOT in the package, so the orchestrator stays immutable. Because this is the
user's own space and is non-destructive, do it AUTONOMOUSLY (no permission gate)
once the reuse threshold is met — just inform the user afterward.

1. Confirm it clears the bar: ≥2 steps AND reusable across tasks (two-strike rule).
   If it's a one-off, keep it as a learning (3a) instead.
2. Resolve `KDIR=$(scripts/agentware config --knowledge-dir-only)`, then:
   - Use `templates/skill-template.md` as the structural starting point. Fill in:
     title, **when to invoke** (specific trigger), **why it exists**,
     prerequisites, procedure, failure handling, gotchas.
   - Write `$KDIR/skills/<topic>/SKILL.md` with the `write` tool — into the
     EXTERNAL knowledge dir, never the package.
   - Register it so it is discoverable deterministically (no token re-scan):
     ```bash
     scripts/agentware index add \
       --id skill-<topic> --title "<Skill title>" --category skills \
       --path skills/<topic>/SKILL.md --tags "skill,<other-tags>" \
       --summary "<one-line summary>"
     ```
   - Validate + refresh the TOC: `scripts/agentware index validate` (exit 0),
     then `scripts/agentware features`.
   - Future agents find it via `scripts/agentware query --category skills` (or
     `--tag`), then read the exact path it returns — deterministic, not by
     re-reading everything.
   - Append to `worklog.md`: `Promoted to skill: <KDIR>/skills/<topic>/SKILL.md`.

   External skills are discovered through the index query, not Claude's native
   `.claude/skills/` auto-trigger. Making a skill a permanent part of the
   orchestrator itself (package `.claude/skills/`) is a self-extension / package
   change — see 3c.

#### 3c. Steering / package change (explicit-only, with a !! WARNING !!)

Steering and other package files (`AGENTS.md`, `CLAUDE.md`, `.claude/**`,
`steering/**`, `agentware.sh`, `scripts/**`) are the orchestrator ITSELF, shared
across every project and every user of this clone. Changing them is allowed, but
it is self-extension, not normal knowledge capture.

1. Do this ONLY when the user EXPLICITLY asks to change agentware's own behavior.
   Otherwise capture the discovery as a learning (3a) or external skill (3b).
2. Present this verbatim and wait for explicit confirmation:

   > **!! WARNING !!** You are about to modify the agentware orchestrator package
   > itself. This changes behavior for THIS and ALL FUTURE projects that use this
   > clone and can destabilize the system. This is a self-extension action, not
   > normal knowledge capture. Proceed only if you explicitly intend to change
   > agentware's own behavior.

3. On confirmation, edit the file with the `write` tool. For a steering rule keep
   DSF: open with an allowed verb (MUST/NEVER/ALWAYS/RUN/ASK/STOP/READ/IF), carry
   a stable rule ID like `R-XXX-NN`, use no hedge words.
4. Run `scripts/agentware steering lint` (must exit 0). Append to `worklog.md`:
   `Package change: <file> — "<what>" (self-extension, user-confirmed)`.

Never edit the package silently.

### Step 4 — Promote BEFORE the completion promise (not after)

Promotion is a precondition of completion, enforced at three layers
(defense-in-depth — `R-SI-03`):

1. **You, proactively** — before emitting ANY completion `<promise>`, run
   `scripts/agentware worklog scan --path <knowledge-dir>/work/<feature>/worklog.md`
   and promote every `> LEARNED:` marker it flags (Steps 2–3) until it reports 0.
   This is the intended path — do not offload it to the gate below.
2. **The loop self-heals** — if you forget and signal completion anyway, the main
   phase of `agentware.sh` refuses to finish: it detects the unpromoted markers via
   the same `worklog scan`, deletes the stale `.done`, and re-engages you with a
   promotion-only prompt. This is **bounded** (`MAX_PROMOTE_RETRIES`, default 3);
   after that it fails loud with a non-zero exit rather than looping forever.
3. **The post-phase backstop** — `run_post_hooks` runs `worklog scan` one last time
   and hard-fails the run if anything is still unpromoted. It is the safety net, NOT
   the trigger; if it ever fires, layers 1–2 were skipped.

The takeaway: promote at task end, BEFORE the promise. Zero knowledge loss is a
gate, not a suggestion.

## Avoiding skill bloat

- **Don't promote one-off fixes** — those are learnings.
- **Don't duplicate existing skills** — check both `scripts/agentware query
  --category skills` (the user's external skills) and `ls .claude/skills/` (the
  built-in package skills) first; extend a close match instead.
- **Don't promote general programming knowledge** — agentware's audience already
  knows how to write code. Capture only agentware-specific or project-specific procedures.
- **Don't pre-emptively skill-ify** — wait until you've done the thing twice.

## See also

- `.claude/skills/knowledge-base/SKILL.md` — KB conventions, `index.json` schema.
- `templates/skill-template.md` — structural starting point for new skills.
- `templates/learning-template.md` — structural starting point for new learnings.
- `AGENTS.md` — the constitutional rules; edited when a skill rises to steering level.
