"""agentware_lib.ops.scaffoldunit — the UNIT MODEL a scaffold emits (layer: ops).

Owns: what a compliant new UNIT is — the four kinds (`module`, `verb`, `panel`,
`loop-lib`), the `KINDS` spec table declaring which artifacts each kind must
emit, name/layer/verb validation (`resolve`, `ScaffoldError`), the target paths
every artifact lands on, the next display ORDER key a new verb takes, and the
rendered TEXT of each artifact (module skeleton, mirrored test stub, README doc
row, curated parity row).
Does NOT own: WRITING any of it to disk or invoking the regenerators -> that is
ops.scaffoldwire's; the CI audit over an already-written tree -> ops.scaffoldcheck's;
the `scaffold` verb's argparse surface and handlers -> ops.scaffoldcmd's; the
manifest generator, the parity manifest and the ownership rule themselves, which
live in the test corpus (`tests/_manifest_gen.py`, `tests/_parity_manifest.py`,
`tests/_test_ownership.py`) and are this module's DOWNSTREAM, never its import.
Layer deps: ops -> core (paths) plus stdlib. All DOWNWARD.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY A SCAFFOLD AT ALL (plan task 73, R-MOD-11)
---------------------------------------------
`R-MOD-11` obliges a new unit to land WHOLE — module, verb declaration, manifest
entry, mirrored unit file and parity row — and every one of those has its own
gate. An author who lands four of the five gets a red suite and a hunt; an author
who lands all five by hand copies a neighbouring module and inherits whatever
that neighbour happened to carry. Neither is a contract; both are folklore.

So the obligation is EXECUTABLE here. `scaffold new` emits the whole set from one
description of the unit, and `scaffold check` (ops.scaffoldcheck) asks the same
question of a unit somebody wrote by hand. The rule, the generator and the audit
therefore say the same thing by construction rather than by review.

WHY THE LAYER TABLE IS READ, NOT COPIED
---------------------------------------
The allowed dependency direction per layer already exists in exactly one place —
`tests/_layerutil.py` — and is PUBLISHED into `catalog/modules.json` by the
manifest generator. This module reads it from the published catalog rather than
holding a second copy, because a scaffold that renders a `Layer deps:` line from
a stale private copy would emit modules that fail the very boundary gate it
exists to satisfy. If the catalog is unreadable the render falls back to naming
no dependencies at all, which is always true of a fresh skeleton (stdlib only).

WHAT THE SKELETON DELIBERATELY DOES NOT DO
------------------------------------------
It does not write real behaviour, and the placeholders say so in words the author
must delete. A scaffold that emitted a plausible-looking body would produce a
module that passes every structural gate while doing nothing — the exact shape of
`learn-a-coverage-row-whose-test-merely-exists-is-not-evidence`. What it emits is
the WIRING (which is mechanical and gated) and a marker where the behaviour goes
(which is not). The emitted test asserts the module's own published contract, so
it is a real, failing-when-broken test on the day it lands rather than a stub
that passes because it asserts nothing.

Stdlib only, deterministic, read-only (this module renders TEXT; the writer is
ops.scaffoldwire).
"""

import json
import os
import re

import agentware_lib.core.paths as paths


class ScaffoldError(RuntimeError):
    """An invalid unit description: bad name, unknown kind/layer, or a collision.

    Raised BEFORE anything is written, so a rejected scaffold leaves no partial
    unit behind for the audit to trip over later.
    """


#: The four kinds the plan names. A kind is a SPEC, not a template switch: it
#: declares where the unit lands, where its mirrored test lands, and which of the
#: five wiring artifacts it owes.
KIND_MODULE = "module"
KIND_VERB = "verb"
KIND_PANEL = "panel"
KIND_LOOP = "loop-lib"

#: Lib layers a `module`/`verb` may be born into, in dependency order.
LIB_LAYERS = ("core", "knowledge", "retrieval", "ops", "features", "cli")

#: `<stem>` and `<verb>` grammars. The verb grammar is the SAME one
#: `agentware_lib.verbs.VERB_RE` enforces at CLI start-up — a scaffold that could
#: emit a verb the discovery contract refuses would produce a tree that does not
#: start.
STEM_RE = re.compile(r"^[a-z][a-z0-9]*$")
VERB_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

#: The step between issued display-order keys, mirroring
#: `agentware_lib.verbs.ORDER_STEP`. A new verb takes `max + ORDER_STEP` so it
#: lands LAST on `--help` and no existing verb moves.
ORDER_STEP = 100

#: Per-kind spec: where the unit lands, where its owning test lands, and which
#: wiring artifacts it owes. `doc_row` is lib-only because the README table this
#: tree publishes is the LIB module router; a dashboard panel or a loop lib is
#: claimed by the manifest's `files` array instead.
KINDS = {
    KIND_MODULE: {
        "title": "a lib module with no CLI surface",
        "dir": "scripts/agentware_lib/{layer}",
        "ext": ".py",
        "test": "tests/unit/{layer}/test_{stem}.py",
        "needs_layer": True,
        "needs_verb": False,
        "doc_row": True,
        "dimension": "modularity",
    },
    KIND_VERB: {
        "title": "a lib module that declares a top-level CLI verb",
        "dir": "scripts/agentware_lib/{layer}",
        "ext": ".py",
        "test": "tests/unit/{layer}/test_{stem}.py",
        "needs_layer": True,
        "needs_verb": True,
        "doc_row": True,
        "dimension": "cli_surface",
    },
    KIND_PANEL: {
        "title": "a dashboard reader/panel module",
        "dir": "scripts/agentware_dashboard",
        "ext": ".py",
        "test": "tests/unit/dashboard/test_{stem}.py",
        "needs_layer": False,
        "needs_verb": False,
        "doc_row": False,
        "dimension": "modularity",
    },
    KIND_LOOP: {
        "title": "a loop orchestrator shell lib",
        "dir": "scripts/loop",
        "ext": ".sh",
        "test": "tests/integration/test_loop_{stem}.py",
        "needs_layer": False,
        "needs_verb": False,
        "doc_row": False,
        "dimension": "loop_orchestration",
    },
}

KIND_NAMES = tuple(sorted(KINDS))

CATALOG_REL = "catalog/modules.json"
README_REL = "scripts/agentware_lib/README.md"
CURATED_REL = "tests/_parity_curated.py"
#: The runnable half of a curated parity row (task 79). The row states the claim
#: and names the test; this table holds the MUTATION the sweep executes against
#: that test. They are separate files because the row is prose a reviewer reads
#: and the probe is a path-and-literal fixture that goes stale on every rename --
#: so a scaffolded unit owes an entry in BOTH.
PROBE_REL = "tests/_parity_mutation_rows.py"


def repo_root(root=None):
    """The tree under scaffold. Defaults to the checkout this package lives in."""
    return root or paths.REPO_ROOT


def layer_direction(root=None):
    """`layer -> allowed dependency layers`, READ from the published catalog.

    Returns an empty mapping when the catalog is absent or unreadable; callers
    render "stdlib only" in that case rather than guessing a direction.
    """
    try:
        with open(os.path.join(repo_root(root), CATALOG_REL), encoding="utf-8") as fh:
            return json.load(fh).get("layer_direction") or {}
    except (OSError, ValueError):
        return {}


def next_order(root=None):
    """The display-order key a NEW verb takes: the current max plus one step.

    Read from the live `__verbs__` declarations through the discovery contract,
    so it cannot drift from what the parser actually orders by. Import is local:
    this module is reached by the CLI on a cold `scaffold` invocation and must
    not pull the whole package walk at import time.
    """
    from agentware_lib import verbs as _verbs
    keys = [order for _verb, order, _mod, _attr in _verbs.discover()]
    return (max(keys) if keys else 0) + ORDER_STEP


def _require(condition, message):
    if not condition:
        raise ScaffoldError(message)


def resolve(kind, name, layer=None, verb=None, task=0, root=None):
    """Validate a unit description and return the resolved UNIT dict.

    The unit is the ONE description every renderer and the writer read, so a
    target path can never be computed two ways. Raises `ScaffoldError` on an
    unknown kind, a malformed name, a missing/invalid layer, a malformed verb, or
    a target that already exists.
    """
    _require(kind in KINDS, "unknown kind %r (known: %s)"
             % (kind, ", ".join(KIND_NAMES)))
    spec = KINDS[kind]
    _require(bool(name) and STEM_RE.match(name),
             "invalid name %r: a unit stem is lowercase letters and digits, "
             "starting with a letter (e.g. `planlint`)" % (name,))
    _require(name not in ("utils", "helpers", "common", "misc", "shared"),
             "R-MOD-05 refuses the name %r: a module named for what it is NOT "
             "has no boundary" % (name,))
    if spec["needs_layer"]:
        _require(layer in LIB_LAYERS, "kind %s needs --layer (one of %s), got %r"
                 % (kind, ", ".join(LIB_LAYERS), layer))
    else:
        layer = None
    if spec["needs_verb"]:
        _require(bool(verb) and VERB_RE.match(verb),
                 "kind %s needs --verb matching %s, got %r"
                 % (kind, VERB_RE.pattern, verb))
    else:
        verb = None
    _require(int(task or 0) > 0,
             "a unit needs --task <plan task number>: the curated parity row it "
             "carries must name the task that lands it (R-MOD-08), and the parity "
             "schema refuses a row with no task")
    unit = _assemble(kind, spec, name, layer, verb, int(task), root)
    _require(not os.path.exists(os.path.join(repo_root(root), unit["path"])),
             "target already exists: %s (choose another name, or extend that "
             "module — R-MOD-09)" % unit["path"])
    return unit


def _assemble(kind, spec, name, layer, verb, task, root):
    """The pure path/id arithmetic behind `resolve`, once validation has passed."""
    rel_dir = spec["dir"].format(layer=layer or "")
    path = "%s/%s%s" % (rel_dir, name, spec["ext"])
    dotted = None
    if kind in (KIND_MODULE, KIND_VERB):
        dotted = "agentware_lib.%s.%s" % (layer, name)
    return {
        "kind": kind,
        "name": name,
        "layer": layer,
        "verb": verb,
        "task": task,
        "path": path,
        "dotted": dotted,
        "test_path": spec["test"].format(layer=layer or "", stem=name),
        "test_dotted": spec["test"].format(layer=layer or "", stem=name)[:-3]
                       .replace("/", "."),
        "doc_row": bool(spec["doc_row"]),
        "dimension": spec["dimension"],
        "order": next_order(root) if verb else None,
        "row_id": ("surface:%s" % verb) if verb else ("mod:unit:%s" % name),
    }


def _deps_sentence(layer, root=None):
    """The `Layer deps:` sentence for a fresh skeleton in `layer`."""
    if not layer:
        return ""
    allowed = layer_direction(root).get(layer) or []
    if not allowed:
        return "%s -> (nothing below it) — stdlib only at landing." % layer
    return ("%s -> %s (stdlib only at landing; every edge DOWNWARD, R-MOD-02)."
            % (layer, ", ".join(sorted(allowed))))


TODO = "TODO(scaffold)"

_MODULE_HEAD = '''"""{dotted} — {title} (layer: {layer}).

Owns: {todo}: the ONE domain this module owns, named as a domain and not as a
file ("the plan-lint rule engine", not "plan helpers").
Does NOT own: {todo}: the nearest neighbour domain, and the module that owns it.
Layer deps: {deps}
Fan-in: 0 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

SCAFFOLDED by `agentware scaffold new --kind {kind} --name {name}`. Delete every
{todo} marker as you fill this in; `agentware scaffold check` does not read them,
but a reviewer does, and the marker is the only thing distinguishing a unit that
was described from one that was merely generated.
"""
'''

_MODULE_BODY = '''

#: The one thing this module publishes at landing. Replace it with the real
#: domain surface; keep SOMETHING exported, because the manifest's grep anchors
#: are derived from the module's own top-level names.
_EXPORTS = [
    "{stem}_contract",
]


def {stem}_contract():
    """The module's own published contract, so its owning test has something REAL
    to assert on the day it lands. Replace with the domain behaviour."""
    return {{"module": "{dotted}", "scaffolded": True}}
'''

_VERB_BODY = '''

#: Verb declaration — the ONE place this verb is registered (R-MOD-01). Discovered
#: by `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("{verb}", {order}, "register_{attr}"),
)

_EXPORTS = [
    "cmd_{attr}",
]


def cmd_{attr}(args):
    """`agentware {verb}` — {todo}: the real handler. Returns the process exit code."""
    as_json = getattr(args, "format", "text") == "json"
    result = {{"verb": "{verb}", "scaffolded": True}}
    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\\n")
    else:
        print("{verb}: scaffolded — {todo}: implement cmd_{attr}")
    return 0


def register_{attr}(sub, fmt_parent):
    """Declare the `{verb}` verb. {todo}: describe the surface in `help=` text a
    user reads, not in a comment only the author reads."""
    parser = sub.add_parser("{verb}", parents=[fmt_parent],
                            help="{todo}: one line, lowercase, no trailing period")
    parser.set_defaults(func=cmd_{attr})
'''

_PANEL_BODY = '''

_EXPORTS = [
    "read_{stem}",
]


def read_{stem}(root):
    """{todo}: the real reader. Dashboard readers are READ-ONLY by contract — they
    open files under an already-confined root and return JSON-able data."""
    return {{"panel": "{stem}", "root": root, "scaffolded": True}}
'''

_LOOP_LIB = '''#!/usr/bin/env bash
# scripts/loop/{stem}.sh — {todo}: the ONE loop concern this lib owns.
#
# SCAFFOLDED by `agentware scaffold new --kind loop-lib --name {stem}`.
# Sourced by agentware.sh (or by another loop lib) — NEVER executed directly, so
# it defines functions and sets no traps of its own. Keep it under the 250-line
# procedural ceiling; a loop lib that grows past it is the orchestrator
# re-monolithising one file at a time.
#
# Wire it by adding a `source` line in agentware.sh and CALLING the function:
# a definition whose call site never moved is the fail-open shape
# tests/_loop_wiring.py exists to catch.

{stem}_run() {{
  # {todo}: the real body. Echo to stdout; return non-zero to fail the phase.
  echo "{stem}: scaffolded"
}}
'''


def render_unit(unit, root=None):
    """The unit's own source text (Python module or shell lib)."""
    kind = unit["kind"]
    if kind == KIND_LOOP:
        return _LOOP_LIB.format(stem=unit["name"], todo=TODO)
    head = _MODULE_HEAD.format(
        dotted=unit["dotted"] or "agentware_dashboard.%s" % unit["name"],
        title=KINDS[kind]["title"], layer=unit["layer"] or "scripts",
        deps=_deps_sentence(unit["layer"], root) or "dashboard -> stdlib only.",
        kind=kind, name=unit["name"], todo=TODO)
    if kind == KIND_VERB:
        attr = unit["verb"].replace("-", "_")
        return (head + "\nimport json\nimport sys\n"
                + _VERB_BODY.format(verb=unit["verb"], attr=attr,
                                    order=unit["order"], todo=TODO))
    if kind == KIND_PANEL:
        return head + _PANEL_BODY.format(stem=unit["name"], todo=TODO)
    return head + _MODULE_BODY.format(stem=unit["name"], dotted=unit["dotted"])


_TEST_PY = '''"""Owning unit file for `{subject}` (R-MOD-06).

SCAFFOLDED with the unit. It asserts the module's PUBLISHED contract — that it
imports, that it carries the docstring labels the manifest generator parses, and
that its landing surface answers — so it reddens on the day the module breaks
rather than passing because it asserts nothing.

{todo}: add the domain assertions. A test that only proves the scaffold ran is
coverage theatre; the parity row this unit carries names the mutation that must
redden THIS file.
"""

from tests._paths import REPO_ROOT

import os
import sys
import unittest

if os.path.join(REPO_ROOT, "scripts") not in sys.path:
    sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

{importline}  # noqa: E402


class {cls}Test(unittest.TestCase):

    def test_the_module_publishes_its_docstring_labels(self):
        doc = {mod}.__doc__ or ""
        for label in ("Owns:", "Does NOT own:", "Layer deps:"):
            self.assertIn(label, doc,
                          "%s is missing the %s label the manifest parses"
                          % ({mod}.__name__, label))

    def test_the_landing_surface_answers(self):
        self.assertTrue({assertion})


if __name__ == "__main__":
    unittest.main()
'''

_TEST_SH = '''"""Wiring test for `scripts/loop/{stem}.sh` (plan task 73 scaffold).

A loop lib has no Python surface, so its owning test asserts the two things that
can silently rot: the lib PARSES (`bash -n`), and its function is actually CALLED
from the orchestrator or another lib — the fail-open shape tests/_loop_wiring.py
was written for.

{todo}: assert the lib's real behaviour by sourcing it in a scratch shell.
"""

import os
import subprocess
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.path.join(REPO, "scripts", "loop", "{stem}.sh")


class Loop{cls}Test(unittest.TestCase):

    def test_the_lib_parses(self):
        proc = subprocess.run(["bash", "-n", LIB], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_lib_defines_its_entry_function(self):
        with open(LIB, encoding="utf-8") as fh:
            self.assertIn("{stem}_run()", fh.read())


if __name__ == "__main__":
    unittest.main()
'''


def _camel(name):
    return name[:1].upper() + name[1:]


def render_test(unit):
    """The mirrored owning test stub for this unit."""
    if unit["kind"] == KIND_LOOP:
        return _TEST_SH.format(stem=unit["name"], cls=_camel(unit["name"]),
                               todo=TODO)
    if unit["kind"] == KIND_PANEL:
        mod = unit["name"]
        importline = "import agentware_dashboard.%s as %s" % (mod, mod)
        assertion = '%s.read_%s(".")["scaffolded"]' % (mod, mod)
    else:
        mod = unit["name"]
        importline = "import %s as %s" % (unit["dotted"], mod)
        if unit["kind"] == KIND_VERB:
            assertion = "%s.__verbs__ and callable(%s.register_%s)" % (
                mod, mod, unit["verb"].replace("-", "_"))
        else:
            assertion = "%s.%s_contract()[\"scaffolded\"]" % (mod, unit["name"])
    return _TEST_PY.format(subject=unit["dotted"] or unit["path"],
                           importline=importline, mod=mod,
                           cls=_camel(unit["name"]), assertion=assertion,
                           todo=TODO)


def render_doc_row(unit):
    """The `scripts/agentware_lib/README.md` module-router row for this unit."""
    return ("| `%s` | %s | %s: %s | `%s` |"
            % (unit["dotted"], unit["layer"], TODO,
               "the ONE domain this module owns", unit["test_path"]))


def _pysafe(text):
    """A one-line Python string literal body — the scaffold never emits a row it
    cannot re-read, so a quote or a backslash in prose can never break the file
    the parity gate imports."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def render_probe_entry(unit):
    """The `MUTATION_PROBES` entry this unit's parity row owes (task 79).

    A generator cannot know which byte of a module, when changed, breaks the
    capability its owning test guards -- that is the author's judgement and the
    whole content of the claim. So the entry it writes is STRUCTURALLY valid and
    DELIBERATELY not yet runnable: `python3 tests/_parity_sweep.py --check`
    accepts it (the shape and the path are real), and the sweep reports it
    STALE-PROBE until the author replaces the two markers. That asymmetry is the
    point. A scaffold that invented a plausible-looking mutation would be
    certifying a claim nobody made, which is the exact failure mode the sweep
    was built to remove; leaving the row red until a human fills it in is the
    honest default.
    """
    return ('    "%s": {\n'
            '        "kind": "replace",\n'
            '        "path": "%s",\n'
            '        "find": "%s: the literal whose change breaks this unit",\n'
            '        "replace": "%s: what it becomes when the capability is gone",\n'
            '    },\n'
            % (unit["row_id"], unit["path"], TODO, TODO))


def render_parity_row(unit):
    """The curated `tests/_parity_curated.py` row this unit owes (R-MOD-08)."""
    what = ("the `agentware %s` verb surface and its handler" % unit["verb"]
            if unit["verb"] else "the %s unit `%s`"
            % (KINDS[unit["kind"]]["title"], unit["path"]))
    mutation = ("%s: name the MUTATION that reddens %s — a change to this unit's "
                "behaviour that its owning test must catch"
                % (TODO, unit["test_dotted"]))
    return ('    _row("%s", "%s",\n'
            '         "%s: %s",\n'
            '         LOCAL, %d, "%s",\n'
            '         "%s"),\n'
            % (unit["row_id"], unit["dimension"], TODO, _pysafe(what),
               unit["task"], unit["test_dotted"], _pysafe(mutation)))


_EXPORTS = [
    "ScaffoldError",
    "KINDS",
    "KIND_NAMES",
    "LIB_LAYERS",
    "TODO",
    "repo_root",
    "layer_direction",
    "next_order",
    "resolve",
    "render_unit",
    "render_test",
    "render_doc_row",
    "render_parity_row",
    "render_probe_entry",
]
