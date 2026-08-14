#!/usr/bin/env bash
# shellcheck shell=bash
# scripts/loop/prompts-post.sh — the POST and MERGE prompts, plus the
# --pre/--main/--post-prompt append block.
#
# Sourced at SOURCE time by agentware.sh immediately after prompts.sh. Split from
# it only by the 250-line ceiling; the append block lives HERE because it must run
# after all five prompt constants exist. Ends with `:` — see the note in args.sh.
#
# ---- POST_PROMPT IS THE UNION OF BOTH TREES' NUMBERED LISTS (plan task 33) ----
# The two trees' post-phase instructions diverged in two places and the merged
# prompt takes the SUPERSET of each:
#
#   items 1-7   identical on both sides — carried verbatim.
#   item 8      this tree's is a strict superset. BOTH sides classify extracted
#               knowledge against the self-improvement decision tree; only this
#               tree adds the anti-leak clause — extracted knowledge must not live
#               ONLY in assessment.md, because `worklog scan` never reads that file,
#               so an assessment-only learning is a silent loss the zero-knowledge-
#               loss gate cannot see. This tree's wording wins outright.
#   item 9      DONOR-ONLY — the frequency-gated skill-proposal lifecycle. Without
#               it a post phase hand-writes skills straight into the always-on
#               roster, which is the always-on context cost this ledger exists to
#               bound. Carried in full.
#   item 10     this tree's item 9 == the donor's item 10 (emit the promise).
#
# The donor wrote item 9 against ITS OWN `skill propose --slug/--ref` +
# `skill proposals --ripe` lifecycle. Per the plan's decided conflict table this
# tree KEEPS the name `skill propose` for its own (argument-disjoint) lifecycle and
# the donor's ledger RE-HOMES to `skill suggest` / `skill suggestions`. So item 9
# names the DECIDED verbs, not the donor's, and guards on the verb actually
# resolving — the re-homing lands in a later port wave, and until then the agent
# must record the candidate rather than run a verb that would exit non-zero. Same
# "no-op when the capability is absent" discipline the org-lint and
# retrieval-quality gates carry (scripts/loop/orglint.sh, retrieval.sh).

POST_PROMPT="You are assessing the completed implementation of $FEATURE_NAME.

## Instructions
1. Read $DOCS_DIR/plan.md to understand what was planned
2. Read $DOCS_DIR/worklog.md to understand what was done
3. For infra/config tasks: verify the resources/configs still exist and are healthy
4. For knowledge-base tasks: verify entries are correct and index.json is valid JSON
   (run \`scripts/agentware index validate\`)
5. Evaluate against:
   - Completeness — all planned tasks done, no partial work
   - Verification — all acceptance criteria actually verified
   - Documentation — knowledge base updated with what was built
   - Conventions — naming, relative paths, no leakage of personal data
6. Autonomous decisions (R-AUTO-04): scan $DOCS_DIR/worklog.md for every
   '> DECISION:' marker. Under an '## Autonomous Decisions' heading in the
   assessment, list each one and judge whether it respected R-AUTO-02 (i.e. it did
   NOT autonomously expand scope, change acceptance criteria, act destructively or
   irreversibly, weaken security, change dependencies, pivot the whole approach, or
   override a STOP gate). Flag any decision that overstepped as a FAIL.
7. Write a PASS/FAIL assessment to $DOCS_DIR/assessment.md
8. After writing the assessment, identify any new learnings or gotchas. For each,
   classify against the self-improvement decision tree
   (.claude/skills/self-improvement/SKILL.md) and note them under
   '## Extracted Knowledge' with: suggested ID, classification (learning /
   skill candidate / steering candidate), one-paragraph summary, suggested
   wiring location, and tags. Extracted knowledge must NOT live only in
   assessment.md (that is a silent knowledge leak the worklog-scan gate never
   reads): for EACH extracted item not already promoted, ALSO append a
   '> LEARNED: <one-liner>' marker to $DOCS_DIR/worklog.md and immediately
   promote it via scripts/agentware learn (per the self-improvement skill), then
   annotate that marker '(Promoted: <id>)' so it resolves. Before emitting
   POST_COMPLETE, run scripts/agentware worklog scan --path $DOCS_DIR/worklog.md
   and confirm 0 unpromoted — the kb-sync commit gate re-checks this and aborts
   the run if any marker is left unpromoted.
9. Skill candidates (the frequency-gated proposal lifecycle). For EACH item you
   classified as a 'skill candidate', do NOT hand-write a skill into the always-on
   roster — promotion into that roster is ALWAYS human-gated. Instead, per
   .claude/skills/self-improvement/SKILL.md §3b:
   (a) if it is ALREADY an always-on skill (\`scripts/agentware query --category
       skills\` / \`skill search\`), do nothing;
   (b) else pick a stable lowercase-kebab slug and record the candidate so its
       FREQUENCY signal accumulates on ONE slug across runs. If
       \`scripts/agentware skill suggest --help\` resolves, run
       \`scripts/agentware skill suggest --slug <slug> --ref $FEATURE_NAME
       --body -\` — it create-or-increments that slug's unique-ref count and
       REFUSES a near-duplicate with 'did you mean <slug>?' (reuse the suggested
       slug so the signal lands on one entry, never a near-duplicate pair). If the
       verb does NOT resolve in this build, do not invent one and do not fall back
       to \`skill propose\` (a DIFFERENT, argument-disjoint lifecycle in this
       tree): record the slug and its one-line rationale under a
       '## Skill candidates' heading in assessment.md instead.
   Append the promotion reference \`(Promoted: proposal-<slug>)\` — or
   \`(Promoted: skill-<slug>)\` once graduated — back onto the originating
   \`> LEARNED:\` line so \`worklog scan\` clears it.
   Then, if \`scripts/agentware skill suggestions --ripe\` resolves, run it and
   surface every ripe proposal (unique-ref count >=
   AGENTWARE_SKILL_PROPOSE_THRESHOLD, default 5) under a
   '## Skill proposals ripe for promotion' heading in assessment.md (slug, count,
   refs) so the operator can promote it. Below threshold: stay silent.
10. Output <promise>POST_COMPLETE</promise> when done"

# MERGE_PROMPT — Phase 5 of the KB git sync (feature 260625-kb-git-sync). Spawned
# (reusing the agentware-execution agent — NO new agent) ONLY for the rare case of
# a same-entry prose conflict: two agents/devs edited the SAME knowledge entry two
# different ways. The deterministic CLI already resolves all DERIVED-file conflicts
# by rebuilding (C-1); this agent reconciles ONLY the conflicted ENTRY markdown,
# preserving facts from BOTH sides, and NEVER touches derived files (they are
# rebuilt by `kb-git merge-continue` afterward). The <FILES> placeholder is
# substituted by kb_sync_push() with the newline-separated conflicted entry paths.
MERGE_PROMPT="A knowledge-base git rebase has PAUSED on a same-entry prose
conflict: the SAME knowledge entry was edited two different ways and git could not
merge it automatically. Your ONLY job is to reconcile the conflicted entry file(s)
below, preserving the facts from BOTH sides. A separate deterministic step rebuilds
all derived files and continues the rebase after you finish — so do NOT run any git
command and do NOT touch derived files.

## Conflicted entry file(s) (resolve EXACTLY these, nothing else)
<FILES>

## Steps
1. Open each conflicted file. Find the git conflict markers (\`<<<<<<<\`,
   \`=======\`, \`>>>>>>>\`) and resolve EVERY one of them.
2. Reconcile by UNION: keep every distinct fact/sentence/bullet from BOTH sides.
   When the two sides state the same thing, keep it once; never drop a fact that
   exists on only one side. Preserve the YAML frontmatter block at the top intact
   (do not duplicate or alter \`id\`, \`title\`, \`category\`, \`tags\`, etc.).
3. Remove ALL conflict markers so no \`<<<<<<<\`, \`=======\`, or \`>>>>>>>\` line
   remains. Leave the file as clean, readable markdown.

## Critical — do NOT cross these lines
- Edit ONLY the conflicted entry file(s) listed above.
- NEVER edit derived files (\`index.json\`, \`FEATURES.md\`, any \`<section>/index.md\`
   roster) — they are regenerated deterministically from frontmatter afterward.
- NEVER run git (no add/commit/rebase/push) — the loop continues the rebase for you.
- Do NOT delete or rename any entry; nothing-lost is enforced downstream.

When every listed file is conflict-marker-free with both sides' facts retained,
output <promise>MERGE_COMPLETE</promise> on a single line."

# Append extra prompts if provided.
[[ -n "$EXTRA_PRE_PROMPT" ]] && PRE_PROMPT="$PRE_PROMPT

## Additional Instructions
$EXTRA_PRE_PROMPT"

[[ -n "$EXTRA_MAIN_PROMPT" ]] && MAIN_PROMPT="$MAIN_PROMPT

## Additional Instructions
$EXTRA_MAIN_PROMPT"

[[ -n "$EXTRA_POST_PROMPT" ]] && POST_PROMPT="$POST_PROMPT

## Additional Instructions
$EXTRA_POST_PROMPT"

:
