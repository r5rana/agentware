# AGENTS.md — Canonical Methodology (Deterministic Steering Format)

> Single source of truth for agent behaviour. Loaded as always-on context because
> the auto-loaded `CLAUDE.md` imports it with `@AGENTS.md`. The agentware subagents
> reference it too. Rationale and examples live in `docs/methodology.md` (NOT
> agent-loaded). Every rule is atomic, carries a stable ID, and is enforced by
> `scripts/agentware steering lint`.
>
> agentware ships ZERO personal data. The knowledge base lives in an EXTERNAL
> directory the operator chose at onboarding. Resolve it with
> `scripts/agentware config --knowledge-dir-only`; NEVER hardcode its path.

## Execution loop

Every task runs this loop. Do not skip steps; do not stop until all subtasks are done.

1. PLAN — break the task into verifiable subtasks; save the plan.
2. EXECUTE — run commands / write code for one subtask.
3. VERIFY — confirm it works with the project's own build/test/health command.
4. NEXT — move to the next subtask.
5. REPEAT — until every subtask is complete.
6. COMPLETE — update the knowledge base, then present results.

## Build and complete

- MUST treat building and completing tasks as the primary job. [R-EXEC-01]
- NEVER mark a task complete while any subtask is in progress or unverified. [R-EXEC-02]
- ALWAYS ask "is the original goal fully achieved?" before presenting results. [R-EXEC-03]
- IF something is bootstrapping THEN wait and verify; NEVER defer it to the user. [R-EXEC-04]
- IF a step failed THEN fix it and retry; NEVER present failure as completion. [R-EXEC-05]
- IF blocked by a dependency THEN document the blocker and ask the user. [R-EXEC-06]
- ALWAYS track ongoing work in the worklog and plan status markers. [R-EXEC-07]
- NEVER pivot to a different approach mid-task without user approval. [R-EXEC-08]
- ALWAYS update the knowledge base with what was built, what works, and gotchas after completion. [R-EXEC-09]

## Planning

- MUST write a plan with verifiable subtasks before non-trivial work. [R-PLAN-01]
- MUST save the plan to `<knowledge-dir>/work/<feature>/plan.md`. [R-PLAN-02]
- ALWAYS update plan status markers as subtasks change. [R-PLAN-03]
- NEVER treat a saved plan as a substitute for execution. [R-PLAN-04]

## Knowledge base location

- RUN scripts/agentware config --knowledge-dir-only to resolve the external knowledge dir; NEVER hardcode its path. [R-LOC-01]
- IF the workspace is not initialized THEN RUN the onboarding flow in .claude/skills/onboarding/SKILL.md before any task work. [R-LOC-02]
- NEVER commit personal data, secrets, or operator-specific knowledge into this repo; that all lives in the external knowledge dir. [R-LOC-03]

## Package immutability & self-extension

- NEVER modify orchestrator package files (AGENTS.md, CLAUDE.md, .claude/**, steering/**, agentware.sh, scripts/**) during normal task work. [R-PKG-01]
- ALWAYS write new knowledge, learnings, skills, and work artifacts to the external knowledge dir, never into the package. [R-PKG-02]
- IF the user EXPLICITLY asks to change agentware's own steering, skills, agents, or loop THEN STOP and present a "!! WARNING !!" that self-extension can destabilize the system for this and every future project, and edit only on explicit confirmation. [R-PKG-03]
- IF a package edit is confirmed THEN RUN scripts/agentware steering lint afterward and STOP if it fails. [R-PKG-04]
- IF a package edit is confirmed THEN RUN scripts/agentware eval --record --gate afterward and STOP if the reliability score regresses. [R-PKG-05]

## Drill-down architecture

- NEVER guess where a skill or knowledge entry lives. [R-DRILL-01]
- RUN scripts/agentware query --id <ID> | --tag <TAG> | --category <CAT> to locate a knowledge entry. [R-DRILL-02]
- READ the exact path the query returns and follow its instructions. [R-DRILL-03]
- NEVER bloat README.md or the knowledge MAIN.md with deep-dives; encapsulate logic in a SKILL.md. [R-DRILL-04]

## Toolkit mandate (structured data)

- NEVER hand-edit the knowledge index.json. RUN scripts/agentware index add|remove for every index mutation. [R-KB-01]
- NEVER hand-create learning files. RUN scripts/agentware learn --topic <T> --summary <S> --tags <A,B> --content <...>. [R-KB-02]
- ALWAYS RUN scripts/agentware index validate after any knowledge-base change. [R-KB-03]
- RUN scripts/agentware query --id|--path|--tag|--category to look up entries. NEVER grep the index.json. [R-KB-04]
- MUST register every new knowledge entry with scripts/agentware index add. [R-KB-05]
- RUN scripts/agentware features to regenerate FEATURES.md after index changes. [R-KB-06]
- ALWAYS update EXISTING knowledge entries after execution, never before building. [R-KB-07]
- IF the user says "document" or "save context" THEN write a full context dump (plan, work done, decisions, gotchas, next steps). [R-KB-08]

## Context discovery

- READ the knowledge MAIN.md and the relevant projects/<project>/index.md when resuming an existing project. [R-CTX-01]
- IF the instruction is a clear standalone task THEN skip context discovery and execute. [R-CTX-02]
- NEVER read every knowledge-base file before starting work. [R-CTX-03]
- NEVER quote MAIN.md to prove context was read. [R-CTX-04]
- RUN scripts/agentware recall "<task summary>" at task start to surface relevant entries by ranked relevance, then READ the returned paths instead of injecting the whole MAIN.md. [R-CTX-05]

## Verification gates

- MUST verify each subtask with the project's own build/test/health command before the next. [R-VERIFY-01]
- IF the change is UI THEN verify it in a browser yourself (see .claude/skills/ui-verification/SKILL.md) before marking complete. [R-VERIFY-02]
- IF the change is a backend endpoint THEN call it yourself and verify status, headers, and body. [R-VERIFY-03]
- IF the change is a mutation THEN do a read-after-write and capture the request/response in the worklog. [R-VERIFY-04]
- MUST ensure the user can independently access and verify the result. [R-VERIFY-05]

## Failure handling (escalation ladder)

> On a failed step, walk this ladder IN ORDER and ADVANCE the moment a tier is exhausted. Never loop a tier; always maintain forward progress toward the goal.

- IF a step fails THEN walk the ladder — (1) knowledge base → (2) own reasoning → (3) change inputs → (4) switch approach → (5) web search — advancing as each tier is exhausted. [R-FAIL-01]
- IF a step fails THEN FIRST query the knowledge base for the error signature and READ it to judge whether the match applies to the LIVE state; NEVER apply a stored fix blindly. [R-FAIL-02]
- IF the KB has no applicable match THEN fall back to your own reasoning and knowledge rather than re-reading. [R-FAIL-03]
- NEVER repeat an identical failing action; every retry MUST change an input, assumption, or approach. [R-FAIL-04]
- IF a few (≤3) attempts on one approach still fail THEN switch approach completely, do not keep tweaking. [R-FAIL-05]
- IF the KB and your own knowledge are exhausted, OR the framework/error is unfamiliar, THEN search the web for current solutions before continuing (see R-WEB-01). [R-FAIL-06]
- MUST maintain forward progress: bound lookups to one pass per distinct error; NEVER get stuck re-reading or re-querying. [R-FAIL-07]
- ALWAYS treat a point-in-time value in a learning (id, path, IP, version) as suspect; verify it against live state, and correct the learning if it contradicts. [R-FAIL-08]

## Anti-patterns

- NEVER create knowledge entries for things not yet built. [R-AP-01]
- NEVER write comparison documents when the user said "proceed". [R-AP-02]
- NEVER update MAIN.md or index.json as a first step before building. [R-AP-03]
- NEVER document failed attempts; fix and retry instead. [R-AP-04]
- NEVER spend more time documenting than executing. [R-AP-05]
- NEVER add orchestration layers when a simple script suffices. [R-AP-06]
- NEVER build infrastructure before the basic path works end-to-end. [R-AP-07]
- NEVER propose multiple options once the user has chosen one. [R-AP-08]
- NEVER run broad build/test commands when a specific test name will do. [R-AP-09]

## Git and dependencies

- NEVER run git commands unless the user explicitly asks; the user owns commits (exception: the one-time onboarding first commit). [R-GIT-01]
- NEVER run destructive git operations (reset --hard, push --force, clean -f, branch -D) without explicit confirmation. [R-GIT-02]
- ASK the user before installing or removing any dependency. [R-DEP-01]
- MUST pin dependency versions; NEVER use open ranges or "latest". [R-DEP-02]

## Web search

- IF a framework is unfamiliar or an error is complex THEN RUN a web search for current best practices before writing code. [R-WEB-01]

## Self-improvement

- ALWAYS mark discoveries in the worklog as `> LEARNED: <one-liner>`. [R-SI-01]
- ALWAYS follow the self-improvement skill (.claude/skills/self-improvement/SKILL.md) at end of task to promote learnings into durable knowledge. [R-SI-02]
- NEVER emit a task-completion promise while any `> LEARNED:` marker in the worklog is unpromoted; promote each via scripts/agentware learn first, then confirm scripts/agentware worklog scan passes. [R-SI-03]

## Conventions

- MUST name resources `{project}-{resource}-v{version}`, or adopt the project's own scheme. [R-CONV-01]
- MUST use relative paths inside repo files; resolve the external knowledge dir at runtime. [R-CONV-02]
- MUST use the write tool for all file creation, never cat/heredoc or multiline echo. [R-CONV-03]
- READ a file's references across the repo before you delete, rename, or move it. [R-CONV-04]
