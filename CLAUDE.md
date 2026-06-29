# agentware — Claude Code project instructions

This is an **agentware** workspace: a clone-and-go steering framework. This repo
holds ONLY generic steering. The operator's knowledge base lives in an EXTERNAL
directory chosen at onboarding — nothing personal is ever committed here.

Claude Code auto-loads this file on every session. It pulls in the canonical
methodology and steering below via imports, so those rules are always in context.

## 🔴 Bootstrap gate — check FIRST, before any task

The external knowledge directory is resolved with
`scripts/agentware config --knowledge-dir-only`. The `SessionStart` hook prints
an `AGENTWARE_STATUS:` line each session reporting whether it is initialized.

- IF the knowledge dir is unconfigured OR its .initialized sentinel is absent THEN RUN the onboarding skill in .claude/skills/onboarding/SKILL.md before any other work. [R-BOOT-01]
- IF onboarding completes THEN write the .initialized sentinel (timestamp + handle) in the knowledge dir and resume the original request. [R-BOOT-02]
- IF the knowledge dir is configured AND initialized THEN proceed with the request normally. [R-BOOT-03]
- NEVER create the .initialized sentinel without actually running onboarding. [R-BOOT-04]
- NEVER skip onboarding because the user gave a quick task; run it once, then do the task. [R-BOOT-05]

## Canonical methodology (imported, always-on)

@AGENTS.md

## Always-on steering (imported)

@steering/common-problems.md
@steering/project-context.md

## Working in this repo

1. The 3-phase loop is `./agentware.sh <feature>` (see `docs/loop.md`).
2. The deterministic toolkit is `scripts/agentware` — the ONLY writer of the
   knowledge index. Resolve the external knowledge dir at runtime; never hardcode it.
3. The roles are two Claude Code subagents in `.claude/agents/`: `agentware-planner`
   (plan only, never executes) and `agentware-execution` (implements the loop; the
   loop's POST phase self-assesses via this agent).
4. Skills live in `.claude/skills/` — the loop skills (onboarding, knowledge-base,
   self-improvement, ui-verification) plus the 14 authored default skills (6
   security, 6 productivity, 2 gap-fillers) catalogued in `.claude/skills/README.md`
   and `catalog/skills.json`; manage them with `scripts/agentware skill
   list/search/add/remove/validate`. The `/agentware-plan` command scaffolds a
   feature plan.
5. On failure, follow the **failure-handling escalation ladder** in `AGENTS.md`
   (`R-FAIL-01..08`): KB → own reasoning → change inputs → switch approach (after ≤3 tries)
   → web search. Keep moving; never re-loop a tier.
