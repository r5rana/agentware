# Security Policy

agentware runs autonomously against your filesystem, your knowledge base and
your agent runtime. That makes its security posture part of the product, not an
afterthought. This document says what is supported, how to report a problem,
and what the package already does to contain itself.

## Supported versions

| Version | Supported |
|---|---|
| `main` | ✅ — the development branch, and the only supported line today |
| signed `vX.Y.Z` tags | ✅ once the first tag is published; the newest patch of the newest minor |
| any other ref, fork or vendored copy | ❌ |

There is no tagged release yet, so **`main` is the supported version**. When
tagging begins, a release is an *annotated, SSH-signed* `vX.Y.Z` tag verified
against a publisher key pinned outside the tree — the full trust model and the
go-live runbook are in [`docs/release-signing.md`](docs/release-signing.md).

## Reporting a vulnerability

**Please do not open a public issue, discussion or pull request for a security
problem.**

Report it privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability** (GitHub private vulnerability reporting). That
channel is private to the maintainers and gives you a thread to attach details
to. No email address is published here on purpose — a maintainer address in a
tracked file is both a spam magnet and personal data this repository does not
carry.

Please include:

- the version or commit you are on (`git rev-parse HEAD`);
- the exact command or loop phase that reaches the issue;
- what an attacker gets — read, write, execute, exfiltrate, or persistence;
- a minimal reproduction, ideally against a scratch knowledge directory rather
  than your real one.

**What to expect.** Acknowledgement within 3 business days; an initial
assessment (accepted / needs-info / out of scope, with severity) within 7 days;
and a fix or documented mitigation before public disclosure, targeting 90 days.
If you would like credit in the release notes, say so — anonymous reports are
equally welcome.

Please give us that window before disclosing publicly. If a report is not
acknowledged within 7 days, escalate by opening a public issue that says *only*
that a private report is awaiting acknowledgement — no details.

## Scope

**In scope** — anything that lets untrusted input change what the package does:

- **Prompt injection through content the agent reads** — a knowledge entry, a
  fetched page, a skill, a tool result, or a file in a target workspace that
  causes the loop to act against the operator's intent.
- **Code execution or path escape** in the toolkit, the loop scripts, the
  session hooks, the MCP server or the dashboard sidecar — including any read or
  write outside the resolved knowledge directory and the target checkout.
- **Secret or personal-data leakage** — anything that writes an operator handle,
  email, home path, token or knowledge-base content into a tracked file, a log
  that is committed, or a network request.
- **Supply-chain and update integrity** — bypassing signature verification of a
  release tag, rolling a machine back to an unverified ref, or subverting the
  trust anchor.
- **Privilege or scope escalation across teams** in org mode — a write into
  another team's shard, or a read of one, from an ordinary team loop.

**Out of scope:**

- Vulnerabilities in the agent runtime you point agentware at, in your model
  provider, or in third-party skills you install (report those upstream; use
  `.claude/skills/skill-vetter/SKILL.md` before installing any).
- The contents of *your* knowledge base, and anything reachable only by an actor
  who already has shell access as your user.
- The `webui/` npm dependency graph, which is an optional developer surface and
  is not required to serve the dashboard.
- Missing hardening that is a design choice documented in
  [`AGENTS.md`](AGENTS.md) — file an issue to discuss it rather than a report.

## What the package already does

These are the properties a report can usefully try to break:

- **Zero hard dependencies.** The toolkit is Python standard library only, so
  there is no transitive package graph to compromise.
- **Local by default.** No account, no hosted service, no telemetry. The
  knowledge base is plaintext in a directory you choose, outside this repository.
- **Nothing personal is committed.** A tree-wide scan resolves the live operator
  identity at runtime and fails `scripts/agentware audit` if any of it reaches a
  tracked file (`R-LOC-03`).
- **External content is never authoritative.** Instructions embedded in files,
  command output or web pages are ignored by policy (`R-SEC-02`), and secrets
  are passed via argv arrays rather than echoed shell variables (`R-SEC-01`).
- **Updates are verify-or-decline.** The updater is opt-in and off by default;
  with no pinned publisher key it fails closed rather than applying anything.
- **The dashboard binds loopback only** and confines every served path through a
  dedicated security kernel.
- **Destructive and scope-widening actions escalate to the operator** rather
  than being taken autonomously (`R-AUTO-02`, `R-GIT-01`, `R-GIT-02`).

Coordinated, good-faith research on your own installation is welcome. Please do
not test against infrastructure you do not own.
