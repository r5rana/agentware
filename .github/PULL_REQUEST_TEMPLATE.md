<!--
Thanks for the change. CONTRIBUTING.md has the full contract; this template is
the short version a reviewer reads first.

Delete any section that does not apply — an empty heading is noise, but a
deleted one is a decision.
-->

## What this changes

<!-- One paragraph. The diff says what; say WHY. -->

Closes #

## How it is proven

<!-- Name the test that reddens if this change is reverted or broken. "The
suite passes" is not an answer: which test, and what does it assert? -->

- Test:
- Mutation that reddens it:

## Checks

Everything below runs locally in seconds. Tick what you ran; do not tick what
you did not.

- [ ] Targeted tests for the code I touched pass
      (`python3 -m unittest tests.unit.<layer>.test_<module>`)
- [ ] The four tiers are no worse than `tests/baselines/`
      (`for t in unit gates integration e2e; do python3 -m unittest discover -s tests/$t -t .; done`)
- [ ] The structural checkers are green (`python3 tests/_ceiling_gate.py`,
      `tests/_modularity_rules.py`, `tests/_test_ownership.py`,
      `tests/_manifest_gen.py --check`, `tests/_parity_manifest.py --check`)
- [ ] `scripts/agentware scaffold check` passes
- [ ] Generated files were REGENERATED, not hand-edited
- [ ] `scripts/agentware steering lint --strict` passes *(only if `AGENTS.md` or
      `steering/` changed)*

## Contract

- [ ] The unit landed **whole**: module, verb declaration, manifest entry,
      mirrored owning test, parity row (`R-MOD-11`) — or this change adds no unit
- [ ] No upward cross-layer import, and no file or function over its ceiling
- [ ] **No personal data**: no handle, email, home path or knowledge-base
      content in any tracked file (`scripts/agentware audit` → `personal_data`)
- [ ] No new third-party dependency for the Python package
- [ ] Documentation that names a path, a symbol or a gate still resolves

## Notes for the reviewer

<!-- Anything you are unsure about, a decision you made and its alternatives, or
a follow-up you deliberately did not fold in. -->
