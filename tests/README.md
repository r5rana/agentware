# agentware test suite

Hermetic, stdlib-only (`unittest`) tests for the `scripts/agentware` toolkit.
**No pytest, no third-party dependencies** — pure Python standard library so the
package keeps zero hard dependencies (INV-6).

## Layout (task 58)

The corpus is layered; there are no flat test files under `tests/`.

| Tier | Holds |
|---|---|
| `tests/unit/<layer>/` | one `agentware_lib/<layer>/` module's behaviour (`cli`, `core`, `features`, `knowledge`, `ops`, `retrieval`) |
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

**Never rename a test method or class to fix a name that has gone stale.** The
freeze cannot tell a rename from a deletion, and it is right not to: narrow what
the method asserts and say so in its docstring instead.

Helper modules (`tests/_*.py`) stay at the corpus root. Layered files find the
repo through `tests/_paths.py` (`REPO_ROOT`, `TESTS_DIR`) rather than counting
`os.path.dirname` hops, which is depth-dependent and fails silently.

## Running

```sh
python3 -m unittest discover -s tests -v                       # whole suite
python3 -m unittest tests.integration.test_existing_cli -v      # one module
python3 -m unittest discover -s tests/unit/retrieval -t . -v    # one tier
```

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

Three of the gates here do not test the toolkit — they test whether the suite is
still *measuring* the toolkit. Every one exists because a green suite and an
absent suite look identical from the outside.

| Gate | Question it answers |
|---|---|
| `test_preserved_gates.py` | READ seam: does every CLI symbol the tests reference still resolve, and are the frozen node ids still collected? |
| `test_mutation_canary.py` | WRITE seam: does rebinding a symbol on the loaded CLI module still change what the dispatched command prints? `hasattr` cannot tell — it is satisfied by a photograph — so the canary patches, dispatches, and demands the output change. It is proven to bite against a facade whose passthrough is removed and one whose passthrough is an `import` + `globals().update(...)` namespace copy. |
| `test_suite_delta.py` | Per-commit accounting: did any module's pass count rise without a new test node id? A rise nobody added a test for is a test that stopped exercising something. See `tests/baselines/README.md` for the re-stamp workflow. |

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
