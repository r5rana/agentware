#!/usr/bin/env python3
"""Runnable R-DATA-01 gate for the worked add-a-feature walkthrough (task 75).

Underscore-prefixed so `unittest discover` never COLLECTS it — the same
convention `tests/_doc_sync.py` and `tests/_doc_extensibility_check.py` carry.
Stdlib-only, py3.7-safe, read-only, runnable directly and re-targetable:

    python3 tests/_walkthrough_check.py [<doc.md> ...]

Default target: `docs/add-a-feature.md`, plus `docs/architecture.md` for arm D.

WHY THIS EXISTS, and why it is not the e2e
------------------------------------------
The plan's acceptance criteria for the walkthrough are two claims, and they fail
in different ways:

  * *every command executes and succeeds* — a BEHAVIOURAL claim. Only running it
    can prove that, which is `tests/e2e/test_add_a_feature_walkthrough.py`: it
    copies the tree, applies every block in document order and reads every exit
    code.
  * *every gate the troubleshooting section names exists by that name in the
    tree* — a STRUCTURAL claim, and the failure mode is quiet. A troubleshooting
    entry for a gate that does not exist costs a reader more than no entry at
    all: they go looking for a file, find nothing, and stop trusting the page.
    That is what this module gates, in a second, with no scratch tree.

The two are deliberately separate so the structural arms stay fast enough to run
in the tight loop while the e2e pays for a real checkout.

Arms, each exiting non-zero and naming every offender:

  A. **Runnable-block resolution.** Every command in a ```bash block resolves:
     a `python3 tests/_x.py` names a file that exists (or one the walkthrough
     itself creates), a `scripts/agentware <verb>` names a LIVE verb from
     `agentware_lib.verbs.discover()`, and a `python3 -m unittest <dotted>`
     names a module that resolves to a real path under `tests/`.
  B. **Named-gate resolution.** Every gate named in the troubleshooting tables
     resolves: a `tests/**.py` path exists, a `scripts/agentware <verb>` is a
     live verb.
  C. **Coverage and shape.** Every gate in `REQUIRED_GATES` — the gates
     `architecture.md` §9 publishes plus the ones this walkthrough's own run has
     actually tripped — carries a troubleshooting row, and every row has all
     four columns filled (gate, message, meaning, fix). A row missing its `fix`
     column is a rotting entry, not a shorter one.
  D. **No ghost commands, across BOTH docs.** Every `python3 tests/_*.py`
     command any checked document names must resolve to a real file. This arm
     was written because `architecture.md` §7(iv) instructed contributors to run
     two checkers this tree has never had (`_ast_forward_edges.py`,
     `_global_resolve.py`): ported prose keeps its pointers, and the pointers are
     wrong (R-FAIL-08).
  E. **Block-convention integrity.** Every fenced block carries a known info
     string; every `path=` names a file that exists or that the walkthrough
     creates; every `norun:` block states a reason drawn from the declared
     allowlist, so "not executed" can never quietly grow into "nothing is".
"""
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_HERE)

WALKTHROUGH = "docs/add-a-feature.md"
ARCHITECTURE = "docs/architecture.md"

TROUBLESHOOTING_HEADING = "## 4. Troubleshooting"

#: Gates a contributor can trip on a verb-or-module change, each with the path
#: that must exist. Curated rather than globbed: `tests/gates/` holds ~50 files
#: and most of them (the donor-staging gates, the preserved-freeze internals)
#: cannot reject an ordinary contribution, so globbing would demand rows nobody
#: can write usefully. Every entry below either appears in `architecture.md` §9
#: or reddened live while the walkthrough was being written.
REQUIRED_GATES = [
    "tests/gates/test_module_manifest.py",
    "tests/gates/test_module_ceilings.py",
    "tests/gates/test_modularity_rules.py",
    "tests/gates/test_modularity_negative.py",
    "tests/gates/test_test_ownership.py",
    "tests/gates/test_test_layout.py",
    "tests/gates/test_verb_discovery.py",
    "tests/gates/test_parity_gate.py",
    "tests/gates/test_parity_manifest.py",
    "tests/gates/test_parser_golden.py",
    "tests/gates/test_constant_differ.py",
    "tests/gates/test_doc_architecture.py",
    "tests/gates/test_doc_walkthrough.py",
    "tests/gates/test_markdown_links.py",
    "tests/gates/test_portability_gate.py",
    "tests/gates/test_preserved_surface.py",
    "tests/gates/test_suite_delta.py",
    "tests/gates/test_invariants.py",
    "tests/e2e/test_org_local_writers_e2e.py",
]

#: Verb-shaped gates: `scaffold check` and the R-PKG chain are gates too, and a
#: troubleshooting section that skips them sends a reader to a test file for a
#: failure that came out of the CLI.
REQUIRED_VERB_GATES = ["scaffold", "steering", "gate"]

#: The only reasons a block may be documented but NOT executed by the e2e. A
#: free-text reason would let the unexecuted set grow silently, which is exactly
#: the property the executable-document design is buying.
ALLOWED_NORUN = {
    "the-suite-cannot-run-itself":
        "a tier-scoped discover run inside the suite re-enters the suite",
    "mutates-recorded-baselines-and-reads-the-live-knowledge-dir":
        "the R-PKG chain records eval baselines and resolves the operator's KB",
}

FENCE_RE = re.compile(r"^```(.*)$")
#: A markdown table row's cells, minus the leading/trailing pipe.
ROW_RE = re.compile(r"^\|(.+)\|\s*$")
CODE_RE = re.compile(r"`([^`]+)`")
TESTS_SCRIPT_RE = re.compile(r"python3\s+(tests/_[A-Za-z0-9_]+\.py)")
CLI_VERB_RE = re.compile(r"scripts/agentware\s+([a-z][a-z-]*)")
UNITTEST_MOD_RE = re.compile(r"python3\s+-m\s+unittest\s+([\w.]+(?:\s+[\w.]+)*)")
SCAFFOLDED_VERB_RE = re.compile(r"scaffold\s+new\b.*?--verb\s+([a-z][a-z-]*)")
GATE_PATH_RE = re.compile(r"tests/(?:gates|e2e|unit|integration)/[A-Za-z0-9_/]+\.py")


def read(root, rel):
    path = os.path.join(root, rel)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def live_verbs(root):
    """The verb surface as the PACKAGE reports it, not as a fixture claims it.

    Imported rather than parsed: a pin can be stale, and a walkthrough that
    names a verb the tree no longer serves is the exact failure arm B exists for.
    """
    scripts = os.path.join(root, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        from agentware_lib import verbs as vb
    except ImportError:                                  # pragma: no cover
        return set()
    return set(name for name, _o, _m, _a in vb.discover())


def blocks(text):
    """Every fenced block as (info_string, body_lines, opening_line_number)."""
    found, info, buf, start = [], None, [], 0
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(line)
        if match and info is None:
            info, buf, start = match.group(1).strip(), [], number
        elif match:
            found.append((info, buf, start))
            info = None
        elif info is not None:
            buf.append(line)
    return found


def classify(info):
    """(kind, argument) for a fence info string.

    kinds: run · run-fail · norun · write · edit · prose.
    """
    if info == "bash":
        return ("run", "")
    if info == "bash expect-fail":
        return ("run-fail", "")
    if info.startswith("bash norun:"):
        return ("norun", info[len("bash norun:"):])
    if info.startswith("python path=") or info.startswith("text path="):
        return ("write", info.split("path=", 1)[1])
    if info.startswith("diff path="):
        return ("edit", info.split("path=", 1)[1])
    return ("prose", info)


def created_paths(text):
    """Files the walkthrough itself writes — resolvable even if absent today."""
    made = set()
    for info, _body, _line in blocks(text):
        kind, arg = classify(info)
        if kind == "write":
            made.add(arg)
    return made


def created_verbs(text):
    """Verbs the walkthrough scaffolds, which the LIVE package rightly lacks.

    The worked example adds `greet` and never lands it, so arm A must resolve it
    against the document's own `scaffold new --verb` line rather than against
    `discover()`. Read from the command itself, not from a constant: a checker
    carrying its own copy of the example's name would keep passing after the
    example was renamed.
    """
    made = set()
    for info, body, _line in blocks(text):
        kind, _arg = classify(info)
        if kind not in ("run", "run-fail"):
            continue
        for command in command_lines(body):
            made.update(SCAFFOLDED_VERB_RE.findall(command))
    return made


def command_lines(body):
    """Shell lines worth resolving: no comments, no continuations, no blanks."""
    out = []
    for line in body:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _module_path(dotted):
    return os.path.join(*dotted.split(".")) + ".py"


def check_runnable(root, text, made):
    """Arm A — every command in an executed block names something that exists."""
    problems = []
    verbs = live_verbs(root) | created_verbs(text)
    for info, body, line in blocks(text):
        kind, _arg = classify(info)
        if kind not in ("run", "run-fail"):
            continue
        for command in command_lines(body):
            for rel in TESTS_SCRIPT_RE.findall(command):
                if not os.path.exists(os.path.join(root, rel)):
                    problems.append("A:%d: runnable block names a missing "
                                    "script %s" % (line, rel))
            for verb in CLI_VERB_RE.findall(command):
                if verb not in verbs:
                    problems.append("A:%d: runnable block names verb `%s`, "
                                    "which the package does not serve"
                                    % (line, verb))
            for group in UNITTEST_MOD_RE.findall(command):
                for dotted in group.split():
                    rel = _module_path(dotted)
                    if rel in made or os.path.exists(os.path.join(root, rel)):
                        continue
                    problems.append("A:%d: runnable block names test module "
                                    "%s, which resolves to no file"
                                    % (line, dotted))
    return problems


def _section(text, heading, stop="\n---"):
    """The slice from HEADING to the next stop needle.

    Anchored on the heading line rather than a phrase, because every heading is
    also a contents entry and a phrase search finds the contents first.
    """
    start = text.find("\n" + heading)
    if start < 0:
        return ""
    tail = text[start + 1:]
    end = tail.find(stop)
    return tail if end < 0 else tail[:end]


def troubleshooting_rows(text):
    """Every four-column table row in the troubleshooting section."""
    rows = []
    section = _section(text, TROUBLESHOOTING_HEADING)
    for line in section.splitlines():
        match = ROW_RE.match(line)
        if not match:
            continue
        cells = [c.strip() for c in match.group(1).split("|")]
        if len(cells) != 4 or set(cells[0]) <= set("-: "):
            continue
        if cells[0] in ("Gate",):
            continue
        rows.append(cells)
    return rows


def check_gate_resolution(root, text):
    """Arm B — every gate, checker and verb a troubleshooting row NAMES exists.

    Split from the coverage arm at its branch ceiling, and the seam is real: this
    asks whether each pointer the page makes RESOLVES, while `check_gate_coverage`
    asks whether the page points at everything it must. One rots when the tree
    moves; the other rots when the tree grows.
    """
    problems = []
    verbs = live_verbs(root)
    for cells in troubleshooting_rows(text):
        gate = cells[0]
        for path in GATE_PATH_RE.findall(gate):
            if not os.path.exists(os.path.join(root, path)):
                problems.append("B: troubleshooting names a gate that does not "
                                "exist: %s" % path)
        for verb in CLI_VERB_RE.findall(gate):
            if verb not in verbs:
                problems.append("B: troubleshooting names verb `%s`, which the "
                                "package does not serve" % verb)
        for script in TESTS_SCRIPT_RE.findall(gate):
            if not os.path.exists(os.path.join(root, script)):
                problems.append("B: troubleshooting names a checker that does "
                                "not exist: %s" % script)
    return problems


def check_gate_coverage(_root, text):
    """Arm C — every required gate has a row, and every row is fully filled in."""
    problems = []
    rows = troubleshooting_rows(text)
    named_paths, named_verbs = set(), set()
    for cells in rows:
        gate = cells[0]
        named_paths.update(GATE_PATH_RE.findall(gate))
        named_verbs.update(CLI_VERB_RE.findall(gate))
        for index, name in ((1, "message"), (2, "meaning"), (3, "fix")):
            if len(cells[index]) < 8:
                problems.append("C: troubleshooting row for %s has an empty or "
                                "stub %s column" % (gate, name))
    for path in REQUIRED_GATES:
        if path not in named_paths:
            problems.append("C: no troubleshooting row for %s" % path)
    for verb in REQUIRED_VERB_GATES:
        if verb not in named_verbs:
            problems.append("C: no troubleshooting row for the `agentware %s` "
                            "gate" % verb)
    if not rows:
        problems.append("C: the troubleshooting section has no rows at all")
    return problems


def check_no_ghost_commands(root, rel, text):
    """Arm D — a `python3 tests/_x.py` any doc names must exist."""
    problems = []
    for number, line in enumerate(text.splitlines(), start=1):
        for script in TESTS_SCRIPT_RE.findall(line):
            if not os.path.exists(os.path.join(root, script)):
                problems.append("D: %s:%d names `%s`, which does not exist in "
                                "this tree" % (rel, number, script))
    return problems


def check_conventions(root, text, made):
    """Arm E — the executable-document conventions hold."""
    problems, norun, expect_fail = [], 0, 0
    for info, _body, line in blocks(text):
        kind, arg = classify(info)
        if kind == "prose":
            if info.startswith("bash") or "path=" in info:
                problems.append("E:%d: unrecognised block info string %r"
                                % (line, info))
            continue
        if kind == "norun":
            norun += 1
            if arg not in ALLOWED_NORUN:
                problems.append("E:%d: norun reason %r is not in the declared "
                                "allowlist" % (line, arg))
        if kind == "run-fail":
            expect_fail += 1
        if kind in ("write", "edit"):
            if not (arg in made or os.path.exists(os.path.join(root, arg))):
                problems.append("E:%d: block targets %s, which neither exists "
                                "nor is created by this document" % (line, arg))
    if not expect_fail:
        problems.append("E: no `bash expect-fail` block — the mutation proof is "
                        "the one step whose evidence is a RED run")
    if norun > len(ALLOWED_NORUN):
        problems.append("E: %d norun blocks for %d allowed reasons"
                        % (norun, len(ALLOWED_NORUN)))
    return problems


def check(root=_REPO_ROOT, targets=None):
    """Every arm over `targets` (default: the walkthrough + architecture)."""
    targets = list(targets or [WALKTHROUGH, ARCHITECTURE])
    problems = []
    for rel in targets:
        text = read(root, rel)
        if text is None:
            problems.append("missing document: %s" % rel)
            continue
        problems.extend(check_no_ghost_commands(root, rel, text))
        if not rel.endswith(os.path.basename(WALKTHROUGH)):
            continue
        made = created_paths(text)
        problems.extend(check_runnable(root, text, made))
        problems.extend(check_gate_resolution(root, text))
        problems.extend(check_gate_coverage(root, text))
        problems.extend(check_conventions(root, text, made))
    return problems


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    problems = check(_REPO_ROOT, argv or None)
    if problems:
        sys.stderr.write("FAIL %s:\n" % os.path.join(_REPO_ROOT, WALKTHROUGH))
        for problem in problems:
            sys.stderr.write("  - %s\n" % problem)
        return 1
    rows = troubleshooting_rows(read(_REPO_ROOT, WALKTHROUGH) or "")
    print("OK %s: %d troubleshooting row(s), every named gate resolves, every "
          "runnable command names a live target"
          % (os.path.join(_REPO_ROOT, WALKTHROUGH), len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
