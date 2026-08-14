"""agentware_lib.ops.scaffoldcheck — the CI AUDIT over hand-written units (layer: ops).

Owns: `scaffold check` — the five wiring questions asked of the WHOLE tree rather
than of one generated unit: does every lib module have a manifest entry
(`check_manifest`), does every declared verb reach the manifest's published verb
list (`check_verbs`), does every module have its mirrored owning test
(`check_tests`), does every top-level verb carry a parity row (`check_parity`),
and does every module have its README router row (`check_docs`) — plus the two
shrink-only debt ledgers those last two honour, and the `audit` that runs them
all and the `FINDING KINDS` it reports.
Does NOT own: EMITTING a unit or its wiring -> ops.scaffoldunit renders and
ops.scaffoldwire writes; the argparse surface -> ops.scaffoldcmd; the gates
themselves — `tests/gates/test_module_manifest.py`, `test_test_ownership.py`,
`test_verb_discovery.py` and `test_parity_gate.py` remain the authorities, and
this audit is the same question asked from the CLI where CI (and an author
mid-change) can ask it without running the suite.
Layer deps: ops -> same-layer ops.scaffoldunit plus stdlib. All DOWNWARD.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

WHY AN AUDIT AND NOT JUST A GENERATOR
-------------------------------------
`scaffold new` only helps the author who runs it. The unit that arrives by hand —
copied from a neighbour, or split out of a file that hit its ceiling — is exactly
the one that lands with four of the five artifacts, and `R-MOD-11` says a unit
missing any of them is not landed. So the same contract is asked in the other
direction: given a tree, which units are incomplete? That question is answerable
in a second, from a clone, with no test runner, which is what makes it a CI step.

WHY IT READS PUBLISHED ARTIFACTS RATHER THAN RE-DERIVING THEM
-------------------------------------------------------------
Every input here is a COMMITTED ledger — `catalog/modules.json`, the parity
manifest, the two debt fixtures, the README table. Re-deriving any of them would
make this a second implementation of a generator the tree already gates for
byte-identity (see ops.scaffoldwire's docstring), and a second implementation is
a second answer. Reading them means a stale ledger shows up HERE as a missing
entry, which is the finding an author needs, rather than as agreement between two
copies of the same mistake.

WHY THE TWO DEBT LEDGERS EXIST
------------------------------
189 modules have no granular owning test and 82 have no README row — measured,
declared, and owned by named follow-up tasks (101 and 74). A check that failed on
all 271 would be switched off within a day, and a check that ignored them would
pass a NEW module with no test. So both are shrink-only ratchets, on the same
discipline the ceiling and modularity ratchets already carry: an undeclared gap
FAILS, and a declared row the tree no longer produces ALSO fails, so the file
only ever shrinks and can never rot into an allowlist.

Stdlib only, read-only, deterministic. Never touches the knowledge base.
"""

import json
import os
import re

import agentware_lib.ops.scaffoldunit as unitmod


PARITY_REL = "tests/fixtures/parity-manifest.json"
TEST_DEBT_REL = "tests/fixtures/test-ownership-debt.json"
DOC_DEBT_REL = "tests/fixtures/doc-row-debt.json"

#: A row in the README module-router table, keyed by the module it claims.
DOC_ROW_RE = re.compile(r"^\| `(agentware_lib\.[A-Za-z0-9_.]+)` \|", re.M)

#: Every finding this audit can report. Published so a CI job can filter by kind
#: and so the kinds are enumerable without reading the code.
FINDING_KINDS = (
    "manifest_entry_missing",
    "manifest_entry_stale",
    "manifest_verb_missing",
    "owning_test_missing",
    "parity_row_missing",
    "doc_row_missing",
    "debt_row_stale",
)


def _read_json(root, rel, default=None):
    try:
        with open(os.path.join(unitmod.repo_root(root), rel), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {} if default is None else default


def _finding(kind, location, detail):
    return {"kind": kind, "location": location, "detail": detail}


def lib_module_paths(root=None):
    """Every `scripts/agentware_lib/**/*.py` that is not a package `__init__`."""
    top = unitmod.repo_root(root)
    base = os.path.join(top, "scripts", "agentware_lib")
    out = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.endswith(".py") and name != "__init__.py":
                rel = os.path.relpath(os.path.join(dirpath, name), top)
                out.append(rel.replace(os.sep, "/"))
    return sorted(out)


def debt_locations(root, rel):
    """The `location` set a shrink-only debt fixture declares.

    The tree's debt fixtures (`ceiling-debt`, `modularity-debt`,
    `test-ownership-debt`, `doc-row-debt`) all carry rows nested inside GROUPS
    that hold the owner and the rationale, with a top-level `rows` array for
    ungrouped ones — so both are flattened here, exactly as
    `tests/_test_ownership.load_debt` flattens them. Reading only one of the two
    returns an EMPTY exemption set, which turns every declared row back into a
    finding; `test_scaffoldcheck` asserts this flattening agrees with that
    loader's on the committed fixture rather than trusting the duplication.
    """
    data = _read_json(root, rel)
    rows = list(data.get("rows") or [])
    for group in data.get("groups") or []:
        rows.extend(group.get("rows") or [])
    return {row.get("location") for row in rows if isinstance(row, dict)}


def check_manifest(root, manifest):
    """Every module on disk is claimed, and every claim resolves on disk."""
    entries = {e["path"]: e for e in manifest.get("modules", [])}
    on_disk = set(lib_module_paths(root))
    bad = [_finding("manifest_entry_missing", path,
                    "no catalog/modules.json entry: regenerate with "
                    "`python3 tests/_manifest_gen.py --write` (R-MOD-07)")
           for path in sorted(on_disk - set(entries))]
    bad.extend(_finding("manifest_entry_stale", path,
                        "catalog/modules.json claims a module that is not on "
                        "disk: regenerate the manifest")
               for path in sorted(set(entries) - on_disk))
    return bad


def declared_verbs():
    """`{(verb, module)}` from the LIVE `__verbs__` declarations.

    The one place this module reaches the running package. Injectable at every
    call site below so the checks are testable against a synthetic tree, which a
    package-global import would make impossible.
    """
    from agentware_lib import verbs as _verbs
    return {(verb, module) for verb, _o, module, _a in _verbs.discover()}


def check_verbs(root, manifest, declared=None):
    """Every verb the package DECLARES is published by the manifest, and vice versa.

    The two witnesses are deliberately different: discovery IMPORTS the modules,
    the manifest reads their AST. A declaration only one can see — built at
    runtime, or sitting in a module that fails to import — is the failure this
    catches.
    """
    published = set()
    for entry in manifest.get("modules", []):
        for row in entry.get("verbs") or []:
            published.add((row.get("verb"), entry["name"]))
    try:
        declared = declared_verbs() if declared is None else set(declared)
    except Exception as exc:  # pragma: no cover - a broken tree, reported not raised
        return [_finding("manifest_verb_missing", "agentware_lib",
                         "verb discovery failed: %s" % (exc,))]
    bad = [_finding("manifest_verb_missing", module,
                    "declares verb %r, which catalog/modules.json does not "
                    "publish: regenerate the manifest (R-MOD-07)" % verb)
           for verb, module in sorted(declared - published)]
    bad.extend(_finding("manifest_verb_missing", module,
                        "catalog/modules.json publishes verb %r that no live "
                        "`__verbs__` declares: regenerate the manifest" % verb)
               for verb, module in sorted(published - declared))
    return bad


def check_tests(root, manifest):
    """Every module has its mirrored owning unit file, or a declared debt row."""
    top = unitmod.repo_root(root)
    declared = debt_locations(root, TEST_DEBT_REL)
    bad = []
    for entry in manifest.get("modules", []):
        rel = entry.get("test_file")
        if not rel or os.path.exists(os.path.join(top, rel)):
            continue
        if entry["path"] in declared:
            continue
        bad.append(_finding(
            "owning_test_missing", entry["path"],
            "no owning unit file at %s (R-MOD-06): write it, or scaffold the "
            "unit whole with `agentware scaffold new`" % rel))
    return bad


def check_parity(root, declared=None):
    """Every top-level verb the parser serves carries a parity-manifest row.

    A verb inherited from either pre-merge tree has a GENERATED `surface:<verb>`
    row derived from the union fixture. A verb added AFTER the merge is in no
    fixture, so it can only be covered by a curated row — which is exactly the
    row `R-MOD-08` obliges and `scaffold new --kind verb` emits.
    """
    rows = _read_json(root, PARITY_REL).get("rows", [])
    if not rows:
        return [_finding("parity_row_missing", PARITY_REL,
                         "no parity manifest on disk: regenerate with "
                         "`python3 tests/_parity_manifest.py --out`")]
    known = {row.get("id") for row in rows}
    try:
        live = sorted({verb for verb, _module in
                       (declared_verbs() if declared is None else set(declared))})
    except Exception as exc:  # pragma: no cover - reported, not raised
        return [_finding("parity_row_missing", "agentware_lib",
                         "verb discovery failed: %s" % (exc,))]
    return [_finding("parity_row_missing", verb,
                     "no parity row `surface:%s`: declare a curated row in "
                     "tests/_parity_curated.py and regenerate with "
                     "`python3 tests/_parity_manifest.py --out` (R-MOD-08)" % verb)
            for verb in live if "surface:%s" % verb not in known]


def doc_rows(root=None):
    """The module names the README module-router table claims."""
    path = os.path.join(unitmod.repo_root(root), unitmod.README_REL)
    try:
        with open(path, encoding="utf-8") as fh:
            return set(DOC_ROW_RE.findall(fh.read()))
    except OSError:
        return set()


def check_docs(root, manifest):
    """Every module has a README router row, or a declared (shrink-only) debt row."""
    rows = doc_rows(root)
    declared = debt_locations(root, DOC_DEBT_REL)
    bad = []
    live_missing = set()
    for entry in manifest.get("modules", []):
        if entry["name"] in rows:
            continue
        live_missing.add(entry["path"])
        if entry["path"] in declared:
            continue
        bad.append(_finding(
            "doc_row_missing", entry["path"],
            "no row in %s for %s: add one, or scaffold the unit whole"
            % (unitmod.README_REL, entry["name"])))
    bad.extend(_finding("debt_row_stale", loc,
                        "%s declares a doc-row debt this tree no longer "
                        "produces: delete the row, the ratchet only shrinks"
                        % DOC_DEBT_REL)
               for loc in sorted(declared - live_missing))
    return bad


def audit(root=None, declared=None):
    """Run every check. Returns a JSON-able record; `ok` is the CI exit condition."""
    manifest = _read_json(root, unitmod.CATALOG_REL)
    findings = []
    if not manifest.get("modules"):
        findings.append(_finding(
            "manifest_entry_missing", unitmod.CATALOG_REL,
            "no module manifest on disk: regenerate with "
            "`python3 tests/_manifest_gen.py --write`"))
    else:
        findings.extend(check_manifest(root, manifest))
        findings.extend(check_verbs(root, manifest, declared))
        findings.extend(check_tests(root, manifest))
        findings.extend(check_docs(root, manifest))
    findings.extend(check_parity(root, declared))
    counts = dict((kind, 0) for kind in FINDING_KINDS)
    for item in findings:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {"ok": not findings, "modules": len(manifest.get("modules", [])),
            "findings": findings, "counts": counts}


_EXPORTS = [
    "FINDING_KINDS",
    "declared_verbs",
    "lib_module_paths",
    "debt_locations",
    "doc_rows",
    "check_manifest",
    "check_verbs",
    "check_tests",
    "check_parity",
    "check_docs",
    "audit",
]
