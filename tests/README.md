# agentware test suite

Hermetic, stdlib-only (`unittest`) tests for the `scripts/agentware` toolkit.
**No pytest, no third-party dependencies** — pure Python standard library so the
package keeps zero hard dependencies (INV-6).

## Running

```sh
python3 -m unittest discover -s tests -v        # whole suite
python3 -m unittest tests.test_existing_cli -v  # one module
```

Both invocation styles work; `tests/` is a package (`__init__.py`) and each test
module imports fixtures with a dual `tests._fixtures` / `_fixtures` fallback.

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
