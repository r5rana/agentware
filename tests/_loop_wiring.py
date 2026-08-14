"""The LOOP WIRING gate — nothing lost when the orchestrator was split.

Plan task 33 replaces a 1961-line `agentware.sh` with a <=250-line orchestrator
plus `scripts/loop/*.sh`. Two failure modes matter, and neither reddens anything
else in the tree:

  1. A capability's DEFINITION moves to a lib but its CALL does not move with it.
     The function exists, `bash -n` is clean, every unit test that imports the lib
     passes — and the gate simply never runs. This is the same fail-open shape as
     `learnings_promoted()` returning PASS on a non-executable entrypoint and
     `run_selfheal_review` returning PROCEED when DOCS_DIR and KDIR disagree.

  2. A file drifts back over the modularity ceiling, and the split quietly
     re-monolithises one lib at a time.

So this gate counts, per capability, the number of places it is actually INVOKED
across the orchestrator and every loop lib, and compares that to what
`aw-pre-merge` had. `tests/_loop_callsites.py` is the single definition of what
counts as a call (command position, `trap` handlers, no prose) — see its module
docstring for why a naive grep scores `log` at 68 by counting English.

## Direction of the ratchet

Per capability the assertion is `live >= premerge`, not `live == premerge`. The
split is allowed to ADD wiring — this task adds three call sites that did not
exist before (two org-lint, one retrieval-quality) — but it may never DROP any.
Equality would redden on exactly the change the plan asks for, which is how a
gate ends up being edited to match the tree instead of the tree being edited to
match the gate.

The recorded `premerge` numbers are ALSO cross-checked against
`git show aw-pre-merge:agentware.sh` whenever git is available, so the baseline
cannot be quietly lowered to make a live regression pass. That check is skipped,
not failed, when git or the ref is unavailable (a fresh clone, a shallow CI
checkout) — a missing oracle must not turn into a false accusation.

## The injected donor gates

`run_org_lint_gate` and `run_retrieval_quality_gate` have no `aw-pre-merge`
baseline at all — they are donor capabilities this tree never had — so a
`>= premerge` rule would be vacuously satisfied by zero. They carry an explicit
`min` instead: the plan requires at least 2 org-lint call sites (pre-hook AND
post-hook; one alone leaves half the run unchecked) and at least 1
retrieval-quality call site.

Stdlib only, read-only, deterministic, py3.7-safe.
"""

import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)

from _loop_callsites import count_call_sites, shell_defs  # noqa: E402

REPO_ROOT = os.environ.get("AGENTWARE_WIRING_ROOT") or os.path.dirname(HERE)
SCHEMA = "agentware/loop-wiring@1"
DEFAULT_FIXTURE = os.path.join(HERE, "fixtures", "loop-wiring.json")

ORCHESTRATOR = "agentware.sh"
LIB_GLOB = os.path.join("scripts", "loop", "*.sh")
# Scripts held to the 250-line orchestrator/loop/hook ceiling (plan "Ceilings").
CAPPED_GLOBS = [ORCHESTRATOR, LIB_GLOB, os.path.join("scripts", "hooks", "*")]
SCRIPT_LINE_CAP = 250


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def loop_sources(root=REPO_ROOT):
    """Return {relpath: text} for the orchestrator and every loop lib."""
    out = {}
    orch = os.path.join(root, ORCHESTRATOR)
    if os.path.isfile(orch):
        out[ORCHESTRATOR] = _read(orch)
    for path in sorted(glob.glob(os.path.join(root, LIB_GLOB))):
        out[os.path.relpath(path, root)] = _read(path)
    return out


def total_call_sites(sources, name):
    """Sum a capability's call sites across every loop source file."""
    return sum(count_call_sites(text, name) for text in sources.values())


def defined_in(sources, name):
    """Relpaths of the files that DEFINE `name` (should be exactly one)."""
    return [rel for rel, text in sources.items() if name in shell_defs(text)]


def premerge_counts(ref="aw-pre-merge", root=REPO_ROOT):
    """Call-site counts at the pre-merge orchestrator, or None if unavailable."""
    try:
        text = subprocess.check_output(
            ["git", "show", "%s:%s" % (ref, ORCHESTRATOR)],
            cwd=root, stderr=subprocess.DEVNULL,
        ).decode("utf-8", "replace")
    except Exception:
        return None
    return {name: count_call_sites(text, name) for name in shell_defs(text)}


def capped_scripts(root=REPO_ROOT):
    """Return sorted (relpath, line_count) for every ceiling-capped script."""
    seen = {}
    for pattern in CAPPED_GLOBS:
        for path in glob.glob(os.path.join(root, pattern)):
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, root)
            if os.path.basename(rel).startswith("."):
                continue
            seen[rel] = len(_read(path).splitlines())
    return sorted(seen.items())


def evaluate(fixture, root=REPO_ROOT):
    """Return (violations, checklist). Empty violations == the gate passes."""
    violations = []
    checklist = []
    sources = loop_sources(root)

    if ORCHESTRATOR not in sources:
        return ["orchestrator %s not found under %s" % (ORCHESTRATOR, root)], []

    # --- 1. every pre-merge capability still reachable, and defined exactly once
    live_baseline = premerge_counts(fixture.get("premerge_ref", "aw-pre-merge"), root)
    for row in fixture.get("capabilities", []):
        name = row["name"]
        want = int(row["premerge"])
        got = total_call_sites(sources, name)
        where = defined_in(sources, name)
        checklist.append({
            "capability": name, "origin": row.get("origin", "local"),
            "premerge": want, "live": got,
            "defined_in": where[0] if len(where) == 1 else where,
        })
        if got < want:
            violations.append(
                "WIRING LOST: %s is called %d time(s) in the split tree but %d at "
                "%s — a call site did not move with its definition"
                % (name, got, want, fixture.get("premerge_ref", "aw-pre-merge")))
        if len(where) != 1:
            violations.append(
                "DEFINITION: %s is defined in %d file(s) (%s); expected exactly one"
                % (name, len(where), ", ".join(where) or "none"))
        # Cross-check the frozen baseline against git so it cannot be lowered.
        if live_baseline is not None and name in live_baseline:
            if live_baseline[name] != want:
                violations.append(
                    "BASELINE DRIFT: fixture records premerge=%d for %s but "
                    "%s:%s actually has %d — the frozen baseline was edited"
                    % (want, name, fixture.get("premerge_ref", "aw-pre-merge"),
                       ORCHESTRATOR, live_baseline[name]))

    # A capability present at pre-merge but absent from the fixture is a silent
    # drop: the checklist would simply never mention it.
    if live_baseline is not None:
        recorded = {r["name"] for r in fixture.get("capabilities", [])}
        for name in sorted(set(live_baseline) - recorded):
            violations.append(
                "UNCLAIMED: %s existed at %s but carries no checklist row"
                % (name, fixture.get("premerge_ref", "aw-pre-merge")))

    # --- 2. the injected donor gates, which have no pre-merge baseline ---------
    for row in fixture.get("injected", []):
        name = row["name"]
        need = int(row["min"])
        got = total_call_sites(sources, name)
        where = defined_in(sources, name)
        checklist.append({
            "capability": name, "origin": row.get("origin", "donor"),
            "premerge": 0, "live": got, "min": need,
            "defined_in": where[0] if len(where) == 1 else where,
        })
        if got < need:
            violations.append(
                "INJECTED WIRING MISSING: %s has %d call site(s), needs >= %d (%s)"
                % (name, got, need, row.get("why", "")))
        if len(where) != 1:
            violations.append(
                "DEFINITION: %s is defined in %d file(s) (%s); expected exactly one"
                % (name, len(where), ", ".join(where) or "none"))
        # The plan requires these live in NEW libs, not in the already-large hooks
        # lib nor the orchestrator.
        if len(where) == 1 and where[0] in (ORCHESTRATOR, os.path.join("scripts", "loop", "hooks.sh")):
            violations.append(
                "PLACEMENT: %s must be defined in a NEW loop lib, not %s"
                % (name, where[0]))

    # --- 3. the 250-line ceiling over orchestrator / loop / hook scripts -------
    debt = {r["path"]: int(r["lines"]) for r in fixture.get("script_cap_debt", [])}
    seen_debt = set()
    for rel, lines in capped_scripts(root):
        if lines <= SCRIPT_LINE_CAP:
            if rel in debt:
                violations.append(
                    "STALE DEBT: %s is %d lines (within the %d cap) but still "
                    "carries a debt row — remove it (the fixture is shrink-only)"
                    % (rel, lines, SCRIPT_LINE_CAP))
            continue
        if rel in debt:
            seen_debt.add(rel)
            if lines > debt[rel]:
                violations.append(
                    "CEILING REGRESSION: %s grew to %d lines, worse than its "
                    "declared debt of %d" % (rel, lines, debt[rel]))
        else:
            violations.append(
                "CEILING: %s is %d lines, over the %d-line orchestrator/loop/hook "
                "cap and undeclared — split it" % (rel, lines, SCRIPT_LINE_CAP))
    for rel in sorted(set(debt) - seen_debt):
        if not os.path.isfile(os.path.join(root, rel)):
            violations.append(
                "STALE DEBT: %s no longer exists but still carries a debt row" % rel)

    return violations, checklist


def load_fixture(path=DEFAULT_FIXTURE):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("schema") != SCHEMA:
        raise ValueError("unexpected fixture schema: %r" % data.get("schema"))
    return data


def main(argv):
    fixture_path = DEFAULT_FIXTURE
    as_json = "--format=json" in argv
    if "--fixture" in argv:
        fixture_path = argv[argv.index("--fixture") + 1]

    fixture = load_fixture(fixture_path)
    violations, checklist = evaluate(fixture)

    if as_json:
        print(json.dumps({"ok": not violations, "violations": violations,
                          "checklist": checklist}, indent=2, sort_keys=True))
    else:
        print("---- loop wiring checklist (orchestrator + scripts/loop/*.sh) ----")
        for row in checklist:
            mark = "ok " if row["live"] >= row.get("min", row["premerge"]) else "LOST"
            print("  [%s] %-28s pre-merge=%-3s live=%-3s  %s"
                  % (mark, row["capability"], row["premerge"], row["live"],
                     row["defined_in"]))
        print("  %d capabilities checked" % len(checklist))
        for rel, lines in capped_scripts():
            if lines > SCRIPT_LINE_CAP:
                print("  [debt] %-44s %d lines (cap %d)" % (rel, lines, SCRIPT_LINE_CAP))
        if violations:
            print("\n[FAIL] loop wiring gate")
            for v in violations:
                print("  - %s" % v)
        else:
            print("\n[OK] loop wiring gate")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
