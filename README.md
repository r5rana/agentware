<div align="center">

<img src="docs/assets/hero.png" alt="agentware — Self-Learning Agentic Loop with Reliable Deterministic Memory" width="100%">

<br/>

![tests](https://img.shields.io/badge/tests-332%20passing-2dd4bf)
![LongMemEval-S Recall@5](https://img.shields.io/badge/LongMemEval--S%20Recall%405-91.4%25-4ade80)
![dependencies](https://img.shields.io/badge/dependencies-zero%20·%20stdlib-0d9488)
![retrieval](https://img.shields.io/badge/retrieval-byte--identical-a3e635)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
![data](https://img.shields.io/badge/data-100%25%20local%20·%20yours-0d9488)

*Every loop builds on verified knowledge, self-heals, and gets smarter — zero LLM in the memory path, proven by benchmark. Open-source. Your data never leaves a directory you own. Free of cost framework*

</div>

---

## Reliable Agentic Loops with persistent memory

There are two kinds of tools today, and a gap between them:

- **Memory layers** (mem0, Letta, Zep, cognee, agentmemory) *remember* — but they're passive libraries with **no loop**, most let an **LLM write the memory** (extraction/summarization) so what's stored is non-deterministic and can drift or hallucinate, and several lean on a **hosted service** you don't fully control.
- **Agent harnesses** (OpenHands, OpenClaw, Cline, Aider, Goose) *drive* a loop — but their **memory is the weak link**: ephemeral context, or an **LLM deciding what to store**. OpenClaw's own research documents *"silent memory pollution"* where ordinary misinformation rewrites long-term memory ([arXiv 2603.23064](https://arxiv.org/abs/2603.23064)).

**agentware is the seat between them:** a real execution loop **and** memory that's deterministic, non-hallucinated, benchmark-gated, and managed entirely by code — so every loop deterministically reuses verified knowledge, and the system **self-heals, self-improves, and self-extends** as it runs.

**And it's open-source and fully yours.** agentware is Apache-2.0; your knowledge base is plaintext markdown + JSON in a directory **you choose** — git-native, with a full audit trail of every prompt and transcript. Nothing personal is committed to this repo, nothing phones home, and there's no account or hosted service in the loop: the framework runs on **your own** agent runtime — **Claude Code or OpenAI Codex**, chosen at onboarding (overridable via `AGENTWARE_CLI`).

---

## Feature comparison: agentware vs most used autonomous agentic harnesses

The loop is table stakes; **trustworthy memory + self-betterment is not.** Every cell is sourced from each project's own docs, repos, and papers (verified 2026-06) — e.g. OpenClaw's memory-pollution finding is from its own [research paper](https://arxiv.org/abs/2603.23064).

| Capability | **agentware** | OpenHands | OpenClaw | Cline | Aider | Goose | Hermes |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Autonomous execution loop | ✅ | ✅ | ✅ | ✅ | ⚠️ partial | ✅ | ✅ |
| Persistent cross-session memory (built-in) | ✅ | ⚠️ human-authored | ✅ | ❌ convention | ❌ per-session | ✅ | ✅ |
| **Memory managed by code, not an LLM** | ✅ | ❌ lossy summarizer | ❌ LLM-judge | ❌ | ✅ *(not persistent)* | ❌ | ❌ agent-curated |
| **Non-hallucinated** (no LLM authors memory) | ✅ | ⚠️ | ❌ *pollution documented* | ❌ | ✅ | ❌ | ❌ LLM-curated |
| Self-heal + self-improve (learn→rule) | ✅ | ❌ | ⚠️ gated skills | ❌ | ⚠️ narrow | ⚠️ human-gated | ✅ *(LLM Curator)* |
| Self-extend (writes its own skills) | ✅ | ❌ | ✅ gated | ❌ | ❌ | ⚠️ | ✅ autonomous |
| Benchmark-gated regression ledger | ✅ | ❌ | ❌ | ⚠️ evals | ⚠️ leaderboard | ⚠️ | ❌ |
| Memory/retrieval needs **no LLM or network** | ✅ | ❌ | ❌ | ❌ | ✅ *(map only)* | ❌ | ❌ |
| Maturity / adoption (★, approx) | new | ≈78k | ≈380k | ≈64k | ≈47k | ≈50k | ≈17k |

✅ yes/strong · ⚠️ partial/caveated · ❌ no/weak. **The honest takeaway:** every harness has a loop, but **none pairs it with deterministic, persistent, non-LLM-managed memory + a regression gate.** Where they lead, we say so: OpenHands has sandboxed code-exec breadth, Cline has deep IDE integration, **Hermes is the closest in spirit** — local-first, persistent, and self-extending — and all of them have vastly more adoption. The line that still separates us from Hermes: its memory is **LLM-curated** (an autonomous Curator grades and prunes it), where ours is **code-managed and benchmark-gated**.

In addition, since this is a framework, it is compatible with EVERY single LLM model, agent or harnesses. So you can use OpenClaw or Hermes with this framework and completely own the memory with all benefits offered here.

---

## Capability comparison: agentware vs industry-standard memory layers

| Axis | **agentware** | mem0 | Letta | Zep / Graphiti | agentmemory | cognee |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Has an execution loop (not just a library) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Deterministic retrieval | ✅ | ❌ | ❌ | ⚠️ | ✅ | ⚠️ |
| No LLM in the write path | ✅ | ❌ | ❌ | ❌ | ✅ default | ❌ |
| Zero hard deps / runs with nothing installed | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| **Local-first — your data in plain files, no account/SaaS** | ✅ | ⚠️ hosted offered | ⚠️ server/cloud | ⚠️ cloud | ✅ local | ⚠️ hosted offered |
| Comparable public Recall@5 | ✅ 91.4% | ❌ none | ❌ none | ⚠️ QA-acc | ✅ 95.2% | ⚠️ HotPotQA |
| Benchmark-gated reliability ledger | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Governed learning → rule loop | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Semantic / embedding retrieval | ✅ opt-in (Mode B) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Knowledge graph / multi-hop | ⚠️ roadmap | ✅ | ❌ | ✅ | ✅ opt | ✅ |
| Maturity / adoption (★, approx) | new | ≈59k | ≈23k | ≈28k | ≈24k | ≈22k |

**Honest gaps:** agentware trails on **knowledge-graph multi-hop** and **adoption**. On **raw semantic recall**, a **local, rank-only** semantic mode (no LLM, no cloud) now ships as **opt-in Mode B** (see the measured A/B below) — but on the comparable public benchmark it's a wash at ~111× the latency, so the deterministic BM25 stdlib path stays the zero-install **default**.

---

## The numbers (own, verified, reproducible)

agentware ships an append-only benchmark ledger — every number below traces to a committed row in `benchmarks/history.jsonl`, pinned to commit `42d58a0`.

| Benchmark | Metric | agentware (Mode A — pure stdlib BM25, zero deps) |
|---|---|---|
| **LongMemEval-S (cleaned)** — public | **Recall@5** | **0.9140** · nDCG@5 0.8831 · MRR 0.9104 (470 answerable; 30 abstention scored separately) |
| Own 56-pair gold set | Recall@5 | 0.9554 (reliability 98.2) |
| BM25 vs no-ranking baseline (own gold) | Recall@5 lift | **0.6161 → 0.9554** (+0.34) |
| Retrieval latency | p50 | ~10.5 ms · **byte-identical across runs** |

**Reproduce it yourself (no account/token, pure stdlib):**

```bash
# Fetch the dataset pinned to an exact commit + sha256, then run:
#   (full fetch/verify steps in docs/GUIDE.md → "Benchmark methodology & numbers")
scripts/agentware eval --suite longmemeval --strategy bm25 --top-k 5 --as-of 2026-06-25
scripts/agentware bench scorecard     # regenerate the human-readable scorecard view
```

> **Honest framing — read this.** ① 0.9554 is our **own 56-pair gold set**, not LongMemEval — not apples-to-apples. ② Recall@5 on LongMemEval-S is comparable **only** to systems measured on the same setup (agentmemory: 86.2% BM25-only / 95.2% hybrid; MemPalace 96.6% vector-only). The LongMemEval *paper's* numbers are on the larger `-M` variant with a dense embedder — **not** comparable, so we don't put them head-to-head. ③ Most memory layers headline **QA-accuracy**, a different metric — we compare on capabilities, not forced numbers. ④ agentware is **new**; the projects compared above are far more adopted.

### Optional Mode B (local semantic) — measured A/B, with the honest verdict

Mode A (BM25) is the **default**. Mode B adds an optional local embedding model (`fastembed`, pinned, on-device, no PyTorch) and retrieves with hybrid `bm25+embed`. We A/B'd them on the same data — every cell is a real recorded ledger row:

| Benchmark | Mode A — BM25 (Recall@5 / nDCG@5 / MRR) | Mode B — BM25+embed | Δ Recall@5 | Latency / query | Slowdown |
|---|---|---|---|---|---|
| **LongMemEval-S (470q, public)** | 0.9140 / 0.8831 / 0.9104 | 0.9223 / 0.8806 / 0.8974 | **+0.0083** (noise; nDCG/MRR regress) | **16.5 ms → 1830 ms** | **~111×** |
| Own 56-pair gold (lexically aligned) | 0.9554 / 0.9285 / 0.9326 | 0.9911 / 0.9678 / 0.9673 | **+0.0357** | **10.5 ms → 29.5 ms** | **~2.8×** |

> **Recommendation: keep Mode A (BM25) — the fast, zero-install default (this is what onboarding recommends).** On the comparable *public* benchmark, embeddings are a **wash-to-slight-loss at ~111× the latency** (1830 ms vs 16.5 ms per query); the only measured win is on a small, lexically-aligned own set. Mode B is worth it **only** for paraphrase-heavy KBs where BM25 genuinely underperforms — opt in at onboarding or any time via `scripts/agentware config --set-retrieval semantic` (and back with `--set-retrieval bm25`). Embeddings only **rank**; they never author memory, so Mode B stays non-hallucinated and deterministic (pinned model + cached vectors).

---

## 🧠 Why the numbers are that good — agentware loops don't hallucinate

> **No human — and no LLM — in agentware's memory layer.**

The benchmark above isn't luck. Most "agent memory" is a growing blob of text an LLM dumps in and greps back out: unstructured, lossy, different on every run. **agentware's framework layer has neither a human nor an LLM in it** — deterministic **functions** write, index, and retrieve knowledge.

Every entry is **organized at write-time** — typed, tagged, frontmatter'd, indexed — structured *for retrieval before it's ever needed*, not a rotten chunk searched after the fact. So the context each **agentware loop** receives is **minimal, relevant, and re-creatable**: the same inputs deterministically rebuild the *same* clean, token-efficient context every time.

That's why **agentware loops reason over organized, verified knowledge instead of a noisy haystack** — they don't drift, don't hallucinate, and produce the same high-quality result run after run. The **91.4%** above is the consequence of structured-at-write-time, deterministically-reconstructed context — no embeddings or LLM in the path required.

The moat in one line:

> **Memory organized at write-time by deterministic functions — not a blob an LLM dumps and greps. Clean context in → reliable result out, identically every run.**

---

## Quickstart

```bash
# 1. Clone wherever you want your instance to live
git clone <this-repo> agentware && cd agentware

# 2. Run your agent runtime inside the repository — onboarding auto-starts:
#    it asks which runtime to use (Claude Code or OpenAI Codex), where to store your
#    knowledge base, scaffolds it, and personalizes. Pay attention during onboarding.
claude "hi"      # Claude Code (default), OR
codex "hi"       # OpenAI Codex — onboarding records your choice via `config --set-cli`

# 3. Write a short plan using PLAN_AW, then fire-and-forget the loop:
./agentware.sh <YYMMDD-feature>

# or alternatively, execute the plan in interactive mode using command WORK_AW
```

Nothing personal is ever committed: the repo is **pure steering**, and your knowledge base lives in an **external directory you choose**. The same clone works for anyone. See the [User Guide](docs/GUIDE.md).

---

## Features

- **The 3-phase loop** (`agentware.sh`): **Pre** (sharpen the plan) → **Main** (execute + verify each task) → **Post** (self-assess). Promise-gated; trustworthy to run unattended.
- **Deterministic toolkit** (`scripts/agentware`): the **sole writer** of the knowledge index/learnings — valid JSON, consistent tags, no duplicates, paths relative to your dir.
- **Reliable memory**: `recall` (ranked BM25 retrieval), `eval` (benchmark suites), an append-only `history.jsonl` ledger + regenerated `SCORECARD.md`.
- **Self-improvement**: worklog `> LEARNED:` markers are promoted into durable, ID'd steering rules — linted in CI.
- **Self-healing**: every subtask is verified with *your* project's own build/test/health command; failures retry, not ship.
- **Self-extension**: ship features into agentware the same way you ship them into any project (gated by a `!! WARNING !!` for package edits).
- **(Experimental branched feat/HybridModels) Hybrid & local models**: route each loop phase (Pre/Main/Post) to its own runtime+model — keep planning + assessment on cloud Claude and run *execution* on a **local** model (`gpt-oss-20b` / `gemma-26b` via LM Studio + Codex `--oss`). No per-phase keys ⇒ byte-identical all-cloud; a no-progress circuit breaker, opt-in cloud fallback, and one-command revert keep it safe unattended. **[Benchmarked](#local-models): the framework lifts weaker executors most, and KB memory compounds model-independently.**. 
- **Git-native & private**: plaintext markdown + JSON; team-sync over git; full audit trail (every prompt + session transcript) in *your* external dir.

---

## How it works

```
<knowledge-dir>/work/<YYMMDD-feature>/plan.md     # you write phases + acceptance criteria using PLAN_AW
        │
        ▼   ./agentware.sh <feature> to run in LOOP mode with fresh context each iteration or in an interactive mode using WORK_AW
   ┌─────────── PRE ───────────┐  sharpen the plan, no scope change
   ├─────────── MAIN ──────────┤  execute one task → verify → recall prior knowledge → worklog
   └─────────── POST ──────────┘  self-assess → assessment.md → promote learnings
```

The toolkit guarantees *how* knowledge is written; the agent decides *what*. Retrieval is byte-identical (no LLM/RNG/network/wall-clock in ranking), proven by guard tests: `test_recall_json_is_byte_identical_across_runs`, `test_cli_imports_are_stdlib_only`, `test_recall_leaves_entire_kb_tree_unchanged` (`tests/test_invariants.py`).

Full walkthrough: [docs/GUIDE.md](docs/GUIDE.md) · plan format: [docs/loop.md](docs/loop.md) · rationale: [docs/methodology.md](docs/methodology.md).

---

<a id="local-models"></a>

## 🪶 Small & local models — same framework, weaker models, still ship

You don't need a frontier model to get tasks done. agentware's loop — **plan → execute → verify → recall → self-heal** — is exactly the scaffold a *smaller* model needs to punch above its weight: it sharpens the plan before the weak model touches anything, verifies every subtask with your project's own test/health command (so a wrong edit retries instead of shipping), and injects only the relevant, deterministically-retrieved prior knowledge. **The weaker the executor, the more the framework lifts it** — and that's measured below, not asserted.

### Per-phase routing — the knob

Every loop phase picks its **own** runtime + model. Resolution is **env → phase `config.env` → global `AGENTWARE_CLI`/`MODEL` → default `claude`**, so with *no* per-phase keys the loop is **byte-identical all-cloud** (fully backward-compatible — the new surface is inert until you opt in).

| Knob | What it sets | Values |
|---|---|---|
| `AGENTWARE_{PRE,MAIN,POST}_CLI` | runtime for that phase | `claude` \| `codex` |
| `AGENTWARE_{PRE,MAIN,POST}_MODEL` | model id for that phase | e.g. `opus`, `haiku`, `gpt-oss-20b`, `gemma-26b` |
| `AGENTWARE_{PRE,MAIN,POST}_LOCAL` | local provider (Codex `--oss`) | `lmstudio` \| `ollama` |

### The hybrid profile (recommended) — cloud brains, local hands

Keep **planning + assessment on cloud Claude** (you never want a weak model judging its own plan or honesty), and run the high-volume **execute** phase on a **local** model:

```bash
# Persist the hybrid profile (effective on the NEXT run):
scripts/agentware config --set-main-cli   codex
scripts/agentware config --set-main-local lmstudio
scripts/agentware config --set-main-model gpt-oss-20b   # or gemma-26b (the more reliable local editor)

# …or set it for a single run, no persistence:
AGENTWARE_MAIN_CLI=codex AGENTWARE_MAIN_LOCAL=lmstudio AGENTWARE_MAIN_MODEL=gemma-26b \
  ./agentware.sh <YYMMDD-feature>

# Revert to all-cloud at any time (one command):
scripts/agentware config --set-main-cli claude
```

Under the hood the execute phase then spawns `codex exec --oss --local-provider lmstudio -m <model> …`; PRE/POST stay on `claude`. (Always pass `--local-provider lmstudio` — `codex --oss` silently falls back to Ollama, and may auto-pull a huge model, if the LM Studio server is unreachable.)

### Safety rails (so it's safe to run unattended)

- **No-progress circuit breaker** — `AW_NOPROGRESS_ABORT` halts a phase that spins without flipping a task marker, so a stuck weak model can't burn the loop.
- **Opt-in cloud fallback** — `AGENTWARE_MAIN_FALLBACK=claude` re-runs a stalled local task on cloud Claude.
- **Cloud-only reconcile** — promote/merge reconcile steps always route to cloud regardless of main routing.
- **One-command revert** — `scripts/agentware config --set-main-cli claude`.
- **Cost-safe by construction** — all cloud calls go through your Claude Code **subscription** (no API key, no per-token billing, no spend cap; quota is the only ceiling). Local calls cost **zero** dollars and stay **fully private** on your machine.

> **One-time local PRE-FLIGHT.** The local stack (LM Studio MLX serving the `/v1/responses` API Codex needs) must be brought up **manually once** — the loop won't start servers headlessly. On a 24 GB M4 Pro the two local executors **can't co-reside** (`gpt-oss-20b` ≈ 12 GB, `gemma-4-26b-a4b-qat` ≈ 15 GB); rotate one at a time with `lms load`/`lms unload` and re-probe `:1234`. Full steps + verified pitfalls: [docs/loop.md → per-phase routing & the hybrid local-executor profile](docs/loop.md#per-phase-routing--the-hybrid-local-executor-profile).

### The benchmarks — does a weaker model actually get tasks done?

A controlled matrix: **3 tests × harness {without, with framework} × executor {opus, sonnet, haiku, gpt-oss-20b (local), gemma-26b (local)}**. Cloud runs on the Claude subscription; local runs on LM Studio + Codex `--oss` on a 24 GB M4 Pro. Raw rows are committed (`bench/*.csv` + `REPORT.md`). Small-N by design — treat deltas as **directional**, and read the honest confounds below.

**① The framework lifts weaker executors the most.** On the *hard* synthetic exercises (`list-ops`, `bowling`) — exactly where headroom exists — the **bare** model is unreliable for every tier; inside the framework loop, every executor that can emit edits reaches the all-Opus reference pass rate:

| Executor | Bare (hard exercises) | Inside framework | Lift |
|---|:--:|:--:|:--:|
| opus (reference) | 1/4 (25%) | 2/2 (100%) | already strongest — smallest headroom |
| sonnet | 2/4 (50%) | 2/2 (100%) | **+50pp** |
| **haiku** (cheapest cloud) | 2/4 (50%) | 2/2 (100%) | **+50pp → reaches the Opus reference** |
| **gemma-26b** (local) | 0/2 (bare timeout/fail) | 1/1 (100%) | **fail → verified solve** |
| gpt-oss-20b (local) | 0/2 | 0/1 | *no lift — executor-blocked, see note* |

> The lone non-lift is **not** a framework failure: under Codex 0.142.3 `gpt-oss-20b`'s `apply_patch` tool-call fails to parse deterministically, so it never emits an edit. The reason→recall→verify scaffold is sound; the model just can't act on it — which is why **`gemma-4-26b-a4b-qat` is the recommended default local *editor*** (`gpt-oss-20b` is fine for shell-driven edits).

**② "Never forget" compounds — and it's model-independent.** A sequence of 3 *related* bugs; with memory on, each verified fix is `recall`-able for the next, so retrieval hits climb. The no-memory control never accumulates. It reproduces **identically on cloud and local**:

| Arm | recall_hits chain (bug 1 → 2 → 3) |
|---|:--:|
| control (memory off, any executor) | **0 → 0 → 0** |
| with memory · opus (cloud) | **0 → 1 → 2** |
| with memory · haiku (cloud) | **0 → 1 → 2** |
| with memory · **gpt-oss-20b (local)** | **0 → 1 → 2** |
| with memory · **gemma-26b (local)** | **0 → 1 → 2** |
| with memory · sonnet (cloud) | 0 → 0 → 1 *(confound — see below)* |

The deterministic `recall` is the *single* varied factor — so the "never forget" payoff is the framework's, not the model's, and a small local model gets it for free.

**③ No-regression safety holds for local executors too.** On a real-repo bug-fix (`python-slugify` regression, scored by the repo's own 82-test suite), **every** executor passed both bare and framed — including the local ones (`gemma-26b` solved 82/82 via sed-edit; `gpt-oss-20b` 82/82 via shell edits). The easy single-hunk bug *saturates* (no headroom to discriminate), so it yields a clean **no-regression** signal plus a cost/time spread, not a discrimination signal.

**Cost & time.** Bare cloud one-shot is cheapest per *easy* task (~10–20 s) but unreliable on hard ones. The framework adds 10–20× wall-clock (the plan→verify→assess scaffold) — worth it exactly when a task is **hard** (Test ①) or **recurring** (Test ②). Local trades dollars for wall-clock: **zero marginal cost, fully private, ~10–40× slower** than cloud on this box.

**Honest confounds (read these).** ① Small N — directional, not statistically significant. ② The easy real-repo bug saturates; true framework-effect measurement needs a harder multi-file bug (logged as future work). ③ The `sonnet` compounding cell shows `0,0,1` because bug 1 hit a `claude -p` **stdout-reconstruction artifact** (a truncated/fenced response wrote an incomplete file — a harness output bug, *not* a reasoning failure), so no learning promoted; recorded as-is rather than re-run to tidy the data. Tool-loop executors (`codex exec` edits files in place) never hit this class.

**Verdict:** the hybrid profile is the practical default — **pre/post on cloud Claude, main chosen by task** (Opus for hard one-shots; a cheaper or local main when the verify-loop + KB memory carry it). For weaker and local models, the framework is the difference between "couldn't do it bare" and "verified solve."

---

## Roadmap

agentware is honest about where it's *not yet* industry-standard:

- **🌙 Dream mode** *(next)* — idle-time background maintenance ("dreams"). **The fundamental:** in agentware the *interactive* path stays flat as the KB grows — retrieval is ranked + **token-budgeted**, so each loop injects a bounded, relevant slice no matter how large the KB gets. The **only** work that scales with size is *maintenance*: re-indexing, re-embedding, audit, dedup, PII-redaction, git-sync. Dream mode moves all of it **off the hot path** — a scheduled, idle-gated job that rebuilds the BM25/vector caches, runs the health + benchmark gates, promotes pending learnings, scrubs the ledger, dedups/compacts entries, syncs the KB to git, and leaves a "dream journal" for the morning. You wake to a fresh, compacted, backed-up KB and never feel the maintenance cost. *Phase 1 = deterministic ops (safe, unattended); Phase 2 = LLM-assisted curation behind a review queue.*
- **Knowledge-graph / multi-hop** retrieval — deeper traversal over the shipped deterministic dependency graph.
- **Broader runtime support** (native Windows; more agent CLIs beyond the shipped Claude Code + OpenAI Codex).

**Shipped since this list started:** **hybrid per-phase model routing + local executor** (run the execute phase on a local `gpt-oss-20b`/`gemma-26b` via LM Studio + Codex `--oss` while planning/assessment stay on cloud Claude — [benchmarked above](#local-models)), **Mode B** local semantic retrieval (optional/opt-in — see the measured A/B above; BM25 stays default), the **observability dashboard** (live loop + benchmark health), and a deterministic **KB dependency graph**.

**Deliberately *not* on the roadmap (the moat line):** an LLM in the retrieval or write path. That is exactly where determinism and non-hallucination break — and it's our whole point.

---

## Requirements

- **An agent runtime: Claude Code (`claude`) or OpenAI Codex (`codex`)** — chosen at
  onboarding and persisted via `scripts/agentware config --set-cli claude|codex`;
  override per-run with `AGENTWARE_CLI=claude|codex`. Resolution is env → config →
  default `claude`. See [docs/loop.md](docs/loop.md#runtime-adapter-claude-code--openai-codex)
  for the Codex adapter (the `codex exec` invocation, sandbox/approval mapping, persona
  injection, and `--json` logging renderer).
- **Optional hybrid (local-executor) profile** — the runtime+model is selectable
  **per loop phase** via `AGENTWARE_{PRE,MAIN,POST}_{CLI,MODEL,LOCAL}` (set with
  `config --set-{pre,main,post}-{cli,model,local}`). The **hybrid** default keeps
  plan+assess on cloud Claude and runs *execute* on a **local model**
  (`gpt-oss-20b` via LM Studio + `codex --oss --local-provider lmstudio`). No
  per-phase keys ⇒ byte-identical all-cloud. A no-progress circuit breaker
  (`AW_NOPROGRESS_ABORT`), opt-in `AGENTWARE_MAIN_FALLBACK=claude`, and a
  one-command revert (`config --set-main-cli claude`) make it safe to run
  unattended. **Cost-safe by construction:** all cloud calls go through your
  Claude Code **subscription** (no API key, no per-token billing, no spend cap;
  quota is the only ceiling). One-time LM Studio PRE-FLIGHT + the verified 24 GB
  pitfalls are in [docs/loop.md](docs/loop.md#per-phase-routing--the-hybrid-local-executor-profile).
- **POSIX shell + `bash` + `jq` + Python 3** — macOS, Linux, or Windows via WSL/Git-Bash.
- Git optional (onboarding offers `git init` + push via `gh`). Node.js ≥ 18 only for optional Playwright UI verification.

---

## Contributing

PRs and issues welcome. agentware governs *itself* the same way it governs your work — changes to its own steering/skills/loop go through a plan, a `!! WARNING !!` self-extension gate, and the `steering lint` + benchmark gates before they ship. Run `python3 -m unittest discover -s tests` and `scripts/agentware audit --with-tests` before opening a PR.

## License

[Apache-2.0](LICENSE) © 2026 Rahul Rana — includes an explicit patent grant; see [`NOTICE`](NOTICE).
