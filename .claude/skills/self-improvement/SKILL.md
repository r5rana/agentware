# Self-Improvement Skill — How agentware Grows Its Own Skills

> **When to invoke**: at the end of any task or iteration, when you notice you
> just did something you'll likely do again — or when the post-phase assessment
> (run by the execution agent) extracts knowledge from a worklog. agentware treats incidental learnings as
> cheap (just write them) and skill promotion as a small, auditable negotiation
> with the user.

## Why this skill exists

Without an explicit promotion procedure, knowledge dies in three places:

1. **In conversation**, when the agent says "ah, I figured out X" but never
   writes it down.
2. **In `assessment.md`**, when the post-phase assessment extracts a learning and nothing
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
  skill yet, that's the moment to promote. In practice you rarely promote by hand:
  the automated pipeline (dream step `k` + `scripts/agentware skill propose`)
  detects recurring, cohesive learning clusters and PROPOSES an approval-gated
  tier-2 skill-candidate workorder for you to approve+run (see 3b).

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

`--topic` MUST be a hyphenated lowercase slug — `learn` uses it VERBATIM for both
the entry id (`learn-<topic>`) and the filename (`learnings/<topic>.md`).

Then **amend the ORIGINAL `> LEARNED:` line in `worklog.md`** by appending the
canonical promotion reference `(Promoted: learn-<topic>)` to it, e.g.:

```
> LEARNED: dev server takes ~8s to become ready (Promoted: learn-dev-server-warmup)
```

`worklog scan` keys on this EXPLICIT reference: it resolves the `learn-<topic>` id
EXACTLY against the live index/files (no fuzzy substring guessing), so a real
promotion is never missed and an un-promoted marker is never spuriously passed.
A bare `Captured learning: learnings/<topic>.md` one-liner elsewhere is fine as a
human note, but the reference on the marker line is what clears the gate.

NEVER hand-create the learning file, NEVER hand-edit `index.json` — `learn` is
the only writer. Do NOT ask the user; learnings belong on the record without ceremony.

### Artifact capture standard

When a learning REFERENCES a concrete artifact — a config file, YAML snippet,
exact command line, error string or stack trace, file path, code snippet, URL /
endpoint, or identifier — **embed it VERBATIM in a fenced code block** rather
than paraphrasing it in prose.

**Why:** Detail lost at capture time is unrecoverable. A learning that says
"configured the metrics exporter with signed auth" is useless when the agent
needs the *actual* config six months later. The retriever can surface a
learning only if the content is there to surface.

Keep the artifact VENDOR-NEUTRAL where the fact allows it: capture the shape
that reproduces the behaviour, not the hostname of one deployment. A concrete
endpoint someone must copy verbatim belongs in the external knowledge dir, never
in a skill shipped with this package (`R-LOC-03`).

**Good (artifact embedded):**

```markdown
> LEARNED: the metrics exporter needs a signed-auth extension wired by NAME,
> not just enabled (Promoted: learn-metrics-exporter-signed-auth)

Body passed to `learn --content -`:

The exporter only authenticates when the extension is named on the exporter's
`auth.authenticator` key — declaring the extension alone silently sends
unsigned requests:

​```yaml
extensions:
  signedauth:
    region: <region>
    service: metrics

exporters:
  remotewrite:
    endpoint: https://<metrics-host>/api/v1/write
    auth:
      authenticator: signedauth      # <- the line that does the work
​```

Reload command (the config is read once, at start):
​```bash
sudo systemctl restart metrics-collector && journalctl -u metrics-collector -f
​```
```

**Thin (artifact paraphrased — avoid this):**

```markdown
> LEARNED: configured the metrics exporter with signed auth

Body: "Set up the collector to use signed authentication when exporting to the
metrics endpoint. Had to restart the service after the config change."
```

The thin version captures the *fact* but loses the *artifact*. When another agent
retrieves it, they still have to guess the YAML structure and the exact command.

**Precision caveat — do NOT pad:**

- Genuinely one-fact learnings (e.g. "webpack lazy-chunk naming uses
  `[request]` placeholder" — 136 tokens, complete as-is) MUST stay terse.
- `decisions/` records (median ~200 tokens, terse by design) MUST stay terse.
- Richness means capturing real artifacts WHEN THEY EXIST, not adding filler
  prose to short entries. If no concrete artifact is referenced, a one-liner is
  perfectly fine.

#### 3b. Skill (reusable procedure) — AUTHORED into canonical KB `skills/`, invocable only on approval

A skill is **authored** (never a hand-written `index add` stub, never a download):
it lands in the canonical KB `skills/<name>/` as the source of truth, and becomes
**invocable** on this machine only through the explicit approval gate. Two layers:

- **Canonical source** — KB `skills/<name>/SKILL.md`, written+registered by
  `scripts/agentware skill add` (which VETS the skill before it is trusted). This
  is the shared source of truth; `index add --category skills` is NOT the promotion
  path — do not hand-register a skill stub.
- **Per-machine execution cache** — `scripts/agentware skill sync --approve <name>`
  re-vets and installs the skill into `~/.claude/skills` (the ONLY install path;
  `sync` NEVER auto-installs — approval is the sole gate). Once installed, retrieval
  pins it via `SKILL_PIN_FLOOR=0.85` so a matching skill floats to the top of recall.

**The automated pipeline (shipped — normally you do NOT hand-author).** Recurring,
cohesive learnings are auto-detected and surfaced as approval-gated proposals — you
rarely promote a skill by hand:

1. **Detection** runs BOTH on the offline dream cadence (dream step `k`,
   `skill-propose`) AND on demand via `scripts/agentware skill propose` (add
   `--dry-run` to rank candidate clusters without emitting; `--format json` for the
   structured report). It clusters `learnings` by a density-gated tag-spine (never a
   naive similarity/duplicate trigger), dedups against existing KB + `.claude/skills/`
   names (surfacing an "extend `<X>`" note instead of a sibling), and is bounded by a
   per-run cap + the `WORKORDER_DRAFT_CAP` (g3) gate.
2. **Proposal** — each qualifying cluster becomes a **tier-2 PROPOSED workorder**
   (`work/auto/skill-candidate-<fp>/plan.md`, `Status: draft`, NEVER auto-started).
   The operator approves+running it IS the gate — the toolkit only detects+proposes;
   it NEVER authors a stub, NEVER runs `index add --category skills`, NEVER installs.
3. **Authoring** — the approved+run workorder reads each source learning via
   `scripts/agentware query --id <id>`, authors the SKILL.md via the **skill-creator**
   skill, then `scripts/agentware skill add` vets+installs it into canonical KB
   `skills/`, declaring **reversible** relates-edges back to the source learnings
   (no learning is ever auto-deleted).
4. **Invocability** — `scripts/agentware skill sync --approve <name>` makes it
   invocable on this machine (per the two-layer gate above).

**Hand-authoring (only when you are doing it right now, not via a proposal).**
Confirm it clears the bar first: ≥2 steps AND reusable across tasks (two-strike
rule); a one-off stays a learning (3a). Then use the **skill-creator** skill (or
`templates/skill-template.md`) to author `SKILL.md`, run `scripts/agentware skill
add --source <path> --allow-external` to vet+install it into canonical KB `skills/`,
validate with `scripts/agentware index validate` (exit 0) + `scripts/agentware
features`, and `scripts/agentware skill sync --approve <name>` to make it invocable.
Future agents find it via `scripts/agentware query --category skills` (or `--tag`)
and the sync-installed cache. Append to `worklog.md`: `Promoted to skill: <name>`.

Making a skill a permanent part of the orchestrator PACKAGE itself (package
`.claude/skills/`) is a distinct self-extension / package change — see 3c.

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
