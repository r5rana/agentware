# Contributing to agentware

Thanks for being here. agentware is a clone-and-go steering framework: this
repository holds **only generic steering, code and tests** — the operator's
knowledge base lives in an external directory chosen at onboarding, and nothing
personal is ever committed here.

That single sentence is also the first rule you have to keep.

## Contents

- [Ground rules](#ground-rules)
- [Getting set up](#getting-set-up)
- [Where code goes — the modular contract](#where-code-goes--the-modular-contract)
- [Running the checks](#running-the-checks)
- [Commits and pull requests](#commits-and-pull-requests)
- [Reporting bugs and requesting features](#reporting-bugs-and-requesting-features)
- [Security](#security)
- [Licensing of contributions](#licensing-of-contributions)

## Ground rules

1. **Zero hard dependencies.** The toolkit is Python standard library only, and
   stays that way. If your change needs a third-party package, it is the wrong
   change — say so in an issue before writing it. (The optional web dashboard
   under `webui/` has its own `npm` dependency graph; the Python package does
   not depend on it, and the dashboard serves without it.)
2. **No personal data, ever.** No handles, emails, home paths, hostnames or
   operator-specific knowledge in tracked files (`R-LOC-03`). The check that
   enforces this is `scripts/agentware audit`'s `personal_data` row, and it
   resolves the live identity at runtime — so a sample handle you borrow from
   your own machine *will* be caught. Use a neutral placeholder.
3. **Everything is verified by something that can fail.** A claim in a document,
   a rule in `AGENTS.md`, a capability in a module — each has a gate that
   reddens when it stops being true. Adding a claim means adding or extending
   the gate that proves it.
4. **Derived files are never hand-edited.** `catalog/modules.json`,
   `tests/fixtures/parity-manifest.json`, the module router tables and the
   ceiling tables are generated. Re-run the generator; do not patch the output.
5. **The package governs itself the way it governs your work.** A change to the
   steering, the skills or the loop goes through a plan, the self-extension
   warning gate, and the `steering lint` + benchmark gates before it ships
   (`R-PKG-01..06` in [`AGENTS.md`](AGENTS.md)).

## Getting set up

You need Python 3 and git. There is nothing to install.

```bash
git clone https://github.com/r5rana/agentware.git
cd agentware
scripts/agentware --help
```

The first run of an agent session resolves (or asks for) the external knowledge
directory; see the onboarding skill in
[`.claude/skills/onboarding/SKILL.md`](.claude/skills/onboarding/SKILL.md). You
do **not** need a knowledge base to work on the code or run the suite — the
tests are hermetic and build their own scratch directories.

## Where code goes — the modular contract

`scripts/agentware` is a thin entrypoint. All domain code lives in the layered
package [`scripts/agentware_lib/`](scripts/agentware_lib/README.md), whose
layers import strictly downward:

```text
core → knowledge → retrieval → ops → features → cli
```

An upward module-level import is a **test failure**, not a style note. Files
carry line ceilings (entrypoint 120, procedural 250, module 600) and functions
carry their own (120 lines, 12 branches). When a file approaches its ceiling you
split it where the *obligation* changes — you never raise the cap.

A new unit lands **whole, in one change**: the module, its verb declaration, its
manifest entry, its mirrored owning test at `tests/unit/<layer>/test_<stem>.py`,
and its parity row. The scaffold writes the hand-maintained half for you:

```bash
scripts/agentware scaffold new --kind verb --layer ops --name greet --verb greet --task 12
scripts/agentware scaffold check
```

`--layer` is a decision, not a formality, and `--task` is the plan task or issue
number the unit lands under — the parity row refuses to exist without one.

Read these three, in this order, before moving code:

| Document | What it answers |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | the module map, the layer contract, the ceilings, and how the gates enforce them |
| [`docs/add-a-feature.md`](docs/add-a-feature.md) | the worked walkthrough: a verb from nothing to green, in the commands you actually type |
| [`.claude/skills/modular-development/SKILL.md`](.claude/skills/modular-development/SKILL.md) | the same procedure written for an agent, if you are driving one |

The rules themselves (`R-MOD-01..12`) are in [`AGENTS.md`](AGENTS.md). This
section deliberately does not restate them: a second copy of the module map is a
second thing to drift.

## Running the checks

**Everything CI runs, in one command:**

```bash
python3 tests/_ci_gate.py
```

That is the *same* entrypoint [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
calls — the workflow contains no gate commands of its own, so a red in CI is
reproduced locally by running that line, and a gate cannot be added to one side
without the other. `--list` prints the stage table with the reason each stage
exists; `--stage <id>` runs one; `--fail-fast` stops at the first red.

**Just cloned?** One command takes a fresh clone to a verified working state —
no install step, no network:

```bash
python3 tests/_ci_gate.py --profile bootstrap
```

It runs the structural checkers plus a hermetic `init` → `index validate` →
`recall` → `audit` sequence against a throwaway knowledge dir, so it never
touches your real one. `tests/e2e/test_ci_bootstrap.py` proves that same command
works from a genuinely empty directory.

**Targeted first.** Never run a broad suite when a specific test will do:

```bash
python3 -m unittest tests.unit.ops.test_audit
```

**The whole suite is four tier-scoped runs** — never `discover` over the whole
tree, which collects `tests/fixtures/` and intermittently hangs
([`tests/README.md`](tests/README.md) explains why):

```bash
for t in unit gates integration e2e; do
    python3 -m unittest discover -s tests/$t -t . || echo "TIER $t RED"
done
```

Green means *no worse than the recorded baseline*, not *zero failures* — read
`tests/baselines/` for what is already known-red.

**The structural checkers** run in a second each and catch most review comments
before a reviewer has to write them. Each prints a verdict and exits non-zero on
a finding — `python3 tests/_ci_gate.py --profile bootstrap` runs all six:

```bash
python3 tests/_ceiling_gate.py            # file and per-function ceilings
python3 tests/_modularity_rules.py        # layer direction, module naming
python3 tests/_test_ownership.py          # every module has an owning unit file
python3 tests/_manifest_gen.py --check    # catalog/modules.json matches the tree
python3 tests/_parity_manifest.py --check # every capability carries a row
python3 tests/_parity_sweep.py --check    # every row's mutation probe is runnable
```

**If you added or changed a curated parity row**, prove its mutation actually
reddens the test it names. The row is a claim; the probe is what refutes it:

```bash
python3 tests/_parity_sweep.py --row <row-id>   # PASS is the only good answer
python3 tests/_parity_sweep.py --all --jobs 4   # the whole ledger, ~5 minutes
```

`NOT-REDDENED` means the capability is unguarded and the row is a promise
nothing keeps — fix the gap. Do not reach for a different mutation that happens
to redden something else.

**If you touched `AGENTS.md` or `steering/`**, the steering linter is mandatory:

```bash
scripts/agentware steering lint --strict
```

The full finish-line list — including the retrieval no-regression and release
gates that close a package edit — is
[§5 of the walkthrough](docs/add-a-feature.md#5-the-finish-line). Section 4 of
that document is a troubleshooting table for **every gate that can reject your
change**: what its message means and how to clear it.

## Commits and pull requests

- **One logical change per commit.** A commit that lands a module and also
  reformats an unrelated file is a commit nobody can review or revert.
- **Conventional Commits** for the subject line: `feat:`, `fix:`, `docs:`,
  `test:`, `refactor:`, `perf:`, `build:`, `ci:`, `chore:`. Keep it under ~72
  characters and write it in the imperative.
- **The body says why**, not what — the diff already says what.
- **These two are a gate, not a style note.** `python3 tests/_commit_series.py`
  reads the whole branch range and fails on a non-conventional or junk subject,
  on a second author identity (in the author field, the *committer* field, or a
  `Co-Authored-By:` / `Generated with` trailer), on a date outside the declared
  window, and on a merge commit or a range that got shorter. Bounds live in
  `tests/fixtures/commit-series.json`; the expected identity is *anchored* to
  the author of the commit the branch was cut from and is never written down,
  so a stray identity has to be removed from history rather than declared away.
- **Open the PR against `main`** and fill in the template. Every checkbox in it
  names a check you can run locally in seconds; ticking one you did not run
  wastes a review round.
- **Do not commit generated artifacts by hand** (see ground rule 4) and do not
  commit anything from your knowledge directory.

A maintainer reviews for: the gate that proves the change, the layer the code
landed in, and whether the unit is whole. Those three are most of the review.

## Reporting bugs and requesting features

Open an issue using one of the templates in
[`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE). A bug report that includes
the exact command, the full output and `scripts/agentware audit` gets diagnosed;
one that does not usually gets a request for those three.

Blank issues are disabled on purpose — the templates ask for the fields that
decide whether a report is actionable.

## Security

**Do not open a public issue for a security problem.** The reporting process,
the supported versions and the scope are in [`SECURITY.md`](SECURITY.md).

## Licensing of contributions

agentware is licensed under [Apache-2.0](LICENSE). By submitting a contribution
you agree that it is licensed under the same terms (inbound = outbound), and
that you have the right to submit it. Keep the `LICENSE` and
[`NOTICE`](NOTICE) files intact; if your change vendors third-party content,
add its attribution to `NOTICE` in the same commit.

Participation is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).
