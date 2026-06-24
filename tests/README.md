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
