# agentware test suite

Hermetic, stdlib-only (`unittest`) tests for the `scripts/agentware` toolkit.
**No pytest, no third-party dependencies** — pure Python standard library so the
package keeps zero hard dependencies (INV-6).

## Layout (task 58)

The corpus is layered; there are no flat test files under `tests/`.

| Tier | Holds |
|---|---|
| `tests/unit/<layer>/` | one module's behaviour: an `agentware_lib/<layer>/` module (`cli`, `core`, `features`, `knowledge`, `ops`, `retrieval`) or a `scripts/agentware_dashboard/` module (`dashboard`, task 69) |
| `tests/integration/` | two or more layers, or a seam the lib does not own — the orchestrator, the hooks, the dashboard, the MCP server |
| `tests/e2e/` | a whole user-visible path: a full loop, a fresh clone, a benchmark run |
| `tests/gates/` | the migration and repo-structure gates themselves, whose subject is a `tests/_*.py` harness module or a tree-wide invariant |

Rules the suite enforces on itself (`tests/gates/test_test_layout.py`):

- no test file directly under `tests/`, and none outside a tier;
- **no test file over 600 lines** — split along class boundaries, lifting the
  shared header into a sibling `_<name>_support.py`;
- a test file that MOVES is recorded in `tests/_test_layout.py`'s
  `RELOCATIONS`, because a node id embeds its module path and the
  preserved-surface freeze reads a moved id as a lost one otherwise.
- **every `agentware_lib` module is owned by `tests/unit/<layer>/test_<stem>.py`**
  and by nothing else (`tests/gates/test_test_ownership.py`). A new module
  without that file fails the gate; the pre-existing residual is declared in
  `tests/fixtures/test-ownership-debt.json` and may only shrink. Run
  `python3 tests/_test_ownership.py --report` for the map, or `--references`
  for the triage list of same-layer files already exercising a testless module.

**Never rename a test method or class to fix a name that has gone stale.** The
freeze cannot tell a rename from a deletion, and it is right not to: narrow what
the method asserts and say so in its docstring instead.

Helper modules (`tests/_*.py`) stay at the corpus root. Layered files find the
repo through `tests/_paths.py` (`REPO_ROOT`, `TESTS_DIR`) rather than counting
`os.path.dirname` hops, which is depth-dependent and fails silently.

## Running

```sh
# WHOLE SUITE — four tier-scoped runs, in the tiers' own runner. This is the
# supported path: the package has zero hard dependencies (INV-6), so pytest is
# NOT installed on a stock clone and `python3 -m pytest` fails with
# "No module named pytest" before collecting anything.
for t in unit gates integration e2e; do
    python3 -m unittest discover -s tests/$t -t . || echo "TIER $t RED"
done

python3 -m unittest tests.integration.test_existing_cli -v      # one module
python3 -m unittest discover -s tests/unit/retrieval -t . -v    # one tier
```

Tier-scoped `discover` needs no `--ignore` flags: `tests/fixtures/` holds fixture
DATA (including SWE task repos with their own `test_hidden.py` graders, meant to
be run BY a test and never collected as one) and sits under no tier, so the four
runs above cover the whole corpus and collect none of it. If you DO have pytest
installed, `python3 -m pytest tests/ -q -p no:cacheprovider --ignore=tests/fixtures`
works too — but treat it as a convenience, not the contract. Drop the
`--ignore=tests/test_eval_gold.py` that older instructions carry: task 58 layered
the corpus and no file sits at that path any more, so the flag ignores nothing
and reads as coverage that is not happening.

> **Do not run `unittest discover -s tests` for the whole suite.** It collects
> `tests/fixtures/`, and doing so INTERMITTENTLY HANGS — observed three times,
> each stalling for 40+ minutes at 0.0% CPU with a static log, twice wedging an
> autonomous loop iteration that was waiting on it. A run that hangs rather than
> fails is the worst shape a verification can take: it cannot be distinguished
> from a slow one without checking CPU (`ps -p PID -o pcpu=,stat=`), and any wait
> that polls only for a SUCCESS marker never returns. Tier-scoped `discover` is
> fine; it is the whole-tree collection that is not.

> **Restoring a mutated source file is not restoring the tree.** Any harness that
> edits a module, runs a test, and writes the original text back — the
> non-vacuity sweeps every paydown wave runs — must ALSO delete that module's
> `__pycache__/*.pyc` and bump its mtime. CPython validates a cached `.pyc`
> against the source's (mtime-seconds, size), never its contents, so a mutation
> that preserves the BYTE LENGTH and is restored inside the same second leaves
> bytecode compiled from the MUTANT that every later interpreter accepts as
> current. Measured at task 99 wave 4: `==` → `!=` plus `✅` → `⬜` (both 3-byte
> characters) left `tests.unit.ops.test_derive` failing on a clean tree for two
> full suite runs. The dangerous direction is the quiet one — a leftover mutant
> reddens the NEXT row's run, so the sweep reports a property GUARDED that
> nothing guards. Run mutant subprocesses with `PYTHONDONTWRITEBYTECODE=1` too.

> **A test can only distinguish alternatives its fixture separates.** Two shapes
> of vacuity, both found by the wave-15 sweep in tests written for that wave.
> (1) An ORDER claim whose branches coincidentally agree proves nothing: the
> `query` precedence test drove `--depends-on a` and `--impact d` over a diamond
> where forward-from-`a` and reverse-from-`d` are the SAME pair, so permuting the
> dispatch tuple stayed green. Pick inputs whose alternatives are provably
> different, and assert each in turn. (2) `assertIn` on a TRUNCATED value also
> passes against the untruncated one, because the short form is a prefix — the
> `--check` test asserted a 9-character sha and the full 16-character one
> satisfied it. Pin the line whole (include the delimiter) AND assert the
> untruncated form is absent. Both generalise: whenever the code derives a value
> by shortening or by choosing among alternatives, ask whether the OTHER value
> also satisfies the assertion.

> **A probe that anchors on an INDENTED line goes stale when code is extracted.**
> A curated parity row's mutation (`tests/_parity_mutation_rows.py`) matches a
> literal source string. If that literal carries the line's indentation, any
> refactor that lifts the line into a helper dedents it and the probe stops
> matching — the row then reports `STALE-PROBE`, and the capability it claims is
> unproven while the summary still reads PASS. Measured at task 99 wave 9:
> `sec:transcript-redaction-on-the-commit-path` had matched nothing since wave 2
> dedented its target. Anchor on the narrowest text unique in the file, and run
> the FULL sweep (`python3 tests/_parity_sweep.py --all`) after any wave that
> MOVES code — the schema-only `--check` cannot see it.

> **A wave that MOVES a guarded line leaves a stale PIN behind, and the stale
> pin reads as a regression.** Three ratchets anchor on source TEXT rather than
> on a symbol — `tests/_parity_mutation_rows.py` (the curated mutation probes),
> `tests/_indexreader_gate.py` (the raw-read sites) and
> `tests/_preserved_pins.py` (the collision table) — so re-indenting a guard or
> extracting it into a helper makes the anchor miss. `_parity_sweep --all`
> reports it as `STALE-PROBE`, which is the honest wording: the probe is stale,
> not the capability. Measured three waves running (10, 11 and 12). THE RULE:
> after any wave that moves a guarded line, grep those three files for the
> MUTATED TEXT, not only for the module path — a path grep finds nothing when
> the code moved WITHIN its file. And the schema-only `--check` cannot see it;
> only the full sweep can.
>
> **Do not spend `_parity_mutation_rows.py`'s remaining lines on comments.**
> That file sits deliberately BELOW its 600-line `tests/**` cap by the exact
> size of one curated row, because `tests/e2e/test_add_a_feature_walkthrough`
> APPENDS a row to it in a scratch copy and then runs the ceiling gate there —
> so a two-line comment added at 594 lines is green locally and reddens the
> walkthrough at 602. Explanations for a row belong here or in the worklog; the
> file itself carries data.

> **Deleting `AGENTWARE_KNOWLEDGE_DIR` does not un-configure the knowledge dir.**
> The resolver's precedence is env → `~/.agentware/config.env` → default, and
> `HOME_CONFIG` is bound at import time, so a test that POPS the variable to
> reach a "no knowledge dir" arm falls through to the OPERATOR'S machine config
> — which on a real machine resolves, so the arm never runs and the assertion
> reads whatever the live KB produced. Point the variable at a path that does
> NOT EXIST instead: `require_knowledge_dir` then refuses with a distinct
> "does not exist" message a test can assert on. Measured at task 99 wave 11,
> where a `skill sync` precedence test ran `--reset` and `--approve` against the
> operator's real knowledge dir and reported rc 0 for it. The same rule applies
> to any helper that resolves the KB from the ENVIRONMENT rather than from an
> argument it was handed — `cmd_plan_new` does, which is why an evidence driver
> calling `_skill_promote_run(kdir, ...)` must ALSO set the env var or every
> emit returns `blocked: emitter refused (rc=1)`.

> **`eval --record --gate` under `AGENTWARE_NESTED_UNITTEST=1` records a
> reliability score that is NOT comparable to any other row.** The guard exists
> so a verb that shells `unittest discover` inside a suite run does not recurse,
> and it makes the verb record `test_pass_rate: null` — which drops the
> pass-rate component out of the composite. Measured at task 99 wave 11: the
> same commit, the same corpus and byte-identical retrieval metrics scored
> **68.37** under the guard and **77.86** without it, a 9.5-point drop that reads
> exactly like a diff-caused regression. Run the R-PKG-05 gate WITHOUT the guard,
> and read `checks.test_pass_rate` before believing any reliability delta.

`tests/` and every tier are packages (`__init__.py`), and each test module
imports fixtures with a dual `tests._fixtures` / `_fixtures` fallback.

The package consistency pass also runs the suite:

```sh
scripts/agentware audit --with-tests
```

## How it works

- `tests/_fixtures.py` builds a **synthetic temp KB** (`tempfile.mkdtemp`) with a
  handful of `learnings/*.md`, `configurations/*.md`, `references/*.md` files and
  a consistent `index.json`. Tests drive the real CLI against it by setting
  `AGENTWARE_KNOWLEDGE_DIR` to the temp dir — so tests **never touch the
  operator's real knowledge base**.
- Tests must **never** call `agentware init`: that writes `~/.agentware/config.env`
  and would clobber the operator's real knowledge-dir pointer. The synthetic
  `index.json` is built directly instead.
- The CLI script is imported as a module via `importlib` (it has no `.py`
  extension) and invoked through `main(argv)`, capturing stdout/stderr and exit
  code.

## Gates that guard the suite itself (feature 260813)

Nine of the gates here do not test the toolkit — they test whether the suite is
still *measuring* the toolkit. Every one exists because a green suite and an
absent suite look identical from the outside.

| Gate | Question it answers |
|---|---|
| `test_preserved_gates.py` | READ seam: does every CLI symbol the tests reference still resolve, and are the frozen node ids still collected? |
| `test_mutation_canary.py` | WRITE seam: does rebinding a symbol on the loaded CLI module still change what the dispatched command prints? `hasattr` cannot tell — it is satisfied by a photograph — so the canary patches, dispatches, and demands the output change. It is proven to bite against a facade whose passthrough is removed and one whose passthrough is an `import` + `globals().update(...)` namespace copy. |
| `test_suite_delta.py` | Per-commit accounting: did any module's pass count rise without a new test node id? A rise nobody added a test for is a test that stopped exercising something. See `tests/baselines/README.md` for the re-stamp workflow. |
| `test_parser_golden.py` | PROSE seam: the surface gates are deliberately help-blind (`_surface_capture.py` records `required`/`nargs`/`choices`/`default` and never reads `help=`), so every rendered `--help` string was unpinned. This is a byte golden over all 125 surfaces plus the top-level display-order pin. |
| `test_neutrality_redact.py` | WRITE seam: does a generator that COMMITS captured output neutralise it first, through the same needle set the portability gate grades the tree by? The gate is a scan, and a scan can only report a leak once it is committed — three frozen captures (a donor test-run baseline and two node-id freezes) carried a vendor token for two waves because a capture taken at a pinned head cannot be regenerated to remove it. |
| `test_portability_store.py` | PUBLISHABILITY seam: did the object store gain only locally authored commits? The commit-RANGE surface walks reachability from the branch and stops; a store keeps every abandoned rewrite and fetched tip, and `git push` publishes by REF. It is how task 82's pre-rewrite identity was found still sitting on a `refs/heads/` backup after the branch itself was proven clean. |
| `test_parity_sweep.py` | PROOF seam: does each parity row's stated `mutation` actually redden the test it names? Until task 79 the only thing enforced about that field was `len(...) > 20`, so seventy-eight capability claims rested on sentences nobody had run. The sweep applies each probe in a throwaway `git clone` and demands a red, distinguishing a no-op probe (`STALE-PROBE`) from an unguarded capability (`NOT-REDDENED`) from a mutation that merely breaks an import (`LOAD-ERROR`). Its first run found eight rows whose named test could not observe the capability at all, one capability nothing guarded, and one assertion satisfied by a docstring. |
| `test_solo_parity.py` | BEHAVIOUR seam: with team mode UNSET, does a fresh clone of the merged tree still behave like `aw-pre-merge`? Every other migration gate compares a STRUCTURE — an object dump, a capability row, a normalised function body — so none of them ever runs the two CLIs and reads what an operator would see. This one clones both, walks every parser node for lost actions and rewritten `help=`/`metavar=` prose, and runs a 31-invocation read-only session against byte-identical scratch knowledge bases, comparing exit code, stdout and stderr. Surviving differences are declared in `tests/fixtures/solo-parity.json` with a reason, and the ledger bites both ways: an undeclared difference fails, and so does a row whose difference has gone. Its first run found `config`'s report silently missing its `skill_promote` line and eleven `help=` strings replaced by thinner text. |
| `test_test_ownership.py` | OWNERSHIP seam: when a module breaks, is there a test file whose SUBJECT is that module? A module is owned by exactly one file, `tests/unit/<layer>/test_<stem>.py`; a test elsewhere that merely references it owns nothing. Task 71 ported the donor's M11 without its carve-out (which let any flat white-box `tests/test_*.py` referencing an export claim ownership — and a file reaching through the facade references them all). The residual is declared in `tests/fixtures/test-ownership-debt.json` and shrinks only. |

### Regenerating the `--help` golden corpus

`tests/fixtures/help_golden/` holds one `.txt` per parser surface, plus a
`_meta.json` recording the interpreter and `COLUMNS` that produced them.
Rendered help is **not** portable across interpreters (argparse changed its
section header and its "option that takes a value" rendering) or terminal
widths (88 of the 125 surfaces re-wrap), so the corpus is captured once, with
`COLUMNS=80` pinned, and the byte comparison is scoped to the recorded
interpreter — the structural pins run everywhere. Adding a verb, a sub-verb or
a `help=` string is a genuine `--help` change:

```sh
python3 tests/_help_golden.py --write     # regenerate, pruning stale surfaces
python3 tests/_help_golden.py --verify    # byte-diff without writing
```

A new top-level verb additionally needs `EXPECTED_VERBS` and
`EXPECTED_SURFACES` updated in `tests/gates/test_parser_golden.py` — that
coupling is deliberate, so a verb cannot appear without someone noticing.

## Transitional gates for the strangler migration (feature 260813 task 32)

Three gates exist only while the tree has two halves — the modular
`agentware_lib` and the preserved monolith — and they answer the three questions
legacy-default routing (task 29) makes possible to get wrong. Each is runnable
on its own as well as through its suite.

| Gate | Question it answers | Run it |
|---|---|---|
| `test_legacy_ratchet.py` | Is `scripts/agentware_legacy.py` still only ever getting SMALLER, and did every deleted handler leave as half of a PORT rather than into thin air? Ceilings are `legacy_lines` and `legacy_bytes` (whitespace-insensitive, so reflowing or relocating code into a class is not a shrink); `legacy_defs` and `legacy_handlers` are NAME SETS that may only lose members; `strangler.PORTED_VERBS` is a grow-only floor. `--record` refuses to loosen any of them and enforces the coupling PER HANDLER — `cmd_<verb>[_<sub>]` must name a verb appended in the same commit, and that verb must itself be in the union fixture. There is no flag to skip it. | `python3 tests/_legacy_ratchet.py --strict` · re-record after a port with `--record` |
| `test_promotion_gate.py` | May this verb join the allowlist — does its surface cover the BINDING UNION on `kind`, `required`, `nargs`, `default`, `choices`, `type` and the subparsers `dest`, not on flag names? Two arms: a verb served by the lib (ported or donor-only) is compared against the union, an unported one against `surface-local.json` (what `aw-pre-merge` served), reading the parser the entrypoint really builds rather than the file on disk. | `python3 tests/_promotion_gate.py` · `--verb <v> --readiness` before an append |
| `test_presence_gate.py` | Are the six donor-only verbs (`fleet`, `fleet-emit`, `org`, `team`, `templates`, `work`) present, complete, non-STUB and ROUTABLE in the modular tree? Nothing else can ask: legacy has no handler for them, so a stub satisfies every surface comparison, and being registered is not the same as being reachable — a verb absent from `PORTED_VERBS` is reported as pending, never as passed. | `python3 tests/_presence_gate.py` |

The ratchet and the promotion gate's unported arm retire with the passthrough at
task 56 — both treat the monolith's absence as a terminal state rather than an
error. The presence gate does not retire: those six verbs must still be there,
and reachable, long after the monolith is gone.

## Test-naming convention (avoid `-k` collisions)

`python3 -m unittest ... -k <token>` matches the token as a substring of the test
**module filename** AND every **method name**, across the whole suite. So a test
prefix must be **unique**: it must not be a substring of any test-module filename
or of any method in another suite. Concretely, a `-k test_migrate` batch collects
`test_migrate_runner.py` *and* any stray `test_migrate_*` method elsewhere; a
`-k test_drift` batch collects `test_drift_dream.py` *and* any stray
`test_drift_*`. Keep each suite's methods under its own unambiguous prefix (e.g.
`test_g1harden_`, `test_gatebaseline_`, `test_schemaguard_`, `test_donestamp_`,
`test_attachmigrate_`, `test_evalpin_`) — verified unique against every existing
module filename and method before adoption.
