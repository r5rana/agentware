#!/usr/bin/env python3
"""Runnable R-DATA-01 completeness gate for the contributor architecture doc.

Underscore-prefixed so `unittest discover` never COLLECTS it (the convention
`tests/_doc_sync.py`, `tests/_ast_forward_edges.py` and `tests/_global_resolve.py`
already carry); it is a standalone, stdlib-only, py3.7-safe checker meant to be
run directly and re-targeted:

    python3 tests/_doc_extensibility_check.py [<doc.md> ...]

Default target: `docs/architecture.md`.

A documentation gate is worth having only if it can fail for a reason the author
did not think of, so this asserts FIVE things and exits non-zero naming every
offender:

  A. Every non-subcommand extension point has a recipe entry — its anchor token
     appears in the recipes section, and the section carries one `Test:` field
     per recipe.
  B. Every shared-file chokepoint is listed WITH a mitigation.
  C. The doc's module table has parity with `catalog/modules.json` in BOTH
     directions (a manifest module with no row, and a row naming no module, are
     both failures).
  D. Every managed block, and the router table in
     `scripts/agentware_lib/README.md`, is what a regeneration would produce —
     delegated to `tests/_doc_sync.py`, so the doc cannot drift from the manifest
     by hand-editing either side.
  E. The router table has the same two-way parity as C, allowing a row that
     names a PACKAGE the manifest publishes modules inside (`…core.config`),
     since the router documents the package seam as well as its leaves.

  F. **Every anchor the recipes name still RESOLVES in the tree.** This is the
     arm that stops the document rotting: A only proves the doc mentions
     `DREAM_STEP_FUNCS`, and a doc that confidently names a symbol deleted three
     waves ago is worse than one that says nothing (R-FAIL-08). So each recipe
     declares the file that OWNS its extension point and a token that must be
     found there live. Two rows are deliberately different: an org-lint or
     plan-lint recipe names the NEXT rule id (`O14`, `R17`), which by definition
     does not exist yet, so the live probe looks for its PREDECESSOR — the id the
     next rule must actually follow.
"""
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import _doc_sync as docsync  # noqa: E402

#: (human name, doc anchor token, owning file, token that must exist there).
EXTENSION_POINTS = [
    ("knowledge category", "CATEGORIES",
     "scripts/agentware_lib/knowledge/index/indexread.py", "CATEGORIES = ("),
    ("org lint rule (O14)", "O14",
     "scripts/agentware_lib/ops/orglint.py", "O13"),
    ("steering rule", "DSF_RULE_ID_RE",
     "scripts/agentware_lib/ops/steering.py", "DSF_RULE_ID_RE"),
    ("authored skill", "catalog/skills.json",
     "scripts/agentware_lib/features/skills.py", "SKILLS_CATALOG_PATH"),
    ("dream step", "DREAM_STEP_FUNCS",
     "scripts/agentware_lib/features/dreamsteps/registry.py", "DREAM_STEP_FUNCS"),
    ("fanout gate", "FANOUT_DEFAULT_GATES",
     "scripts/agentware_lib/features/fanoutmq/mqintegrate.py",
     "FANOUT_DEFAULT_GATES"),
    ("metric / derive_*", "derive_*",
     "scripts/agentware_lib/ops/derive.py", "def derive_"),
    ("MCP tool", "_mcp_tool_definitions",
     "scripts/agentware_lib/features/mcp.py", "_mcp_tool_definitions"),
    ("retrieval ranking signal", "recall_ranked",
     "scripts/agentware_lib/retrieval/skillpin.py", "def recall_ranked"),
    ("plan lint rule (R17)", "R17",
     "scripts/agentware_lib/ops/planlint.py", "R16"),
    ("fleet event field", "FLEET_TERMINAL_REQUIRED",
     "scripts/agentware_lib/ops/fleet/fleetshard.py", "FLEET_TERMINAL_REQUIRED"),
    ("CLI verb declaration", "__verbs__",
     "scripts/agentware_lib/verbs.py", "__verbs__"),
    ("knowledge-base migration step", "_KB_MIGRATIONS",
     "scripts/agentware_lib/features/migratecmd.py", "_KB_MIGRATIONS = ("),
    ("dashboard panel", "PANELS",
     "webui/src/panels/registry.ts", "export const PANELS"),
]

#: Shared-file chokepoints -> the token that must appear in the chokepoints
#: section. A registry NOT listed here is one a contributor collides on with no
#: warning, which is the whole failure this section exists to prevent.
CHOKEPOINTS = [
    ("module manifest", "catalog/modules.json"),
    ("parity manifest", "tests/fixtures/parity-manifest.json"),
    ("authored skill catalog", "catalog/skills.json"),
    ("parser golden pin", "EXPECTED_VERBS"),
    ("steering rule-count baseline", ".rule-count-baseline"),
    ("shrink-only debt ledgers", "-debt.json"),
    ("documented module maps", "_doc_sync.py"),
    ("merge-policy path list", "FANOUT_MERGE_UNION_PATHS"),
]

MITIGATION_WORDS = ("append-only", "regenerat", "idempotent", "one per line",
                    "one element per line", "shrink")

RECIPES_HEADING = "### (v) Recipes for the non-subcommand extension points"
CHOKEPOINTS_HEADING = "## 8. Shared-file chokepoints"

#: A doc module-table row key: the first column of a `| `agentware_lib.x` | …`
#: row. Deliberately NOT any prose mention — a symbol reference in a sentence is
#: not a claim that the module is documented.
ROW_RE = re.compile(r"\|\s*`(agentware_lib\.[A-Za-z0-9_.]+)`\s*\|")


def _section(text, heading, stop_needles=("\n## ",)):
    """The slice of `text` from a HEADING line to the earliest stop needle.

    Anchored on the heading itself rather than on a phrase, because every
    heading is also a table-of-contents entry: a phrase search finds the TOC
    first and returns a section that contains nothing.
    """
    i = text.find("\n" + heading)
    if i < 0:
        return ""
    rest = text[i + 1:]
    cut = len(rest)
    for needle in stop_needles:
        j = rest.find(needle, len(heading))
        if j != -1:
            cut = min(cut, j)
    return rest[:cut]


def _read(root, rel):
    with open(os.path.join(root, rel), encoding="utf-8") as handle:
        return handle.read()


def check_recipes(text):
    """A. one recipe per extension point, each carrying a Test:."""
    errors = []
    recipes = _section(text, RECIPES_HEADING, ("\n## ",))
    if not recipes:
        return ["A: could not locate the %r recipes section" % RECIPES_HEADING]
    n_test = len(re.findall(r"Test:", recipes))
    if n_test < len(EXTENSION_POINTS):
        errors.append("A: recipes section has only %d 'Test:' fields; need >= %d "
                      "(one per recipe)" % (n_test, len(EXTENSION_POINTS)))
    for name, anchor, _owner, _live in EXTENSION_POINTS:
        if anchor not in recipes:
            errors.append("A: NO recipe entry for '%s' (missing anchor %r)"
                          % (name, anchor))
    return errors


def check_chokepoints(text):
    """B. every shared registry listed, with a mitigation."""
    errors = []
    choke = _section(text, CHOKEPOINTS_HEADING, ("\n## ",))
    if not choke:
        return ["B: could not locate the %r section" % CHOKEPOINTS_HEADING]
    if not any(word in choke for word in MITIGATION_WORDS):
        errors.append("B: chokepoints section names no mitigation (expected one "
                      "of %s)" % (MITIGATION_WORDS,))
    for name, token in CHOKEPOINTS:
        if token not in choke:
            errors.append("B: chokepoint '%s' (%r) not listed with a mitigation"
                          % (name, token))
    return errors


def check_module_table(text, manifest):
    """C. the doc's module table has two-way parity with the manifest."""
    published = set(m["name"] for m in manifest["modules"])
    rows = set(ROW_RE.findall(text))
    errors = ["C: manifest module %r is NOT a row in the doc table" % name
              for name in sorted(published - rows)]
    errors.extend("C: doc table row %r is NOT in the manifest" % name
                  for name in sorted(rows - published))
    return errors


def check_router_table(root, manifest):
    """E. the router's rows are exactly the manifest's modules and packages."""
    published = set(m["name"] for m in manifest["modules"])
    claimed = set(docsync.router_rows(_read(root, docsync.README_REL)))
    errors = ["E: manifest module %r has no row in %s"
              % (name, docsync.README_REL)
              for name in sorted(published - claimed)]
    for name in sorted(claimed - published):
        if any(module.startswith(name + ".") for module in published):
            continue  # a PACKAGE seam the router documents above its leaves
        errors.append("E: %s row %r is neither a manifest module nor a package "
                      "holding one" % (docsync.README_REL, name))
    return errors


def check_anchors_resolve(root):
    """F. every recipe anchor still resolves in the tree it points at."""
    errors = []
    for name, anchor, owner, live in EXTENSION_POINTS:
        path = os.path.join(root, owner)
        if not os.path.exists(path):
            errors.append("F: '%s' names owner %s, which does not exist"
                          % (name, owner))
            continue
        if live not in _read(root, owner):
            errors.append("F: '%s' anchor %r is stale — %r is not in %s"
                          % (name, anchor, live, owner))
    return errors


def check_doc(doc_path, root=None):
    root = root or _REPO_ROOT
    text = _read(root, os.path.relpath(doc_path, root))
    with open(os.path.join(root, docsync.CATALOG_REL), encoding="utf-8") as fh:
        manifest = json.load(fh)
    errors = check_recipes(text)
    errors.extend(check_chokepoints(text))
    errors.extend(check_module_table(text, manifest))
    errors.extend("D: %s" % problem for problem in docsync.drift(root))
    errors.extend(check_router_table(root, manifest))
    errors.extend(check_anchors_resolve(root))
    return errors


def main(argv):
    docs = argv[1:] or [os.path.join(_REPO_ROOT, docsync.ARCH_REL)]
    ok = True
    for doc in docs:
        errors = check_doc(doc)
        if errors:
            ok = False
            sys.stderr.write("FAIL %s:\n" % doc)
            for error in errors:
                sys.stderr.write("  - %s\n" % error)
        else:
            sys.stdout.write(
                "OK %s: %d/%d extension-point recipes, all %d chokepoints "
                "mitigated, module-table + router parity with %s, every managed "
                "block in sync, every anchor resolves\n"
                % (doc, len(EXTENSION_POINTS), len(EXTENSION_POINTS),
                   len(CHOKEPOINTS), docsync.CATALOG_REL))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
