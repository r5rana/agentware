# Pre-existing failure baselines

> Feature `260813-modular-core-adoption`, task 5.

**Green means "no worse than the recorded baseline", never "all pass."**

Both the destination tree and the donor tree carry pre-existing test and gate
failures that predate this migration. A merge gate that demands an all-green
suite would either block forever or be silently disabled. These files record
what was already red **before** the first package change, per module and per
gate, so every later gate can diff against a real number.

## Files

| File | Tree |
|---|---|
| `baseline-local.json` | this repository, at the commit recorded in `git_head` |
| `baseline-donor.json` | the modularised donor working tree, at its `git_head` |
| `suite-delta-prev.json` | the per-commit delta gate's **before** side (task 28) |
| `suite-delta-head.json` | the per-commit delta gate's **after** side (task 28) |

The first two are produced by `tests/_baseline_capture.py`, the delta pair by
`tests/_suite_delta.py`. Those are the only supported writers. Do not hand-edit
any of them.

## Regenerating

Each capture targets a **scratch copy** of the knowledge base via a scoped
`AGENTWARE_KNOWLEDGE_DIR` prefix, never the live one: the suites and the gates
both read and write knowledge state, and the donor suite is unaudited code. The
capture records a content fingerprint of the knowledge dir before and after and
sets `knowledge_dir_unchanged` accordingly, so a suite that mutates its target
is caught rather than assumed away.

```sh
KB=<durable scratch copy of the knowledge dir>   # .initialized kept, .git removed

AGENTWARE_KNOWLEDGE_DIR="$KB" python3 tests/_baseline_capture.py \
  --root . --label local --out tests/baselines/baseline-local.json

AGENTWARE_KNOWLEDGE_DIR="$KB" python3 tests/_baseline_capture.py \
  --root <donor tree> --label donor --out tests/baselines/baseline-donor.json
```

Give each side its own scratch copy when running the two captures
concurrently — the gates write baseline state into the knowledge dir, so a
shared target races.

### Why module runs carry `AGENTWARE_NESTED_UNITTEST=1`

Several modules exercise verbs that themselves shell `unittest discover -s
tests`. The package guards that with `AGENTWARE_NESTED_UNITTEST=1`, which the
`audit` verb sets before shelling the suite — but a module run one-per-
subprocess does not inherit it, so each such module silently re-runs the
**entire** suite inside its own row. That is quadratic wall time and, for any
module whose nested suite outlives the per-module budget, a false `timeout`
red: `tests.test_acr_gate` recorded `timeout` at 900 s unguarded and `ok` in
0.3 s guarded. The capture therefore applies the guard to **module** runs and
withholds it from **gate** runs, so each gate row still reflects what an
operator invoking that gate would see.

Fixture data under `tests/fixtures/` is pruned from discovery: it is never
collected by `unittest discover -s tests`, and the SWE task fixtures sit under
hyphenated directory names that no dotted-module run could resolve.

## What a row means

Each entry in `modules[]` is one test module, run as its own
`python3 -m unittest <dotted>` subprocess so that a crash or a hang is confined
to one row:

- `status` — `ok`, `failed`, `crash` (interpreter died before the runner
  reported) or `timeout`;
- `ran` / `passed` / `failures` / `errors` / `skipped` and the two
  expected-failure counters, parsed from the runner summary;
- `output_tail` — present only on a non-`ok` row.

Each entry in `gates[]` is one package gate — `steering lint`, `audit` and
`gate release` — with its exit code, and, where the verb speaks JSON, its
per-check breakdown plus a sorted `failing` list.

`totals` aggregates the module rows; `modules_not_ok` is the headline number a
ratchet should compare against.

## How later gates consume this

- A module may not regress from `ok` to any other status.
- A module's `failures + errors` may not rise.
- A gate's `failing` set may not gain a member.
- A **fall** in any of those is expected and welcome; a fall is what porting
  work looks like. Ratchets built on these files must be one-directional.
- A rise in `passed` with no new test node id is a defect, not progress — see
  the per-commit delta gate below, which reads these same rows.

## The per-commit delta gate (task 28)

`tests/_suite_delta.py` captures the module rows **and** the collected node ids
in one pass, at one commit, and `tests/test_suite_delta.py` diffs the recorded
pair. Capturing both halves together is the point: the counts and the ids can
never be compared across a skew, so "this module gained two passes" is always
answerable with "and here are the two node ids that arrived".

The rules, per module:

A pass count may rise by exactly what **new node ids** and **repaired outcomes**
account for, and no more:

| Shape | Verdict |
|---|---|
| `passed` rose by more than (new node ids + non-passing outcomes that became passes) | **violation** — a rise nobody added a test for and nothing was fixed for |
| `passed` fell by more than (removed node ids + new non-passing outcomes) | **violation** — passes went somewhere unrecorded |
| `failures + errors` rose | **violation** |
| `skipped` rose | **violation** — skipping is the cheapest way to make a suite greener |
| `ran` or `passed` exceeds the module's collected node ids | **violation** — `COUNTS EXCEED COLLECTION`, which is what makes a silently dropped node id fail |
| `status` left `ok` | **violation** |
| module gone, every node id collected elsewhere | note: `MOVED` (task 58 relocates the corpus) |
| module gone, node ids collected nowhere | **violation** — `LOST MODULE` |
| new module, every pass covered by a genuinely new id | note: `ADDED` |
| red → green, or skipped → run | note: `REPAIRED`, and it buys exactly that many passes |

What the gate does **not** see: a test whose assertions were gutted also goes
red → green and reads as a repair. This closes the *arithmetic*; the body differ
(task 30) and review close the meaning.

Capture both sides **in the same checkout**. Several suites resolve fixtures
relative to the repo root, so a `git worktree` at another path skips or fails
tests for reasons that have nothing to do with the commit — three modules
diverged that way while this gate was being seeded. A pair whose `tree_root`s
differ is labelled `CHECKOUT SKEW` in the report rather than quietly trusted.

Re-stamping is two commands; run them per commit, always with a scoped
`AGENTWARE_KNOWLEDGE_DIR` so the live knowledge base is never the target:

```sh
KB=<durable scratch copy of the knowledge dir>

# the shipped head capture becomes the new before side
cp tests/baselines/suite-delta-head.json tests/baselines/suite-delta-prev.json

AGENTWARE_KNOWLEDGE_DIR="$KB" python3 tests/_suite_delta.py capture \
  --root . --label head --out tests/baselines/suite-delta-head.json

python3 tests/_suite_delta.py check \
  --before tests/baselines/suite-delta-prev.json \
  --after  tests/baselines/suite-delta-head.json --verbose
```

A capture that adds the delta gate's OWN module has a self-reference: the first
capture measures `tests.test_suite_delta` before the artifact it reads exists.
Capture twice on that one commit and ship the second, so the next commit's
delta sees the module in the state it actually ships in.
