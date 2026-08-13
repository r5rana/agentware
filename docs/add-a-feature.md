# Adding a feature to agentware — a worked walkthrough

> **Companion to [`docs/architecture.md`](architecture.md).** That document is the
> map: layers, ceilings, registration, and how the gates enforce them. This one is
> the road: the real commands, in order, from an empty idea to a green tree —
> plus a troubleshooting section for **every gate that can reject your change**.
>
> Contributor rules of the road: [`AGENTS.md`](../AGENTS.md) (`R-MOD-01..12` are
> the modular-architecture rules) · running the suite:
> [`tests/README.md`](../tests/README.md) · the module router:
> [`scripts/agentware_lib/README.md`](../scripts/agentware_lib/README.md).

**All package edits are self-extension** (`R-PKG-03`). Nothing here is optional
bookkeeping: every step below exists because a committed gate fails without it.

---

<a id="how-to-read"></a>
## How to read this document

This walkthrough is **executable**. `tests/e2e/test_add_a_feature_walkthrough.py`
copies this tree into a scratch checkout, applies every block below in document
order, and asserts each one does what the prose says. If a command here stops
working, this document goes red before a reader can be misled by it.

| Block | Means |
|-------|-------|
| ```` ```bash ```` | run it at the repo root; it must exit **0** |
| ```` ```bash expect-fail ```` | run it; it must exit **non-zero** (a mutation proof) |
| ```` ```bash norun:<reason> ```` | shown for the reader, not run by the e2e, with the reason stated |
| ```` ```python path=<file> ```` | write that file with exactly this body |
| ```` ```diff path=<file> ```` | apply this hunk to that file: the `-` lines are replaced by the `+` lines |

Counts in the prose (`128` surfaces, `203` modules) are this tree's values on the
day the step was written. Yours will differ once verbs land after this one; the
**shape** of each step does not.

## Contents

1. [Which path am I on?](#which-path)
2. [Path A — a new top-level verb, from nothing to green](#path-a)
3. [Path B — extending a verb that already exists](#path-b)
4. [Troubleshooting — every gate that can reject your change](#troubleshooting)
5. [The finish line](#finish-line)

---

<a id="which-path"></a>
## 1. Which path am I on?

**Extend an existing verb's module rather than adding one** (`R-MOD-09`). Adding a
module is for a domain no module owns; adding a verb is for a capability no verb
exposes. Most changes are Path B, and Path B is four steps.

| You want to | Path | Cost |
|---|---|---|
| add an argument, a sub-verb, or a behaviour to an existing verb | [B](#path-b) | one module + its owning test |
| add a top-level verb the CLI does not have | [A](#path-a) | one module + eight wiring edits, all named below |
| add a knowledge category, dream step, MCP tool, lint rule, panel, … | neither — see the 14 recipes in [`architecture.md` §7(v)](architecture.md#7-the-extensibility-contract) | one anchor + one test |

Find the owning module before you write anything — `grep` is not the tool, the
router is:

```bash
grep -n "plan lint\|planlint" scripts/agentware_lib/README.md
```

---

<a id="path-a"></a>
## 2. Path A — a new top-level verb, from nothing to green

The worked example is a verb called `greet` that prints one line. It is
deliberately trivial: everything hard about adding a verb here is the **wiring**,
and a trivial handler makes the wiring the only thing on screen.

### Step 1 — scaffold the unit

Do not hand-write any of this. `scaffold new` emits the hand-maintained half and
runs the two generators for you (`R-MOD-07`, `R-MOD-11`):

```bash
scripts/agentware scaffold new --kind verb --layer ops --name greet --verb greet --task 75
```

It reports exactly what it claimed:

```text
scaffold verb `greet` (verb `greet`):
  create  scripts/agentware_lib/ops/greet.py
  create  tests/unit/ops/test_greet.py
  edit    scripts/agentware_lib/README.md
  edit    tests/_parity_curated.py
  regen   manifest (rc=0) wrote catalog/modules.json
  regen   parity (rc=0) [OK] wrote tests/fixtures/parity-manifest.json
```

Three things to notice, because they are the whole design:

- **`--layer` is a decision, not a formality.** `ops` because a greeting is an
  operation over resolved state; a verb that reads or writes the knowledge index
  is `knowledge`, one that ranks is `retrieval`, one that composes several layers
  is `features`. If your change fits no layer, STOP and record the `> DECISION:`
  before writing code (`R-MOD-12`).
- **`--task` lands on the curated parity row**, which the parity schema refuses
  without one. Use the plan task number you are working under.
- **The order key is derived** (here `4600`, a sparse key above the current
  maximum) and it IS the verb's `--help` display position.

The verb answers immediately — registration is complete before the handler is:

```bash
scripts/agentware greet
```

### Step 2 — fill the module

The scaffold leaves `TODO(scaffold)` markers. `scaffold check` does not read
them; a reviewer does, and the marker is the only thing distinguishing a unit
that was *described* from one that was merely *generated*. Delete every one:

```python path=scripts/agentware_lib/ops/greet.py
"""agentware_lib.ops.greet — the operator greeting surface (layer: ops).

Owns: the `agentware greet` verb — rendering one greeting line for a named
addressee, in text or json.
Does NOT own: operator identity resolution, which `core.identity` owns and this
module would call rather than re-derive.
Layer deps: ops -> core, knowledge, retrieval (stdlib only; every edge DOWNWARD, R-MOD-02).
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by).
"""

import json
import sys


#: Verb declaration — the ONE place this verb is registered (R-MOD-01).
__verbs__ = (
    ("greet", 4600, "register_greet"),
)

_EXPORTS = [
    "cmd_greet",
    "greeting",
]

__all__ = list(_EXPORTS)

#: The addressee used when `--name` is not given.
DEFAULT_NAME = "operator"


def greeting(name):
    """The pure, deterministic core: one greeting line for `name` (INV-1)."""
    return "hello, %s" % (name or DEFAULT_NAME)


def cmd_greet(args):
    """`agentware greet` — print one greeting line. Returns the exit code."""
    line = greeting(getattr(args, "name", None))
    if getattr(args, "format", "text") == "json":
        json.dump({"greeting": line}, sys.stdout, ensure_ascii=False,
                  indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print(line)
    return 0


def register_greet(sub, fmt_parent):
    """Declare the `greet` verb and its one optional addressee argument."""
    parser = sub.add_parser("greet", parents=[fmt_parent],
                            help="print one greeting line for an addressee")
    parser.add_argument("--name", default=DEFAULT_NAME,
                        help="who to greet (default: %s)" % DEFAULT_NAME)
    parser.set_defaults(func=cmd_greet)
```

The four docstring contract lines (`Owns:` / `Does NOT own:` / `Layer deps:` /
`Fan-in:`) are parsed by the manifest generator, not decoration. `register_<verb>`
takes `(sub, fmt_parent)` even when `--format` is unused — the signature is the
discovery contract. `_EXPORTS` is *what cross-module consumers need*, not every
public name.

```bash
scripts/agentware greet --name rahul --format json
```

### Step 3 — fill the owning test

Every `agentware_lib` module is owned by `tests/unit/<layer>/test_<stem>.py` and
by nothing else (`R-MOD-06`). The parity gate additionally refuses an owning test
with **two or fewer** test methods — the scaffolded stub is exactly two, so the
gate makes you write at least one assertion about your own domain:

```python path=tests/unit/ops/test_greet.py
"""Owning unit file for `agentware_lib.ops.greet` (R-MOD-06).

Asserts the module's published contract — that it imports, that it carries the
docstring labels the manifest generator parses, that its landing surface answers,
and that the `--name` default the parity row names as its mutation is really
wired — so it reddens on the day the module breaks.
"""

from tests._paths import REPO_ROOT

import argparse
import os
import sys
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import agentware_lib.ops.greet as greet  # noqa: E402


class GreetTest(unittest.TestCase):

    def test_the_module_publishes_its_docstring_labels(self):
        doc = greet.__doc__ or ""
        for label in ("Owns:", "Does NOT own:", "Layer deps:"):
            self.assertIn(label, doc,
                          "%s is missing the %s label the manifest parses"
                          % (greet.__name__, label))

    def test_the_landing_surface_answers(self):
        self.assertTrue(greet.__verbs__ and callable(greet.register_greet))

    def test_the_name_argument_defaults_to_the_published_default(self):
        """The MUTATION the curated parity row names: drop `--name`'s default."""
        parser = argparse.ArgumentParser()
        fmt = argparse.ArgumentParser(add_help=False)
        fmt.add_argument("--format", default="text")
        greet.register_greet(parser.add_subparsers(dest="verb"), fmt)
        args = parser.parse_args(["greet"])
        self.assertEqual(args.name, greet.DEFAULT_NAME)
        self.assertEqual(greet.greeting(args.name), "hello, operator")


if __name__ == "__main__":
    unittest.main()
```

### Step 4 — say what the parity row proves

The scaffold appended a curated row to `tests/_parity_curated.py` with two
`TODO(scaffold)` fields. A row is only evidence if it names a **mutation** —
a change to the unit's behaviour that its owning test must catch (`R-MOD-08`):

```diff path=tests/_parity_curated.py
-         "TODO(scaffold): the `agentware greet` verb surface and its handler",
+         "the `agentware greet` verb surface and its handler",
```

```diff path=tests/_parity_curated.py
-         "TODO(scaffold): name the MUTATION that reddens tests.unit.ops.test_greet — a change to this unit's behaviour that its owning test must catch"),
+         "drop the `--name` default from register_greet"),
```

A curated row's `task` must belong to its dimension's declared plan tasks, so
register the task number once:

```diff path=tests/_parity_schema.py
-        "plan_tasks": [7, 29, 32, 52, 54, 73],
+        "plan_tasks": [7, 29, 32, 52, 54, 73, 75],
```

### Step 5 — pin the surface

A new verb IS a `--help` change. Two independent pins record it — the ordered
verb list and the total surface count — and they are deliberately not touched by
the scaffold: *a generator that moved its own pin would certify its own change.*

```diff path=tests/gates/test_parser_golden.py
-    "prefer", "review", "remediate", "review-report", "graph", "scaffold",
+    "prefer", "review", "remediate", "review-report", "graph", "scaffold",
+    "greet",
```

```diff path=tests/gates/test_parser_golden.py
-EXPECTED_SURFACES = 128
+EXPECTED_SURFACES = 129
```

`EXPECTED_SURFACES` counts `1 root + verbs + sub-verbs`: raise it by one per new
help surface — the verb, plus each of its sub-verbs. Then re-capture the rendered
help corpus (never hand-edit a fixture under `tests/fixtures/help_golden`):

```bash
COLUMNS=80 python3 tests/_help_golden.py --write
```

### Step 6 — classify the verb as a writer or a reader

`tests/_org_writer_rows.py` is the roster: every local-only CLI node is in exactly
one of WRITERS / UNPROBED_WRITERS / NON_WRITERS, and the e2e asserts the
partition, so a verb added later cannot join an unenumerated set. The question the
row answers is **does this node write the knowledge dir**, because that is what
has a team shard to land in or leak out of (`R-ORG-02`):

```diff path=tests/_org_writer_rows.py
-    "scaffold": "parent: subcommands only, no action of its own",
+    "greet": "reader: prints one greeting line, writes nothing",
+    "scaffold": "parent: subcommands only, no action of its own",
```

### Step 7 — the one pin that is not about your verb

`scaffold`'s own declaration test asserts its order key is the highest in the
tree — true when it landed, false the moment anyone uses the tool for its
purpose. Narrow the assertion **in place**; never rename a test method to fix a
stale name, because the preserved-surface freeze reads a rename as a deletion
(see [`tests/README.md`](../tests/README.md)):

```diff path=tests/unit/ops/test_scaffoldcmd.py
-    def test_the_order_key_is_unique_and_last(self):
-        keys = [order for _v, order, _m, _a in vb.discover()]
-        self.assertEqual(keys.count(4500), 1)
-        self.assertEqual(max(keys), 4500)
+    def test_the_order_key_is_unique_and_last(self):
+        """Narrowed: the key is UNIQUE, and last among the verbs that existed
+        when scaffold landed. A verb added later takes a higher key by design,
+        and the whole ordered surface is pinned by
+        tests/gates/test_parser_golden.py::EXPECTED_VERBS, which owns that claim."""
+        keys = [order for _v, order, _m, _a in vb.discover()]
+        self.assertEqual(keys.count(4500), 1)
+        self.assertEqual(max(k for k in keys if k <= 4500), 4500)
```

### Step 8 — regenerate every derived artifact

Five generators, none of whose outputs may ever be hand-edited. Run them in this
order; each is idempotent, so re-running one is always safe:

```bash
python3 tests/_manifest_gen.py --write
python3 tests/_parity_manifest.py --out
python3 tests/_parity_manifest.py --stamp-ratchet
python3 tests/_constant_differ.py --write
python3 tests/_doc_sync.py --write
```

| Generator | Writes | Why your verb moved it |
|---|---|---|
| `_manifest_gen.py` | `catalog/modules.json` | a new file must be claimed by the manifest (`R-MOD-07`) |
| `_parity_manifest.py --out` | `tests/fixtures/parity-manifest.json` | your curated row is compiled into the ledger |
| `_parity_manifest.py --stamp-ratchet` | `tests/fixtures/parity-ratchet.json` | the shrink-only floors move up by your row |
| `_constant_differ.py --write` | `tests/fixtures/constant-divergence.json` | your module's `_EXPORTS` changes a pinned literal count |
| `_doc_sync.py --write` | `scripts/agentware_lib/README.md` · `docs/architecture.md` | both module maps gain your row, rendered from the manifest |

### Step 9 — prove the mutation bites

A gate that cannot be made to fail proves nothing, and your parity row now claims
one. Apply exactly the mutation the row names:

```diff path=scripts/agentware_lib/ops/greet.py
-    parser.add_argument("--name", default=DEFAULT_NAME,
+    parser.add_argument("--name",
```

The named test must go **red** — if it stays green, the row is a claim with no
evidence behind it and the test is the thing to fix, not the row:

```bash expect-fail
python3 -m unittest tests.unit.ops.test_greet
```

Put it back:

```diff path=scripts/agentware_lib/ops/greet.py
-    parser.add_argument("--name",
+    parser.add_argument("--name", default=DEFAULT_NAME,
```

```bash
python3 -m unittest tests.unit.ops.test_greet
```

### Step 10 — ask the wiring question from the outside

`scaffold check` asks the same five questions in a second, from a clone, with no
test runner — manifest entry, declared-verb/manifest agreement (two independent
witnesses: discovery *imports*, the manifest reads *AST*), mirrored owning test,
a parity row per top-level verb, and a router row per module:

```bash
scripts/agentware scaffold check
```

Then the structural gates, each runnable on its own (this is the fast loop — the
whole set takes about two seconds):

```bash
python3 tests/_ceiling_gate.py
python3 tests/_modularity_rules.py
python3 tests/_test_ownership.py
python3 tests/_manifest_gen.py --check
python3 tests/_parity_manifest.py --check
python3 tests/_doc_sync.py --check
python3 tests/_doc_extensibility_check.py
python3 tests/_walkthrough_check.py
```

### Step 11 — run the tests

Targeted first — `R-AP-09`: never run a broad suite when a specific test will do:

```bash
python3 -m unittest tests.unit.ops.test_greet tests.unit.ops.test_scaffoldcmd
```

Then the tiers your change reaches, and finally the whole corpus. The whole suite
is **four tier-scoped runs**, never `discover -s tests` over the whole tree — that
collects `tests/fixtures/` and intermittently hangs at 0.0% CPU
([`tests/README.md`](../tests/README.md)):

```bash norun:the-suite-cannot-run-itself
for t in unit gates integration e2e; do
    python3 -m unittest discover -s tests/$t -t . || echo "TIER $t RED"
done
```

Green means *no worse than the recorded baseline*, never *zero failures* — read
`tests/baselines/` for what is already known-red.

### Step 12 — the self-extension gate chain

Every package edit closes with these (`R-PKG-04..06`). They are slower and belong
at the end, not in the loop:

```bash norun:mutates-recorded-baselines-and-reads-the-live-knowledge-dir
scripts/agentware steering lint --strict
scripts/agentware eval --record --gate
scripts/agentware gate release
```

`steering lint` only moves if you touched `AGENTS.md` or `steering/*.md`;
`eval --gate` is the retrieval no-regression arm (INV-7); `gate release` runs
content preservation, gold-fixture retrieval and reliability, and a **FAIL there
STOPS the change** unless it is pre-existing drift unrelated to your diff — which
is recorded as a `> DECISION:`, not waved through.

---

<a id="path-b"></a>
## 3. Path B — extending a verb that already exists

Four steps, no generators to run unless you add a file. The whole point of the
layered tree is that this path is short.

1. **Find the owner.** One `grep` against the router, never a tree-wide search:

   ```bash
   grep -n "planlint" scripts/agentware_lib/README.md
   ```

2. **Edit that module only.** Add the argument in its `register_*`, the behaviour
   in its `cmd_*` or a pure helper beside it. Keep every module-level import
   DOWNWARD (`R-MOD-02`); if you genuinely need a symbol from a higher layer, make
   it a function-local import **with a stated reason** — that is the documented
   escape hatch, and a module-level upward edge is a gate failure.

3. **Extend the module's owning test**, `tests/unit/<layer>/test_<stem>.py`. Add
   a method; never rename an existing one.

4. **Run the module's test and the gates that can notice you:**

   ```bash
   python3 -m unittest tests.unit.ops.test_planlint
   python3 tests/_ceiling_gate.py
   python3 tests/_modularity_rules.py
   ```

**When Path B grows extra steps:**

| If your change… | Also do |
|---|---|
| adds or removes a **sub-verb**, or any `--flag` on a documented surface | raise `EXPECTED_SURFACES` if it is a new help surface, then `COLUMNS=80 python3 tests/_help_golden.py --write` (Step 5) |
| pushes the file over its **line cap**, or a function over 120 lines / 12 branches | split where the obligation changes; **never raise a cap** (`R-MOD-04`) — the caps are pinned by value in `tests/gates/test_module_manifest.py` |
| adds a **new module** | recipe (ii) in [`architecture.md` §7](architecture.md#7-the-extensibility-contract): docstring contract lines, `_EXPORTS`, `tests/_manifest_gen.py --write`, an owning unit file, `tests/_doc_sync.py --write`, and a curated parity row |
| makes the verb **write the knowledge dir** for the first time | move its row in `tests/_org_writer_rows.py` from NON_WRITERS to WRITERS and give it a live probe (Step 6) |
| touches one of the **14 extension points** (a category, a dream step, an MCP tool, a lint rule, a panel…) | follow that recipe in [`architecture.md` §7(v)](architecture.md#7-the-extensibility-contract) — each names its owner file, whether the edit is LOCAL or SHARED, and the `Test:` to extend |
| changes **ranking** | keep it deterministic and parameterised on `as_of` (INV-1), keep it read-only (INV-2), exclude org frontmatter (`R-ORG-08`), and close with `scripts/agentware eval --record --gate` |

---

<a id="troubleshooting"></a>
## 4. Troubleshooting — every gate that can reject your change

Each row is a gate that has actually rejected the walkthrough above. **Read the
message before you touch anything**: most of these are a regenerator you have not
run yet, and the two that are not are telling you something true.

### The wiring gates

| Gate | What it says | What it means | Fix |
|---|---|---|---|
| `scripts/agentware scaffold check` | `R-MOD-07: <module> has no manifest entry` | a file on disk that the manifest does not claim | `python3 tests/_manifest_gen.py --write` |
| `scripts/agentware scaffold check` | `R-MOD-06: <module> has no owning test` | no `tests/unit/<layer>/test_<stem>.py`; a file that merely *references* your module is not ownership | write the owning unit file (Step 3) |
| `scripts/agentware scaffold check` | `R-MOD-08: verb <v> has no parity row` | the completeness ledger cannot see your capability — `surface:*` rows are generated from a pre-merge fixture and cannot cover anything added since | add a curated row in `tests/_parity_curated.py`, then `--out` (Step 4) |
| `tests/gates/test_module_manifest.py` | `manifest is stale` / a claim that resolves to nothing | the manifest and the tree disagree in either direction | `python3 tests/_manifest_gen.py --write` — **never** hand-edit `catalog/modules.json` |
| `tests/gates/test_verb_discovery.py` | the AST witness and the import witness disagree | `__verbs__` names a registrar that does not exist, two verbs share an order key, or a module was renamed without its declaration | fix the declaration in its one owning module (`R-MOD-01`) |

### The shape gates

| Gate | What it says | What it means | Fix |
|---|---|---|---|
| `tests/gates/test_module_ceilings.py` (`python3 tests/_ceiling_gate.py`) | `<file> is N lines over its <layer> cap` / `<func> is over 120 lines or 12 branches` | the unit outgrew its obligation | split where the obligation changes (`R-MOD-04`). Do **not** raise a cap and do **not** declare a debt row to fit new code — the ledgers only shrink |
| `tests/gates/test_modularity_rules.py` (`python3 tests/_modularity_rules.py`) | `module-level upward import <a> -> <b>` | your import makes the layer claim false and fixes the package load order | move the shared symbol DOWN, or make the edge a function-local import with a stated reason (`R-MOD-02`) |
| `tests/gates/test_modularity_rules.py` | `dumping-ground module name` | you named a module `utils`, `helpers`, `common`, `misc` or `shared` | name it for the domain it owns (`R-MOD-05`) |
| `tests/gates/test_modularity_negative.py` | one of six planted violation shapes was NOT caught | you changed a gate harness and it stopped biting | restore the check; a gate that cannot fail proves nothing |
| `tests/gates/test_test_ownership.py` (`python3 tests/_test_ownership.py`) | `<module> has no owning test` / `debt row forgives a module that now has one` | the shrink-only ledger and the live violation set disagree | write the owning test, or shrink `tests/fixtures/test-ownership-debt.json`. **Never** add a row for new code |
| `tests/gates/test_test_layout.py` | `test file directly under tests/` · `over 600 lines` · `unrecorded relocation` | the corpus is layered and node ids embed the path | move it into a tier, split it along class boundaries, and record any move in `tests/_test_layout.py::RELOCATIONS` |
| `tests/gates/test_preserved_surface.py` | a frozen node id is missing | you renamed or moved a test method, class or module — the freeze cannot tell a rename from a deletion | narrow the existing method in place and say so in its docstring; if the move is real, record it in `RELOCATIONS` |

### The surface gates

| Gate | What it says | What it means | Fix |
|---|---|---|---|
| `tests/gates/test_parser_golden.py` | `the DISCOVERED verb order drifted from the pinned N-verb display order` | a verb was added, dropped, renamed or re-keyed | update `EXPECTED_VERBS` at the verb's display position (Step 5) |
| `tests/gates/test_parser_golden.py` | `EXPECTED_SURFACES … got N` | the help surface count moved; a lost sub-verb cannot diff clean by also losing its fixture | raise or lower `EXPECTED_SURFACES` by one per surface, then `COLUMNS=80 python3 tests/_help_golden.py --write` |
| `tests/gates/test_parser_golden.py` | a golden help fixture differs | the rendered `--help` text changed | re-capture with `_help_golden.py --write` (fixed `COLUMNS=80`), never by editing the fixture |
| `tests/gates/test_parity_manifest.py` | `<task> not found in [...] : <row id>` | the curated row's task is not declared on its dimension | add the task number to that dimension's `plan_tasks` in `tests/_parity_schema.py` (Step 4) |
| `tests/gates/test_parity_manifest.py` | `2 not greater than 2 : <row id>` | the owning test named by your row is still the scaffolded stub | write a real assertion about your domain (Step 3) |
| `tests/gates/test_parity_gate.py` | `the committed floors do not match the committed manifest` | the shrink-only ratchet is behind your new row | `python3 tests/_parity_manifest.py --stamp-ratchet` |
| `tests/gates/test_constant_differ.py` | `multi_literal … '_EXPORTS' … lib side pinned N, live N+1` | your module added a pinned literal | `python3 tests/_constant_differ.py --write` |
| `tests/gates/test_doc_architecture.py` (`python3 tests/_doc_extensibility_check.py`) | `manifest module '<m>' is NOT a row in the doc table` · `managed block '<b>' is stale` | a module map drifted from the manifest | `python3 tests/_doc_sync.py --write` |
| `tests/gates/test_doc_walkthrough.py` (`python3 tests/_walkthrough_check.py`) | `names a gate that does not exist` · `runnable block names a missing script` | this document points at something the tree does not have | fix the pointer here, or land the thing it names |
| `tests/gates/test_markdown_links.py` | `no such anchor in target file` / a link to a missing file | a documentation link rotted | fix the link; anchors are GitHub heading slugs |

### The whole-tree gates

| Gate | What it says | What it means | Fix |
|---|---|---|---|
| `tests/e2e/test_org_local_writers_e2e.py` | `local-only verbs missing from the writer roster: ['<v>']` | the roster's three buckets no longer partition the local-only surface | classify your node in `tests/_org_writer_rows.py` (Step 6) |
| `tests/gates/test_portability_gate.py` | `HIT contents environment <file>:<line>` | an environment-specific or operator-product needle reached a committed file | remove it. The debt ledger (`tests/fixtures/portability-debt.json`) forgives only *declared, pre-existing* locations and may never forgive an `operator_product` finding |
| `tests/gates/test_suite_delta.py` | `unexplained pass-count rise` | a module's pass count rose without gaining that many node ids — usually a test that stopped exercising the real path | account for the change, or find the test that silently stopped asserting |
| `tests/gates/test_invariants.py` | `non-stdlib import in a stdlib-only path class (INV-6)` | you added a dependency to the package | the package has zero hard dependencies; use the stdlib, or put the sidecar outside the guarded corpus and say so |
| `tests/gates/test_invariants.py` | `network-capable import in a no-network path class` | a ranking or reporting path can reach the network (INV-1) | keep retrieval deterministic, offline and read-only |
| `scripts/agentware steering lint` | `rule-count N (baseline M)` · a malformed rule id | you changed `AGENTS.md` or `steering/*.md` | re-stamp the baseline through `ops.steering._write_rule_count_baseline`; rule ids must match `DSF_RULE_ID_RE` |
| `scripts/agentware gate release` | a FAIL on content preservation, gold retrieval or reliability | your change moved the product's measured behaviour | STOP. Fix it, or — only if the failure is pre-existing drift unrelated to your diff — record it as a `> DECISION:` known issue (`R-PKG-05`) |

**Two failures that are not yours to fix by editing the gate:** a ceiling and an
ownership debt ledger only ever shrink. If your change needs a new row in either,
the change is wrong, not the ledger.

---

<a id="finish-line"></a>
## 5. The finish line

A change is done when all of these are true:

- [ ] `scripts/agentware scaffold check` passes;
- [ ] the five structural checkers are green (`_ceiling_gate`, `_modularity_rules`,
      `_test_ownership`, `_manifest_gen --check`, `_parity_manifest --check`);
- [ ] your module's owning test passes, and the **mutation your parity row names
      reddens it** — proven, not asserted;
- [ ] the four tiers are no worse than `tests/baselines/`;
- [ ] `steering lint`, `eval --record --gate` and `gate release` are clean, or
      their failure is a recorded pre-existing `> DECISION:`;
- [ ] every `> LEARNED:` and `> DECISION:` in your worklog is promoted
      (`R-SI-03`, `R-AUTO-05`).

The unit lands **whole, in one change** (`R-MOD-11`): module, verb declaration,
manifest entry, mirrored unit file, parity row. A change missing any of them is
not a smaller change — it is a change the next contributor cannot verify.
