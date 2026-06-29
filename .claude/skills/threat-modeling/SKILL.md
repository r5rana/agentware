---
name: threat-modeling
description: >-
  Enumerate threats at design time using STRIDE, data-flow diagrams, and
  trust-boundary analysis. When asked to "threat model this", "do a STRIDE
  analysis", "what could go wrong with this design", "map the attack surface",
  "find the trust boundaries", or when scoping a new system, integration, agent
  loop, or data flow BEFORE code is written, decompose the system into a DFD,
  draw trust boundaries, enumerate threats per element with STRIDE, rank by
  risk, and propose a mitigation for each. Fills the gap between secure-by-design
  (a requirements checklist) and sast-audit (a code scanner). Self-contained;
  portable across any agentskills.io harness.
---

# Threat Modeling (STRIDE)

> **Portable Agent Skill** (agentskills.io open standard). The YAML frontmatter
> above is the spec-compliant contract: `name` equals the folder name and
> `description` is the routing text. The body is HARNESS-AGNOSTIC — no hardcoded
> invocation syntax, no harness-only frontmatter. It needs no network and no
> external tools; it is a structured reasoning procedure applied to a design.

> **When to invoke**: at design time, when a system or feature is being scoped
> and you need to enumerate WHAT can go wrong before deciding HOW to defend.
> Trigger it when the user asks to "threat model", run a "STRIDE analysis", map
> an "attack surface", or reason about abuse of a new endpoint, data flow,
> integration, queue, or agent/LLM loop. Use it AFTER `secure-by-design` has
> framed the requirements and BEFORE `sast-audit` scans real code: this skill
> produces the prioritized threat list those two bracket.

## Why this skill exists

A requirements checklist (`secure-by-design`) tells you which controls a feature
generally needs; a scanner (`sast-audit`) finds flaws in code that already
exists. Neither systematically answers "given THIS architecture, what is the
specific set of things an attacker can attempt, ranked by how much they hurt?"
Threat modeling does. Without a written procedure, ad-hoc threat reasoning is
biased toward the threats the author already knows and silently skips whole
categories (repudiation, denial of service, elevation of privilege). STRIDE plus
a data-flow decomposition forces coverage: every element of the system is walked
against every threat category, so gaps surface as explicit "N/A, because…" notes
rather than invisible omissions. The output is a durable artifact — a ranked
threat table with a mitigation per threat — that the build, the tests, and a
later security review all consume.

## Prerequisites

- A description of the design: its components, the data they exchange, who calls
  them, and what they depend on externally. If the architecture is unstated or
  vague, ASK before modeling — a threat model built on a guessed data flow
  enumerates the wrong threats.
- This skill ENUMERATES and PROPOSES; it never weakens a control and never
  auto-applies an irreversible change (R-AUTO-02). Its product is analysis, not
  edits to running systems.
- Treat all crossing data — user input, third-party responses, file content,
  tool/LLM output — as untrusted at every boundary (R-SEC-02). Never put secrets
  in the model artifact, logs, or examples (R-SEC-01).

## Procedure

### Step 1 — Decompose into a data-flow diagram (DFD)

Identify and list the DFD elements; a threat is always anchored to one of them:

- **External entities**: actors and systems outside your control (users,
  browsers, third-party APIs, an LLM provider, an attacker).
- **Processes**: code that acts on data (services, functions, workers, an agent
  loop, a hook/executor).
- **Data stores**: where data rests (databases, caches, queues, files, secrets
  managers, the LLM context window treated as a store of in-flight data).
- **Data flows**: each directed edge — what data moves from which element to
  which, over what channel.

Render it as a simple text DFD (an ASCII/Mermaid sketch or an element + edge
list is sufficient — no tooling required). Keep one diagram per bounded scope; a
sprawling whole-system diagram hides boundaries.

### Step 2 — Draw the trust boundaries

Overlay the lines where the privilege or trust level changes — the only places
threats actually cross:

- client → server, browser → API, public internet → internal network;
- service → service across an authorization or tenant boundary;
- third-party / user-supplied content → your process (including
  untrusted-content → LLM context: prompt-injection lives here);
- low-privilege process → high-privilege process or secret store;
- sandbox → host (CI runner, agent executor, container escape).

Every data flow that crosses a boundary is where you will spend your STRIDE
attention in Step 3. Flows wholly inside one trust zone are lower priority.

### Step 3 — Enumerate threats per element with STRIDE

Walk each element/flow against the six STRIDE categories. State a concrete threat
for THIS design, or mark the category N/A with a one-line reason — do not leave a
cell blank:

- **S — Spoofing** (authenticity): can an attacker impersonate a user, service,
  token, or origin? (weak/absent auth, forgeable identity, missing mTLS, request
  forgery). Counter-property: authentication.
- **T — Tampering** (integrity): can data in transit or at rest be modified?
  (unsigned payloads, mutable logs, mass-assignment, parameter tampering,
  unvalidated deserialization). Counter-property: integrity.
- **R — Repudiation** (non-repudiation): can an actor deny an action because it
  was not logged or logs are forgeable? (no audit trail, tamperable logs).
  Counter-property: auditability.
- **I — Information disclosure** (confidentiality): can data leak? (missing
  encryption, IDOR exposing another tenant's data, verbose errors, secrets in
  logs/URLs, over-broad LLM context, SSRF reading internal/metadata endpoints).
  Counter-property: confidentiality.
- **D — Denial of service** (availability): can the element be exhausted or
  wedged? (no rate limits, unbounded input/recursion, amplification, expensive
  queries, unbounded token/cost consumption in an LLM loop). Counter-property:
  availability.
- **E — Elevation of privilege** (authorization): can an actor gain rights they
  should not have? (broken object/function-level authz, injection that runs
  code, confused-deputy, excessive agent agency, sandbox escape).
  Counter-property: authorization.

Bias guidance: at each trust boundary, **Spoofing/Tampering/Disclosure** dominate
on the flow itself; **Elevation** dominates on the higher-privilege process;
**Repudiation/DoS** dominate on stores and externally reachable processes.

### Step 4 — Rank by risk

For each enumerated threat, assign a qualitative risk so mitigation effort goes
where it matters. Use Likelihood × Impact (High/Medium/Low each), or note DREAD
if the team prefers it — keep it consistent. Factor in exposure (internet-facing
beats internal), attacker capability required, and blast radius (one row vs. the
whole tenant). Sort the table highest-risk first.

### Step 5 — Propose a mitigation per threat

For every threat, give one of the four responses, with the concrete control:

- **Mitigate** — the default: name the specific control (e.g. "object-level
  authz check on `GET /orders/:id`", "rate-limit login to N/min/IP",
  "allow-list outbound hosts, block metadata range"). Cross-reference the
  matching `secure-by-design` requirement where one exists.
- **Eliminate** — remove the feature/flow/exposure that creates the threat.
- **Transfer** — push the risk to a component designed for it (a managed auth
  provider, a WAF, a secrets manager) — and note the residual.
- **Accept** — only with an explicit, written rationale and an owner; never
  silently drop a high/medium threat.

### Step 6 — Produce the threat-model artifact

Output a self-contained artifact:

- The **DFD** (text/Mermaid) with trust boundaries marked.
- A **threat table**: id · element/flow · STRIDE category · threat description ·
  likelihood · impact · risk · response · mitigation/control · verification hook.
- **Acceptance criteria** for the mitigations, phrased so `backend-verification`,
  `test-authoring`, and `sast-audit` can later confirm them (e.g. "non-owner
  GET returns 403", "login throttles after N attempts").
- An explicit **residual-risk** list (everything Accepted/Transferred) with
  rationale and owner.

## Failure handling

- If the architecture is ambiguous, STOP and ask rather than modeling a guessed
  data flow — the wrong DFD yields the wrong threats and a false sense of safety.
- If the system is too large to model in one pass, decompose by trust boundary or
  by bounded context and model each sub-DFD separately; a vague whole-system pass
  misses the boundary-crossing threats that matter most.
- If a STRIDE cell is genuinely not applicable, record it as "N/A — <reason>",
  never blank; a blank cell is indistinguishable from an overlooked threat.
- If modeling surfaces a threat with no acceptable mitigation, escalate it to the
  user as a design blocker rather than quietly accepting it.

## Gotchas

- This is enumeration, not verification — a threat model lists what COULD go
  wrong; it does not prove the mitigations exist. Hand the acceptance criteria to
  `backend-verification`/`sast-audit` to confirm them on real code.
- STRIDE coverage is the point: the value is in the categories you would have
  skipped (Repudiation and DoS are the usual blind spots), so fill every cell.
- Threats cross at trust boundaries; modeling flows inside a single trust zone in
  depth while skipping a boundary-crossing flow inverts the priority.
- "Authenticated" is not "authorized": Elevation-of-privilege via broken
  object-level authz (IDOR) is the most common real-world finding — model it on
  every higher-privilege process, not just the login flow.
- For LLM/agent designs, the context window is a trust boundary and a data store
  at once: untrusted content entering it is Tampering/Spoofing of instructions
  (prompt injection), and its output driving a tool is Elevation — model both.
- A threat model is a living artifact: re-run this skill when the architecture,
  trust boundaries, or external dependencies change.

## See also

- `.claude/skills/secure-by-design/SKILL.md` — the proactive requirements
  checklist that frames the design this skill enumerates threats against.
- `.claude/skills/sast-audit/SKILL.md` — scan the implemented code for the
  threat classes this model identifies.
- `.claude/skills/ci-cd-security-audit/SKILL.md` — threat-model the pipeline and
  agent integration points specifically.
- `.claude/skills/dependency-supply-chain-audit/SKILL.md` — covers the
  supply-chain/Tampering threats on third-party components.
- Related learnings in the external knowledge dir: `learnings/` (trust-boundary
  and egress-control gotchas).
