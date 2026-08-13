# agentware — Roadmap

> **agentware** is a clone-and-go AI context + task-execution framework: a self-learning loop that
> builds **non-hallucinating, deterministic context** every iteration, keeps its memory trustworthy,
> and safely extends *itself*. This is the canonical, always-current roadmap. Live status also lives in
> the KB (`references/agentware-roadmap-p-series.md`, `references/agentware-roadmap.md`) and
> `MAIN.md § CURRENT CAMPAIGN`.

_Last updated: 2026-07-19._

---

## Vision

> *"The vision for agentware is to have an agent that **learns on demand what is required and how it is
> required, so I don't have to manage it** — it is self-managed by the agent."* — the operator

> *"I found out about ralph loops and that was the final part for my framework to be **self-sufficient
> and self-learning** — I could build **non-hallucinating context each time in a deterministic fashion**,
> so each loop only gets correct and relevant information to make informed decisions. Small context, low
> hallucination during execution."* — the operator

> *"If you use a long-memory tool you don't get an agent that utilises it the best way — **there are no
> guardrails and deterministic functions built in**. Looping + memory integration, especially with
> **self-learning, self-healing, and self-extension**, is the moat."* — the operator

The through-line: **today most agents capture but forget, and make you re-explain yourself.** agentware
turns that into an agent that *learns from your talking and its own mistakes, keeps its memory
trustworthy, turns any solved problem into a fleet-wide skill, and watches its own health — so you stop
managing it.*

---

## The self-learning loop

agentware self-improves across **three axes**, each guarded by a deterministic gate so a bad change can
never ship:

```
        ┌──────────────────────────────────────────────────────────────────────┐
        │  YOU steer in one line  ──▶  prefer / relate                           │
        └──────────────────────────────────────────────────────────────────────┘
                    │
   PLAN ──▶ EXECUTE ──▶ VERIFY ──▶ LEARN ──▶ TRUST ──▶ REUSE ──▶ SELF-AUDIT ──▶ (loop)
    │         │          │          │         │          │          │
 recall +  ralph-loop  premise +  capture   ACR +      skills    checkup
 grounded  deterministic gold-fix + decisions/ staleness two-tier (dream
 context   non-halluc.  review   gotchas    + trust   (fleet)   self-exam)
           context      gates
                    │
        ┌──────────────────────────────────────────────────────────────────────┐
        │  OFFLINE (dream): mines your transcripts + preferences → proposes work │
        └──────────────────────────────────────────────────────────────────────┘
```

| Axis | How it self-improves | The gate that keeps it safe |
|---|---|---|
| **Its knowledge** | deterministic recall + ACR (provenance/freshness) + trust & staleness surface + learnings lifecycle + the offline dream miner curate, rank, age-out, and re-mine its own memory | gold-fixture no-regression gate · `audit --stale` |
| **Its code** | self-extension plans + the `relate`/skills self-update let it plan, write, and ship its own features | adversarial-review gate · release gate · `steering lint` |
| **Its verification** | the premise-grounding gate + `eval --record --gate` + schema guard check its own plans against live reality before acting | premise gate (hard-abort) · eval win-gate |

**End to end:** dream mines your transcripts + preferences offline → proposes tier-2 workorders → you (or
the loop) turn them into grounded plans → the execution agent ships them → the adversarial-review gate
audits the diff → learnings promote back into the memory recall trusts → the next cycle mines it again.

---

## Status snapshot (2026-07-19)

- ✅ **The learning-pipeline campaign (P1 → P5) has shipped** — the reliability & memory *engine* is
  built: recall, gates, write-path scale, the flywheel (steering-capture + transcript miner), trust &
  staleness, fleet skills, and self-checkup.
- 🟢 **Ready:** a few reliability/ops closers.
- ⬜ **Open — the product & business half:** IP, CI, sandbox-by-default, GTM/wedge proof, and the
  enterprise control plane. The engine is strong; this is the path to a fundable product.

---

## Strategic roadmap — priority-ordered (from the 2026-07-07 strategic audit)

> **Gate logic:** nothing is fundable until IP is clean (#1) and diligence-proof until CI exists (#2);
> confirmed security & correctness (#3–6) precede everything commercial; wedge/GTM proof (#7–10)
> precedes the expensive control plane (#16). Status refreshed 2026-07-19.

| # | Item | Category | Effort | Impact | Status |
|---|------|----------|--------|--------|--------|
| 1 | Resolve the AWS employment / IP overhang (written IP assignment + moonlighting clearance) | legal | L | 🔴 blocker | ⬜ open |
| 2 | Stand up CI + branch protection (test suite, steering lint, AST guards, secret scan, release gate) | reliability | S | 🔴 blocker | ⬜ open |
| 3 | Sandboxed runtime by default (opt-in skip-perms, scoped Bash allowlist, deny hook on package paths) | security | M | 🟠 high | ⬜ open |
| 4 | Harden team-mode git-sync trust boundary (signed commits + trusted-author allowlist; vet synced skills) | security | L | 🔴 blocker | 🟡 partial (signed updater) |
| 5 | Public claims & positioning honesty pass | gtm | M | 🔴 blocker | ⬜ open |
| 6 | Fix correctness / data-integrity bug cluster (atomic+locked writes, `.done`+markers, secret redaction) | bug-fix | M | 🟠 high | ✅ mostly (P1.6/P1.7 + poisoned-index) |
| 7 | Reposition as "reliability & memory-governance layer — works with your harness" | gtm | M | 🟠 high | ⬜ open |
| 8 | Publish a defensible task-success lift number (SWE runner, n≥25, committed to the ledger) | product | M | 🟠 high | ⬜ open |
| 9 | Build the GTM surface & funnel (landing, demo, docs, pricing, opt-in usage ping) | gtm | M | 🟠 high | ⬜ open |
| 10 | Frictionless install & onboarding (`init --defaults`, pipx/brew/curl, `agentware join <kb-url>`) | product | M | 🟠 high | ⬜ open |
| 11 | v1 delegated identity / authz (repo-per-team KB, identity via git-host perms, signed per-handle commits) | enterprise | M | 🔴 blocker | 🟡 partial (P2d foundation) |
| 12 | Packaging, distribution & versioning (Dockerfiles, pipx/brew, semver+CHANGELOG, SBOM, `update`) | enterprise | M | 🟠 high | 🟡 partial (signed updater) |
| 13 | Compliance-grade audit trail (hash-chained JSONL, retention/scrub, per-row steering attestation) | enterprise | M | 🟠 high | ⬜ open |
| 14 | Enforced cost controls (per-run USD/token cap + per-user/team quota) | enterprise | S | 🟡 med | 🟡 partial (usage hook) |
| 15 | Hosted/shared team dashboard MVP (git-synced metrics + auth + OTel export) | enterprise | L | 🟠 high | 🟡 partial (local dashboard) |
| 16 | Enterprise control plane, post-demand (SSO/SAML/SCIM, server RBAC, multi-tenant — wrap the CLI) | enterprise | XL | 🔴 blocker | ⬜ open (deferred) |
| 17 | Per-iteration watchdog (timeout on a hung spawn so an unattended run can't stall forever) | reliability | S | 🟡 med | ✅ done (circuit breaker) |
| 18 | Retrieval scalability O(N) → O(1) (generation-counter invalidation + lazy sqlite postings) | scalability | M | 🟡 med | ✅ mostly (cache + O(1) freshness) |
| 19 | Decompose the monolith (internal package behind a shim, golden-output-locked + ruff/mypy) | scalability | L | 🟡 med | ⬜ open |
| 20 | Extract a clean runtime-adapter contract (spawn-argv + context-injector + stream-renderer per runtime) | product | M | 🟡 med | 🟡 partial (Codex + hybrid adapters) |
| 21 | Ship remaining planned features + roadmap currency (auto-skill-promotion, OKF export, multi-hop, decay; MAIN.md; this ROADMAP.md) | product | L | 🟢 low | ✅ mostly (P1–P5 + this doc) |

**The 5 that matter most:** (1) clear the AWS/IP question in writing; (2) add CI so every gate actually
runs; (3–4) sandbox-by-default + close the team-sync RCE before any team GA; (5) a free honesty pass so
the strong evidence stops getting discounted.

**Open diligence questions** (flagged by the audit, still unanswered): subscription-ToS compatibility for
unattended programmatic use · a **live end-to-end run was never done** (all loop tests use a fake agent
binary) · steering efficacy is unmeasured (~99 always-on rules, no compliance data) · team mode has never
had a second user.

---

## The learning-pipeline campaign (P-Series) — "what you get"

The engine track, in operator "what you get" framing. All shipped as of 2026-07-19 except P2d.

| Phase | What you get | Status |
|---|---|---|
| **P1** — learning plumbing | Recall actually returns your decisions; honest signals; the system can *measure* whether it uses what it learns | ✅ shipped |
| **P1.5** — plan lifecycle + workorders | Every plan's state at a glance; a lane for the agent to propose its own jobs you approve | ✅ shipped |
| **P1.6** — gates that work | "No regression" is now *true* — a bad change is actually caught | ✅ shipped |
| **P1.7** — write-path foundation | Recall stays fast as the KB grows; concurrent agents stop clobbering saves; clone/pull just works | ✅ shipped |
| **P2b** — scheduler + updater | Ship an improvement once → every machine has it within ~24h (signed, smoke-tested, auto-rollback) | ✅ shipped |
| **P2a** — steering capture (`prefer`) | Say "always use pnpm here" / "never add a co-author" **once** → in force every session | ✅ shipped |
| **P2c** — transcript miner | Dream mines the corpus — repeats, hit-and-fixed errors → durable knowledge, offline & quarantined | ✅ shipped |
| **P3** — trust & staleness | Recall marks + downweights stale/superseded knowledge; a proven-wrong note stops being trusted | ✅ shipped |
| **P4** — skills two-tier | Solve a hard problem once → a **skill** every agent invokes (KB-canonical + vetted cache, no sync-RCE) | ✅ shipped |
| **P5** — checkup | The system examines *itself* and reports at your next session — "3 plans stalled, this decision aged badly, here's a fix" | ✅ shipped |
| **P2d** — handle-namespacing | Add a 2nd person later **without a migration** — team shapes laid while cheap | 📋 draft (TBD) |
| 🔒 Structural change / Team mode | Trigger-gated — only when scale or a real 2nd person demands it | 🔒 gated |

**The endpoint** (verbatim from the campaign roadmap): *"An agent that learns on demand what's required
so I don't have to manage it. P2a is where it starts — it stops making you repeat yourself; P3–P5 is
where you start trusting it unattended."*

---

## What's next

1. **The 5 strategic blockers** (#1 IP, #2 CI, #3 sandbox-default, #4 team-sync, #5 honesty pass) — the
   path from strong engine to fundable product.
2. **Reliability/ops closers** — the review-gate spawn-resilience fix and the workorder/eval housekeeping.
3. **Finish #21** — OKF export (ship or retire), backfill plans for untracked shipped features.

---

*Provenance: the ranked 1–21 roadmap is from the 2026-07-07 strategic audit
(`work/260707-agentware-strategic-audit/`); the P-series was reconstructed from the 2026-07-10 planning
session; the vision quotes are from the operator's session logs. Keep this file current — a stale roadmap
in a "never forget" system is its own credibility bug (roadmap item #21).*
