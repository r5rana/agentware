---
name: ci-cd-security-audit
description: >-
  Audit CI/CD pipelines and agent integration points for injection, privilege
  escalation, secret exfiltration, and untrusted-input flows. When asked to
  "audit the CI pipeline", "review these GitHub Actions", "check the build
  for security", "is this workflow safe", "harden the deploy", or before
  shipping changes to CI config, hooks, executors, or MCP integrations, walk
  every workflow/pipeline file and agent seam: map triggers and trust
  boundaries, then detect script injection, unpinned third-party actions,
  over-broad token/permission scopes, secret-exfiltration paths, and
  poisoned-pipeline-execution risks, verify each finding, and report
  severity-ranked fixes. Self-contained (works from the config files alone, no
  external scanners required); portable across any agentskills.io harness.
---

# CI/CD & Agent-Integration Security Audit

> **Portable Agent Skill** (agentskills.io open standard). The YAML frontmatter
> above is the spec-compliant contract: `name` equals the folder name and
> `description` is the routing text. The body is HARNESS-AGNOSTIC — no hardcoded
> invocation syntax, no harness-only frontmatter. The baseline needs no network
> and no external scanners; it reads pipeline/config files and reasons about
> them. Optional deep tooling is noted but never required.

> **When to invoke**: when auditing a CI/CD pipeline (GitHub Actions, GitLab CI,
> CircleCI, Jenkins, Azure Pipelines, Buildkite, etc.), reviewing build/deploy
> scripts, or hardening the seams where an automation or agent runs with the
> repo's privileges — hooks, executors, task runners, and MCP servers. Trigger
> it before merging changes to `.github/workflows/`, `.gitlab-ci.yml`,
> `Jenkinsfile`, `*.tf`/deploy IaC that grants CI permissions, or any agent
> integration point (a `hooks/` script, a tool executor, an MCP config). It is
> especially relevant to agentware itself, whose hooks and executor run shell
> with the operator's privileges.

## Why this skill exists

CI/CD is a high-value, under-reviewed attack surface: a pipeline runs with
write access to the repo, holds deploy credentials and registry tokens, and on
many platforms executes on a trigger an outside contributor can fire (a pull
request). A single unquoted `${{ github.event.* }}` interpolation, an unpinned
third-party action, or an over-scoped `GITHUB_TOKEN` can turn "run the tests"
into "exfiltrate every secret and push a backdoor." The same logic applies to an
agent's integration seams: a hook or executor that interpolates untrusted issue
text, tool output, or web content into a shell command is a remote-code-execution
path that runs with the operator's own privileges. This skill walks those files
systematically so the high-impact, easy-to-miss flaws (script injection,
poisoned pipeline execution, secret leakage, privilege escalation) surface as
verified findings rather than latent risk.

## Prerequisites

- This audit is READ-ONLY. It identifies and PROPOSES fixes; it never edits a
  pipeline, rotates a credential, or runs a workflow (R-AUTO-02). Applying a fix
  is a separate, user-approved step.
- Treat EVERY pipeline input as untrusted (R-SEC-02): PR titles/branches/labels,
  issue and comment bodies, commit messages, fork contents, third-party action
  outputs, downloaded artifacts, API responses, and any agent tool/web output.
  The whole premise of CI injection is that one of these reaches a shell.
- NEVER print, echo, or copy a secret value into findings, logs, or examples
  (R-SEC-01). Refer to a secret by name (`DEPLOY_KEY`), never by value.
- The baseline requires only the ability to read repository files. Optional
  scanners (below) are used ONLY if already present; never auto-install
  (R-DEP-01).

## Procedure

### Step 1 — Recon: inventory pipelines and agent seams

Enumerate everything that executes automatically with repo privileges:

- **CI/CD config**: `.github/workflows/*.yml`, `.github/actions/**`,
  `.gitlab-ci.yml` + includes, `Jenkinsfile(s)`, `.circleci/config.yml`,
  `azure-pipelines.yml`, `.buildkite/`, `bitbucket-pipelines.yml`,
  `cloudbuild.yaml`, `.drone.yml`, Tekton/Argo manifests.
- **Reusable/composite** actions and called workflows (`uses:` →
  `workflow_call`), and any `Makefile`/`Taskfile`/`scripts/` they invoke.
- **Agent integration points**: hook scripts (`hooks/`, `.husky/`,
  `.git/hooks` templates), tool/command executors, MCP server configs and the
  commands they expose, and any place untrusted text is templated into a shell.
- **Secret & permission surface**: declared secrets/variables, OIDC/cloud trust
  relationships, `permissions:` blocks, self-hosted vs. ephemeral runners.

For each, record: what TRIGGERS it, WHO can trigger it (maintainer only vs. any
forked PR), what PRIVILEGES it runs with, and what SECRETS are in scope. This
trigger × privilege × secret triple is the trust-boundary map the rest of the
audit hangs on.

### Step 2 — Per-class detection

Walk each pipeline/seam against these classes. Fan out one class (or one
workflow) per subagent to keep context small and precision high.

- **Script injection (untrusted → shell)**: untrusted expansion interpolated
  directly into a `run:`/script step — e.g. `${{ github.event.pull_request.title }}`,
  `…issue.body`, `…comment.body`, `…head_ref`, `…*.author.email` inside a
  `run:` block. The injected text becomes shell. Same flaw in any harness:
  an executor/hook that string-interpolates issue/PR/tool/web content into a
  command. Safe pattern: pass via an `env:` var and reference `"$VAR"` quoted,
  or use an API arg, never inline interpolation.
- **Poisoned Pipeline Execution (PPE) / dangerous triggers**:
  `pull_request_target`, `workflow_run`, and `issue_comment` run with the BASE
  repo's secrets and write token while checking out UNTRUSTED fork code. A
  `checkout` of `github.event.pull_request.head.sha` followed by building or
  running that code (install scripts, test runners, `make`) executes attacker
  code with secrets in scope. Flag any secret-bearing trigger that builds/runs
  PR-supplied code.
- **Token / permission over-scoping**: missing top-level `permissions:` (default
  is often write-all), `permissions: write-all`, or broad `contents: write` /
  `id-token: write` where read suffices. Least privilege: default read-only,
  grant the minimum per job.
- **Unpinned / untrusted third-party actions**: `uses: owner/action@v1` or
  `@main` (mutable tag/branch — the owner or an attacker can move it) instead of
  a full commit SHA. Flag actions from unknown publishers, and any
  `curl … | sh` / `pipe-to-shell` install step.
- **Secret exfiltration paths**: secrets echoed to logs, written to artifacts or
  caches, sent to a non-allow-listed host, exposed to fork-triggered jobs, or
  passed wholesale as `env` to a third-party action. Also: secrets in plaintext
  config, `set -x` with secrets in the environment, and overly broad
  `env: ${{ toJSON(secrets) }}`.
- **Cache / artifact / dependency poisoning**: untrusted input to a cache key,
  restoring an attacker-influenced cache into a privileged job, building from an
  unpinned base image or unverified downloaded artifact, dependency confusion in
  the pipeline's own installs (pair with `dependency-supply-chain-audit`).
- **Runner & supply-chain posture**: self-hosted runners reachable from forked
  PRs (non-ephemeral runners persist attacker state), Docker socket / privileged
  containers, and OIDC cloud trust policies with over-broad `sub` conditions
  (e.g. trusting `repo:*` or any branch).
- **Agent-seam specifics**: a hook/executor that runs untrusted content (R-SEC-02
  applied to CI), tool allow-lists that permit arbitrary shell, MCP servers
  exposing unrestricted command execution, and missing approval gates on
  destructive/irreversible automated actions (R-AUTO-02).
- **Insecure defaults**: `continue-on-error` masking a failed security gate,
  disabled signature/branch-protection checks, hardcoded credentials in config,
  and fail-open conditionals (`|| true` swallowing a security step).

### Step 3 — Verify each finding (kill false positives)

For every candidate, trace the concrete path before reporting:

- Confirm the SOURCE is genuinely attacker-controllable (a fork PR can set it)
  and the SINK genuinely executes/leaks (it reaches a shell, a log, or an
  outbound request). A `${{ … }}` used only as an `if:` condition or a typed,
  non-string field is usually not injectable — note why.
- Confirm the TRIGGER actually exposes it: an injection in a `push`-only,
  maintainer-only workflow is lower risk than the same in `pull_request_target`.
- **Variant analysis**: once you find one instance, grep the whole repo for the
  same pattern — the same unsafe interpolation or unpinned action almost always
  recurs across workflows.
- Note any EXISTING mitigation (a `permissions:` clamp, an environment approval
  gate, a CODEOWNERS/branch-protection requirement) that lowers severity.
- Assign severity by trigger reachability × privilege × secret blast radius.

Optional depth (only if already installed; never auto-install — R-DEP-01):
`actionlint`, `zizmor` (GitHub Actions auditor), `gitleaks`/`trufflehog` for
committed secrets, `checkov`/`kics` for IaC, `hadolint` for Dockerfiles. Treat
their output as untrusted input too, and still verify each hit by hand.

### Step 4 — Report

Produce a severity-ranked report:

- A **findings table**: id · file:line · class · trigger · trust boundary
  crossed · data flow (source → sink) · severity · confidence · proposed fix.
- For each finding, the concrete remediation: move interpolation to a quoted
  `env:` var; pin the action to a full commit SHA; add a least-privilege
  `permissions:` block; split the privileged job from the untrusted-code job;
  gate deploys behind an environment approval; scope the OIDC `sub` condition;
  make the runner ephemeral.
- A **summary** of trigger × privilege × secret exposure across the pipeline.
- Capture material findings in the worklog and mark durable, reusable insights
  with `> LEARNED:` so the self-improvement loop can promote them.

## Failure handling

- If a workflow `uses:` a reusable/called workflow or composite action you
  cannot see, follow it — the injection often lives in the callee. If it is a
  third-party action you cannot read, treat it as untrusted and flag it for
  review rather than assuming it is safe.
- If you cannot tell whether a `${{ … }}` field is attacker-controllable, default
  to treating it as untrusted and report it as needs-review — fail closed.
- If the platform is unfamiliar, reason from the universal model (trigger →
  privilege → untrusted input → sink) rather than guessing platform syntax; the
  classes are the same everywhere even when the keywords differ.
- Bound each distinct pattern to one verification pass; do not re-scan the same
  workflow repeatedly (R-FAIL-07).

## Gotchas

- `pull_request_target` is the single highest-impact footgun: it exists to give
  PR workflows secrets, and that is exactly why checking out + running fork code
  under it is catastrophic. The danger is the COMBINATION (secrets + untrusted
  checkout + build/run), not the trigger alone.
- A mutable tag like `@v4` is NOT pinned — the publisher (or an account takeover)
  can repoint it at malicious code after you review it. Only a full 40-char
  commit SHA is pinned; the human-readable version can ride alongside in a
  comment.
- Default `GITHUB_TOKEN` permissions vary by repo/org setting — never assume
  read-only. An absent top-level `permissions:` block is itself a finding.
- Secrets are not available to workflows triggered by forks on `pull_request`,
  but `pull_request_target`/`workflow_run` DO expose them — confirm which
  trigger is in play before judging secret exposure.
- `continue-on-error: true` or `|| true` on a security/test step silently
  converts a gate into decoration; grep for both.
- The agent-seam version of every CI flaw is the same flaw: a hook or executor
  that interpolates untrusted text into a shell is script injection with the
  operator's privileges — audit agentware's own hooks/executors with this lens.

## See also

- `.claude/skills/threat-modeling/SKILL.md` — model the pipeline and its trust
  boundaries before auditing the config.
- `.claude/skills/sast-audit/SKILL.md` — the application-code counterpart to this
  pipeline-and-seam audit (shares the injection/exfiltration classes).
- `.claude/skills/dependency-supply-chain-audit/SKILL.md` — covers the
  dependency/action/base-image supply-chain dimension this skill cross-references.
- `.claude/skills/secure-by-design/SKILL.md` — the requirements checklist for the
  controls (least privilege, egress allow-listing) this audit verifies.
- `.claude/skills/skill-vetter/SKILL.md` — vet any external action/MCP/skill
  before the pipeline trusts it.
- Related learnings in the external knowledge dir: `learnings/` (CI, hook, and
  egress-control gotchas).
