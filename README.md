<div align="center">

<img src="docs/assets/hero.png" alt="agentware — Self-Learning Agentic Loop with Reliable Deterministic Memory" width="100%">

<br/>

![tests](https://img.shields.io/badge/tests-332%20passing-2dd4bf)
![LongMemEval-S Recall@5](https://img.shields.io/badge/LongMemEval--S%20Recall%405-91.4%25-4ade80)
![dependencies](https://img.shields.io/badge/dependencies-zero%20·%20stdlib-0d9488)
![retrieval](https://img.shields.io/badge/retrieval-byte--identical-a3e635)
![license](https://img.shields.io/badge/license-Apache--2.0-blue)
![data](https://img.shields.io/badge/data-100%25%20local%20·%20yours-0d9488)

**Reliable agentic loops with deterministic, persistent memory — open-source, and 100% yours.**
*Every loop builds on verified knowledge, self-heals, and gets smarter — zero LLM in the memory path, proven by benchmark. Open-source. Your data never leaves a directory you own.*

</div>

---

## Reliable Agentic Loops with persistent memory (open-source, 100% data ownership)

There are two kinds of tools today, and a gap between them:

- **Memory layers** (mem0, Letta, Zep, cognee, agentmemory) *remember* — but they're passive libraries with **no loop**, most let an **LLM write the memory** (extraction/summarization) so what's stored is non-deterministic and can drift or hallucinate, and several lean on a **hosted service** you don't fully control.
- **Agent harnesses** (OpenHands, OpenClaw, Cline, Aider, Goose) *drive* a loop — but their **memory is the weak link**: ephemeral context, or an **LLM deciding what to store**. OpenClaw's own research documents *"silent memory pollution"* where ordinary misinformation rewrites long-term memory ([arXiv 2603.23064](https://arxiv.org/abs/2603.23064)).

**agentware is the seat between them:** a real execution loop **and** memory that's deterministic, non-hallucinated, benchmark-gated, and managed entirely by code — so every loop deterministically reuses verified knowledge, and the system **self-heals, self-improves, and self-extends** as it runs.

**And it's open-source and fully yours.** agentware is Apache-2.0; your knowledge base is plaintext markdown + JSON in a directory **you choose** — git-native, with a full audit trail of every prompt and transcript. Nothing personal is committed to this repo, nothing phones home, and there's no account or hosted service in the loop: the framework runs on **your own** agent runtime (e.g. Claude Code).

---

## How agentware stands vs autonomous agent harnesses

The loop is table stakes; **trustworthy memory + self-betterment is not.** Every cell is sourced from each project's own docs, repos, and papers (verified 2026-06) — e.g. OpenClaw's memory-pollution finding is from its own [research paper](https://arxiv.org/abs/2603.23064).

| Capability | **agentware** | OpenHands | OpenClaw | Cline | Aider | Goose |
|---|:--:|:--:|:--:|:--:|:--:|:--:|
| Autonomous execution loop | ✅ | ✅ | ✅ | ✅ | ⚠️ partial | ✅ |
| Persistent cross-session memory (built-in) | ✅ | ⚠️ human-authored | ✅ | ❌ convention | ❌ per-session | ✅ |
| **Memory managed by code, not an LLM** | ✅ | ❌ lossy summarizer | ❌ LLM-judge | ❌ | ✅ *(not persistent)* | ❌ |
| **Non-hallucinated** (no LLM authors memory) | ✅ | ⚠️ | ❌ *pollution documented* | ❌ | ✅ | ❌ |
| Self-heal + self-improve (learn→rule) | ✅ | ❌ | ⚠️ gated skills | ❌ | ⚠️ narrow | ⚠️ human-gated |
| Self-extend (writes its own skills) | ✅ | ❌ | ✅ gated | ❌ | ❌ | ⚠️ |
| Benchmark-gated regression ledger | ✅ | ❌ | ❌ | ⚠️ evals | ⚠️ leaderboard | ⚠️ |
| Memory/retrieval needs **no LLM or network** | ✅ | ❌ | ❌ | ❌ | ✅ *(map only)* | ❌ |
| Maturity / adoption (★, approx) | new | ≈78k | ≈380k | ≈64k | ≈47k | ≈50k |

✅ yes/strong · ⚠️ partial/caveated · ❌ no/weak. **The honest takeaway:** every harness has a loop, but **none pairs it with deterministic, persistent, non-LLM-managed memory + a regression gate.** Where they lead, we say so: OpenHands has sandboxed code-exec breadth, Cline has deep IDE integration, and all of them have vastly more adoption.

---

## How agentware stands vs memory layers

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
| Semantic / embedding retrieval | ⚠️ roadmap | ✅ | ✅ | ✅ | ✅ | ✅ |
| Knowledge graph / multi-hop | ⚠️ roadmap | ✅ | ❌ | ✅ | ✅ opt | ✅ |
| Maturity / adoption (★, approx) | new | ≈59k | ≈23k | ≈28k | ≈24k | ≈22k |

**Honest gaps:** agentware trails on **raw semantic recall** (paraphrase queries), **knowledge-graph multi-hop**, and **adoption**. Those are deliberate scope choices today — a **local, rank-only** semantic mode (no LLM, no cloud) is on the roadmap; the deterministic stdlib path stays the zero-install default.

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

# 2. Run your agent runtime (Claude Code) inside it — onboarding auto-starts:
#    it asks where to store your knowledge base, scaffolds it, and personalizes.
claude

# 3. Write a short plan, then fire-and-forget the loop:
./agentware.sh <YYMMDD-feature>
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
<knowledge-dir>/work/<YYMMDD-feature>/plan.md     # you write phases + acceptance criteria
        │
        ▼   ./agentware.sh <feature>
   ┌─────────── PRE ───────────┐  sharpen the plan (≤3 tasks), no scope change
   ├─────────── MAIN ──────────┤  execute one task → verify → recall prior knowledge → worklog
   └─────────── POST ──────────┘  self-assess → assessment.md → promote learnings
```

The toolkit guarantees *how* knowledge is written; the agent decides *what*. Retrieval is byte-identical (no LLM/RNG/network/wall-clock in ranking), proven by guard tests: `test_recall_json_is_byte_identical_across_runs`, `test_cli_imports_are_stdlib_only`, `test_recall_leaves_entire_kb_tree_unchanged` (`tests/test_invariants.py`).

Full walkthrough: [docs/GUIDE.md](docs/GUIDE.md) · plan format: [docs/loop.md](docs/loop.md) · rationale: [docs/methodology.md](docs/methodology.md).

---

## Roadmap

agentware is honest about where it's *not yet* industry-standard:

- **Mode B — local semantic retrieval** *(next)*: BM25 + a **local, rank-only** embedding model fused via RRF (no LLM, no cloud, no data leaving the machine) to close the semantic-recall gap. Mode A stays the byte-identical, zero-install default. *(Code is built behind the `bm25+embed` strategy; it needs a local embedder installed to score.)*
- **Observability dashboard** — live, tail-able loop + benchmark health.
- **Knowledge-graph / multi-hop** retrieval (deterministic).
- **Broader runtime support** (native Windows; more agent CLIs).

**Deliberately *not* on the roadmap (the moat line):** an LLM in the retrieval or write path. That is exactly where determinism and non-hallucination break — and it's our whole point.

---

## Requirements

- **Claude Code** (`claude` CLI) — the native runtime (set `AGENTWARE_CLI=<your-cli>` for others).
- **POSIX shell + `bash` + `jq` + Python 3** — macOS, Linux, or Windows via WSL/Git-Bash.
- Git optional (onboarding offers `git init` + push via `gh`). Node.js ≥ 18 only for optional Playwright UI verification.

---

## Contributing

PRs and issues welcome. agentware governs *itself* the same way it governs your work — changes to its own steering/skills/loop go through a plan, a `!! WARNING !!` self-extension gate, and the `steering lint` + benchmark gates before they ship. Run `python3 -m unittest discover -s tests` and `scripts/agentware audit --with-tests` before opening a PR.

## License

[Apache-2.0](LICENSE) © 2026 Rahul Rana — includes an explicit patent grant; see [`NOTICE`](NOTICE).
