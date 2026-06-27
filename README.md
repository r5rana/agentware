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

## Roadmap

agentware is honest about where it's *not yet* industry-standard:

- **🌙 Dream mode** *(next)* — idle-time background maintenance ("dreams"). **The fundamental:** in agentware the *interactive* path stays flat as the KB grows — retrieval is ranked + **token-budgeted**, so each loop injects a bounded, relevant slice no matter how large the KB gets. The **only** work that scales with size is *maintenance*: re-indexing, re-embedding, audit, dedup, PII-redaction, git-sync. Dream mode moves all of it **off the hot path** — a scheduled, idle-gated job that rebuilds the BM25/vector caches, runs the health + benchmark gates, promotes pending learnings, scrubs the ledger, dedups/compacts entries, syncs the KB to git, and leaves a "dream journal" for the morning. You wake to a fresh, compacted, backed-up KB and never feel the maintenance cost. *Phase 1 = deterministic ops (safe, unattended); Phase 2 = LLM-assisted curation behind a review queue.*
- **Knowledge-graph / multi-hop** retrieval — deeper traversal over the shipped deterministic dependency graph.
- **Broader runtime support** (native Windows; more agent CLIs beyond the shipped Claude Code + OpenAI Codex).

**Shipped since this list started:** **Mode B** local semantic retrieval (optional/opt-in — see the measured A/B above; BM25 stays default), the **observability dashboard** (live loop + benchmark health), and a deterministic **KB dependency graph**.

**Deliberately *not* on the roadmap (the moat line):** an LLM in the retrieval or write path. That is exactly where determinism and non-hallucination break — and it's our whole point.

---

## Requirements

- **An agent runtime: Claude Code (`claude`) or OpenAI Codex (`codex`)** — chosen at
  onboarding and persisted via `scripts/agentware config --set-cli claude|codex`;
  override per-run with `AGENTWARE_CLI=claude|codex`. Resolution is env → config →
  default `claude`. See [docs/loop.md](docs/loop.md#runtime-adapter-claude-code--openai-codex)
  for the Codex adapter (the `codex exec` invocation, sandbox/approval mapping, persona
  injection, and `--json` logging renderer).
- **POSIX shell + `bash` + `jq` + Python 3** — macOS, Linux, or Windows via WSL/Git-Bash.
- Git optional (onboarding offers `git init` + push via `gh`). Node.js ≥ 18 only for optional Playwright UI verification.

---

## Contributing

PRs and issues welcome. agentware governs *itself* the same way it governs your work — changes to its own steering/skills/loop go through a plan, a `!! WARNING !!` self-extension gate, and the `steering lint` + benchmark gates before they ship. Run `python3 -m unittest discover -s tests` and `scripts/agentware audit --with-tests` before opening a PR.

## License

[Apache-2.0](LICENSE) © 2026 Rahul Rana — includes an explicit patent grant; see [`NOTICE`](NOTICE).
