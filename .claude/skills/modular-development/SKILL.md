---
name: modular-development
description: >-
  Add, move, split or delete code in a layered package without growing a
  monolith. When asked to "add a verb", "add a subcommand", "add a module",
  "where does this code go", "which layer does this belong in", "this file is
  too long", "split this file", "the module manifest check failed", "the
  modularity gate rejected my change", "the ceiling gate failed", "this module
  has no owning test", or before creating, moving or deleting ANY file under a
  layered source tree, run a four-step procedure: triage what you are adding,
  resolve its layer from the import direction, land the unit WHOLE (module +
  registration + manifest entry + mirrored owning test + parity row), and clear
  every ratchet before claiming done. Operationalizes the agentware R-MOD-01..12
  anti-monolith rules, whose always-on text states the rule but not the
  procedure. Self-contained and workspace-scoped (uses the repo's own
  generators and gates, no new deps, no network); portable to any codebase with
  a generated module manifest and shrink-only debt ledgers.
---

# Modular Development — extending a layered package without rebuilding the monolith

> **When to invoke**: before creating, moving, renaming or deleting any file
> under `scripts/agentware_lib/`, `scripts/loop/`, `scripts/agentware_dashboard/`
> or `webui/src/`; when any gate in §5 rejects a change; or when you cannot say
> in ONE sentence which module owns the code you are about to write.

## Why this skill exists

`AGENTS.md` carries `R-MOD-01`..`R-MOD-12` always-on, because a rule that is not
in context is not a rule. But a rule states an *obligation*, not a *procedure* —
"land the unit whole" does not tell you the five artifacts or the order to write
them in, and an agent that has to infer them re-derives a different answer every
session. That inference is where monoliths come back: not from a decision to
build one, but from a hundred small "this is close enough to that file" moves.

So the obligations stay always-on and the procedure lives here, loaded only when
code is about to move. Three documents, one job each — do not read them all:

| Need | Read |
|------|------|
| the rules (always-on, already in context) | `AGENTS.md` §"Modular architecture" |
| this procedure — triage, layer, wholeness, ratchets | this file |
| the map: module list, layer table, ceilings, chokepoints | `docs/architecture.md` |
| the worked commands, in order, for a real change | `docs/add-a-feature.md` |

## 1. Triage — what are you actually adding?

Answer this BEFORE opening an editor. Most changes are not a new module, and the
most common defect this skill prevents is a new module for work an existing
module already owns (`R-MOD-09`).

| What you have | What it is | Where it goes |
|---|---|---|
| A new user-visible command | a top-level **verb** | the module that owns the DOMAIN, declared in that module's `__verbs__` |
| A new flag or sub-verb on an existing command | an **extension** of that verb | that verb's existing module — do not add a sibling |
| A new entry in a registry the package already publishes (a knowledge category, a lint rule, a dream step, a metric, a panel) | a **non-subcommand extension point** | the recipe for it in `docs/architecture.md` §7(v) — each names its owner file and its test |
| A domain no module owns | a new **module** | the layer §2 resolves |
| A second obligation inside a file you are already editing | a **split** | §4 |

If it fits none of these, STOP and record a `> DECISION:` naming the layer you
chose and why, before writing code (`R-MOD-12`). Parking it in the entrypoint or
a grab-bag module is the failure mode the rule exists to catch.

## 2. Layer — resolve it from the import direction, not from the topic

The layers are ordered, and imports run one way only (`R-MOD-02`):

```
core  ->  knowledge  ->  retrieval  ->  ops  ->  features  ->  cli
```

Read that as: `core` may import nothing else; `cli` may import anything. Resolve
your module's layer by asking **what it needs to import**, not what it feels
related to — a module belongs at the layer of its deepest dependency, one step
above it at most.

- **An upward module-level import is a defect, not a style choice.** Fix it, in
  this order: (1) move the shared symbol DOWN to a layer both callers may import;
  (2) move the module to the layer it actually belongs in; (3) only then, a
  function-local import carrying a stated reason in a comment — the one escape
  hatch, and it must say why.
- **A symbol used as a default-arg value cannot be lazily imported** — defaults
  evaluate at import time, so it must be importable at module level from its
  user. `docs/architecture.md` §7(iv) has the full rule.
- **Never name a module** `utils`, `helpers`, `common`, `misc` or `shared`
  (`R-MOD-05`). A module named for what it is NOT has no boundary, so it grows
  until it is the monolith again under a friendlier name. If you cannot name it
  for what it owns, you have not finished §1.

The live layer table and every module's current fan-in are rendered from
`catalog/modules.json` into `docs/architecture.md` §2 — read the map there
rather than guessing from directory names.

## 3. Land the unit WHOLE — five artifacts, one change

`R-MOD-11`: a change that lands a module without its registration, its manifest
entry, its owning test or its parity row is not a smaller change, it is a broken
one — the gates will reject it and the next agent inherits a half-unit.

1. **The module** — `scripts/agentware_lib/<layer>/<name>.py`, with the four
   docstring contract lines (`Owns:` / `Does NOT own:` / `Layer deps:` /
   `Fan-in:`) and an explicit `_EXPORTS` / `__all__` tail.
2. **The declaration** — if it owns a verb, `__verbs__` in that SAME module.
   There is no central registry to edit; adding one is forbidden (`R-MOD-01`).
3. **The manifest entry** — `python3 tests/_manifest_gen.py --write`. NEVER
   hand-edit `catalog/modules.json` (`R-MOD-07`).
4. **The mirrored owning test** — `tests/unit/<layer>/test_<name>.py`
   (`R-MOD-06`). A test in another file that merely *references* your module is
   not ownership: ownership means that file goes red when your module alone
   breaks.
5. **The parity row** — a curated row in `tests/_parity_curated.py`, then
   `python3 tests/_parity_manifest.py --out` (`R-MOD-08`). Never hand-edit the
   generated manifest.

**Do not hand-write any of that.** `scripts/agentware scaffold new` emits the
hand-maintained half and runs both generators; `scripts/agentware scaffold check`
audits an already-written unit and is usable from a clone with no test runner.
The four steps the scaffold cannot do for you — the parser golden pins, the
help fixtures, the curated row's dimension, the writer roster — are Path A of
`docs/add-a-feature.md`, in order, with their real commands.

## 4. Splitting a file that nears its cap

Every file lives under a line cap and every function under 120 lines / 12
branches (`R-MOD-03`); the live cap table is rendered into `docs/architecture.md`
§6.

**Split where the OBLIGATION changes** (`R-MOD-04`) — not at the midpoint, and
not into a grab-bag. Ask what SECOND promise the file has started making; that
promise is the new module's name, and the split is clean when each half can be
described without the word "and".

**Never raise a cap and never add a debt row to fit the code.** The caps are
pinned by value in `tests/gates/test_module_manifest.py` so that lowering the bar
takes two deliberate edits in two files. The pre-existing residuals live in
`tests/fixtures/ceiling-debt.json`, `tests/fixtures/modularity-debt.json` and
`tests/fixtures/test-ownership-debt.json`; all three are **shrink-only in both
directions** — an undeclared violation fails, AND a declared row the tree no
longer produces fails, so a ratchet cannot rot into an allowlist.

When you MOVE a test file, record the move in `tests/_test_layout.py`'s
`RELOCATIONS`: a node id embeds its module path, and the preserved-surface freeze
reads a moved id as a deleted one. For the same reason, never rename a test
method to fix a stale name — narrow what it asserts and say so in its docstring.

## 5. When a gate rejects the change

Read the failure as a *claim about your change*, and fix the change — not the
gate. These are the ones this procedure trips most:

| Gate | What its failure means | Fix |
|---|---|---|
| `tests/gates/test_module_manifest.py` | the manifest disagrees with the tree | re-run `python3 tests/_manifest_gen.py --write`; never hand-edit the JSON |
| `tests/gates/test_module_ceilings.py` | a file or function is over its cap | split at the obligation seam (§4) |
| `tests/gates/test_modularity_rules.py` | a module-level upward import, or a forbidden module name | move the symbol down, move the module, or use the stated-reason lazy import |
| `tests/gates/test_test_ownership.py` | a module has no `tests/unit/<layer>/test_<stem>.py` | write it, or re-aim the file that already exercises the module (through `RELOCATIONS`) |
| `tests/gates/test_verb_discovery.py` | the AST-read declarations and the imported ones disagree | your module fails to import, or the verb is built at runtime |
| `tests/gates/test_parity_manifest.py` | a capability has no row, or the generated manifest is stale | add the curated row, then `python3 tests/_parity_manifest.py --out` |
| `tests/gates/test_guidance_sync.py` | a persona, doc or skill names a path the manifest does not claim, or a retired one | correct the pointer; a path that is deliberately absent needs a witnessed row, not a deletion |

`docs/add-a-feature.md` §4 carries the full table — 29 rows across wiring, shape,
surface and whole-tree gates — each with the gate's real message, what it means
and the command that clears it.

## 6. Before you claim done

Run these, read the exit codes, and fix what bites:

```sh
python3 tests/_manifest_gen.py --check
python3 tests/_parity_manifest.py --check
python3 tests/_ceiling_gate.py
python3 tests/_modularity_rules.py
python3 tests/_test_ownership.py
python3 tests/_guidance_sync.py
python3 tests/_doc_sync.py --check
scripts/agentware scaffold check
```

Then the suite, per `tests/README.md` (tier-scoped; the package has zero hard
dependencies, so a stock clone has no pytest), and — because every package edit
is self-extension (`R-PKG-03`) — `scripts/agentware steering lint`,
`scripts/agentware eval --record --gate` and `scripts/agentware gate release`.

## 7. Porting this method

Nothing above is language-specific. A codebase gets the same property from three
pieces: a **generated** module manifest that no one hand-edits, an ordered layer
list with a one-way import rule, and debt ledgers that may only shrink in both
directions. The rules belong in always-on steering; the procedure belongs in a
file like this one, loaded when code is about to move.
