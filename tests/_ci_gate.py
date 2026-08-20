#!/usr/bin/env python3
"""THE CI ENTRYPOINT -- one command that runs every gate CI runs, in CI's order
(feature 260813-modular-core-adoption, task 78).

    python3 tests/_ci_gate.py                      # everything CI runs
    python3 tests/_ci_gate.py --profile bootstrap  # "does this clone work?"
    python3 tests/_ci_gate.py --stage portability  # one stage
    python3 tests/_ci_gate.py --list               # the declared stage table

WHY AN ENTRYPOINT AND NOT A YAML SCRIPT
---------------------------------------
Task 78's acceptance criterion is *"the workflow runs green via the same
entrypoint locally"*, and the only way to make that mechanically true rather
than aspirational is for the workflow to contain no gate commands at all. A
`.github/workflows/*.yml` that inlines `python3 -m unittest discover ...` is a
second, unversioned definition of "the checks", runnable only inside GitHub, and
it drifts the first time a gate is added: the contributor updates CONTRIBUTING.md
and forgets the YAML, or updates the YAML and nobody can reproduce the red
locally. So the stage table lives HERE, in Python, under the same review as the
gates it runs, and `.github/workflows/ci.yml` calls exactly this file.
`tests/gates/test_ci_workflow.py` arm B enforces that -- it FAILS if any `run:`
line in the workflow names a gate command other than this entrypoint.

WHY A STAGE IS A DECLARED ROW AND NOT A FUNCTION
------------------------------------------------
Each stage names the shell command an operator would type, so the CI log and the
local terminal print the same line, and a red stage is reproduced by copying one
line out of the output. Nothing here wraps a gate in a Python API that could
diverge from the command a contributor is told to run.

THE VACUOUS-COMMAND PROBLEM THIS TABLE EXISTS TO AVOID
------------------------------------------------------
Task 78 found that three of the five commands CONTRIBUTING.md documented as "the
structural checkers" measured NOTHING: `tests/_ceiling_gate.py` and
`tests/_modularity_rules.py` had no `if __name__` dispatch, so running them
imported a module and exited 0; `tests/_manifest_gen.py --check` ignored the flag
and dumped the manifest to stdout, also exiting 0. Wiring those into CI as they
stood would have produced a workflow that was green by construction. They were
fixed in the same change, and `test_ci_workflow.py` arm D now proves every
harness a stage names has a real `__main__` dispatch AND that the ratchet
commands go red on a planted defect -- because "the gate ran" and "the gate could
have failed" are different claims and only the second one is worth anything.

WHY THE SUITE IS FOUR RUNS
---------------------------
`unittest discover -s tests` over the whole tree collects `tests/fixtures/`
(fixture DATA, including SWE task repos carrying their own graders) and
INTERMITTENTLY HANGS -- observed repeatedly, each time stalling 40+ minutes at
0.0% CPU with a static log, which is the worst shape a CI step can take because a
hang is indistinguishable from a slow pass until someone checks CPU. The four
tier-scoped runs collect the same corpus and none of the fixture data
(`tests/README.md`). Children also inherit `AGENTWARE_NESTED_UNITTEST=1` so no
verb under test re-spawns the suite inside itself.

Read-only with respect to the repository. No network. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO_ROOT = os.path.dirname(HERE)

#: The profiles a stage can belong to. `full` is what CI and a contributor run;
#: `bootstrap` is the fast subset that answers only "is this clone WORKING?" and
#: is what a fresh clone runs (see `tests/_bootstrap_gate.py`). Keeping the
#: bootstrap set small is not an optimisation -- it is what stops the fresh-clone
#: stage from re-entering the full gate inside the clone it just made.
FULL = "full"
BOOTSTRAP = "bootstrap"
PROFILES = (FULL, BOOTSTRAP)

#: The tiers of the test corpus, in the order CI runs them: cheapest and most
#: localising first, so a broken build fails in two minutes rather than ten.
SUITE_TIERS = ("unit", "gates", "integration", "e2e")


def _stage(sid, title, argv, profiles, why):
    """One CI stage. `argv` is the command an operator would type, verbatim."""
    return {"id": sid, "title": title, "argv": list(argv),
            "profiles": tuple(profiles), "why": why}


def _suite_stages():
    """The full suite as four tier-scoped runs -- never a whole-tree discover.

    Each tier goes through `tests/_suite_gate.py` rather than straight to
    `unittest`, because this repository's definition of green is "no worse than
    the recorded baseline", never "all pass" (`tests/baselines/README.md`, and
    the plan's Constraints). Reading unittest's exit code implements the other
    definition and would make CI red on day one for the one declared
    pre-existing failure that plan task 89 already owns. The ledger it applies
    is shrink-only and bites in both directions.
    """
    return [
        _stage("suite-" + tier, "test suite: %s tier" % tier,
               ("python3", "tests/_suite_gate.py", "--tier", tier), (FULL,),
               "the %s tier of the corpus, gated against the declared "
               "known-failure ledger; four tier-scoped runs rather than one "
               "whole-tree discover, which collects tests/fixtures/ and "
               "intermittently hangs" % tier)
        for tier in SUITE_TIERS
    ]


#: THE STAGE TABLE. Order is execution order. Every row is a command; nothing is
#: a Python call, so the CI log and a local terminal print the same thing.
STAGES = tuple(
    [
        _stage("steering-lint", "steering lint (--strict)",
               ("scripts/agentware", "steering", "lint", "--strict"),
               (FULL, BOOTSTRAP),
               "R-PKG-04: every steering rule is atomic, id'd and within the "
               "character budget. Runs first because it is seconds and a "
               "malformed rule file breaks every agent session."),
        _stage("module-manifest", "module manifest is current",
               ("python3", "tests/_manifest_gen.py", "--check"),
               (FULL, BOOTSTRAP),
               "R-MOD-07: catalog/modules.json is byte-identical to a "
               "regeneration, so a file cannot be added without being claimed."),
        _stage("ceilings", "modularity ratchet: ceilings",
               ("python3", "tests/_ceiling_gate.py"), (FULL, BOOTSTRAP),
               "R-MOD-03/04: no file over its layer cap, no function over 120 "
               "lines or 12 branches, residual declared and shrink-only."),
        _stage("layer-direction", "modularity ratchet: layer direction",
               ("python3", "tests/_modularity_rules.py"), (FULL, BOOTSTRAP),
               "R-MOD-02/05: no module-level upward import across the layer "
               "order, no grab-bag module name; residual shrink-only."),
        _stage("test-ownership", "modularity ratchet: test ownership",
               ("python3", "tests/_test_ownership.py"), (FULL, BOOTSTRAP),
               "R-MOD-06: every agentware_lib module is owned by "
               "tests/unit/<layer>/test_<stem>.py; residual shrink-only."),
        _stage("parity", "parity gate",
               ("python3", "tests/_parity_manifest.py", "--check"),
               (FULL, BOOTSTRAP),
               "R-MOD-08: every declared capability carries a manifest row with "
               "its status, and every excluded row carries a passing absence "
               "probe."),
        # `--debt` is REQUIRED, not optional tuning: without it the gate reports
        # the six declared, pre-existing donor-fixture findings and CI is red on
        # day one for something no contributor introduced. The ledger is
        # shrink-only and may never forgive an `operator_product` finding, so
        # running under it subtracts history without weakening the rule.
        _stage("parity-probes", "parity mutation probes are runnable",
               ("python3", "tests/_parity_sweep.py", "--check"),
               (FULL, BOOTSTRAP),
               "Task 79: every curated row's mutation is a spec over the closed "
               "grammar, addressed at a path the tree still has and not at the "
               "row's own test. Milliseconds, no clone -- it catches a probe "
               "gone stale from a rename before the sweep spends five minutes "
               "reporting the same thing as a no-op."),
        _stage("parity-sweep", "parity mutation sweep (every curated row)",
               ("python3", "tests/_parity_sweep.py", "--all", "--jobs", "4"),
               (FULL,),
               "THE proof behind the parity ledger: each row's mutation is "
               "applied in a throwaway clone and its named test must go RED. "
               "Costs ~5 minutes and is worth it -- the first run of this stage "
               "found eight rows whose named test could not observe the "
               "capability at all and one capability nothing guarded."),
        _stage("portability", "portability gate (under the debt ratchet)",
               ("python3", "tests/_portability_gate.py", "--debt"), (FULL,),
               "The shipped tree is vendor- and operator-neutral across all "
               "five surfaces (working tree, filenames, empty dirs, git "
               "metadata, commit range)."),
        _stage("object-store", "object store gained only local authorship",
               ("python3", "tests/_portability_store.py"), (FULL,),
               "Task 83: every commit the object store holds beyond the base "
               "ref, classified by the weakest `git push` that could publish "
               "it. The commit-RANGE surface walks reachability from the branch "
               "and stops; a store keeps every abandoned rewrite and fetched "
               "tip, and `push --all` publishes by REF, not by what the branch "
               "reaches. It is how task 82's pre-rewrite identity was found "
               "still sitting on a refs/heads/ backup after the branch itself "
               "was proven clean."),
        _stage("neutrality-redaction", "committed captures carry no vendor token",
               ("python3", "tests/_neutrality_redact.py"), (FULL, BOOTSTRAP),
               "Task 83: the WRITE side of the portability gate. Three frozen "
               "captures (a donor test-run baseline and two node-id freezes) "
               "committed a vendor token no wave could clear, because a capture "
               "cannot be regenerated at a moved head. This runs the same "
               "needle set the scan uses over every capture artifact, so the "
               "redactor and the gate that grades it cannot drift. Cheap enough "
               "for the bootstrap profile; the builtin zero-tolerance class "
               "runs even where the private needle file is absent."),
        _stage("body-differ", "body differ",
               ("python3", "tests/_body_differ.py", "--check"), (FULL,),
               "No ported definition landed with a body THINNER than its "
               "adjudicated winner -- the loss class no surface gate can see."),
        _stage("constant-differ", "constant differ",
               ("python3", "tests/_constant_differ.py", "--check"), (FULL,),
               "No module constant silently took the other tree's literal; the "
               "16 divergent-value constants stay pinned to their decided side."),
        _stage("solo-parity", "solo parity against aw-pre-merge (team unset)",
               ("python3", "tests/_solo_parity.py", "--verify"), (FULL,),
               "Task 81: with team mode UNSET, a fresh clone of the merged "
               "tree behaves identically to aw-pre-merge -- no parser node or "
               "action lost, no help string silently rewritten, and a scripted "
               "read-only session byte-identical on both sides. The only gate "
               "that RUNS both CLIs; it is how the `config` report's dropped "
               "skill_promote line was found. Skips (rc 0) where the tag is "
               "absent, which is the state of a shallow checkout."),
        _stage("commit-series", "commit series is publishable",
               ("python3", "tests/_commit_series.py"), (FULL,),
               "Task 82: the branch's history carries ONE operator identity in "
               "the author field, the committer field and every trailer; every "
               "date sits inside the declared window; every subject describes "
               "an engineering change; and the series is linear and never "
               "shorter than its floor. The portability gate scans the commit "
               "range for vendor TOKENS -- nothing scanned the SHAPE of the "
               "series, which is where a second identity and a tool's "
               "attribution line survive a perfectly scrubbed working tree."),
        _stage("modularity-negative", "modularity negative test",
               ("python3", "-m", "unittest",
                "tests.gates.test_modularity_negative", "-v"), (FULL,),
               "The anti-monolith rules REJECT a violating change -- run "
               "explicitly as well as via the gates tier, because a rule with "
               "no proof that it can fail is an allowlist with better prose."),
        _stage("bootstrap", "fresh-clone bootstrap (from an empty directory)",
               ("python3", "tests/_bootstrap_gate.py", "--from-empty"), (FULL,),
               "A brand-new clone reaches a working state with ONE documented "
               "command, no dependency install and no network. Runs the "
               "bootstrap profile INSIDE the clone -- which is why that profile "
               "must not contain this stage."),
        _stage("clone-health", "clone health (hermetic init + audit)",
               ("python3", "tests/_bootstrap_gate.py", "--health"),
               (BOOTSTRAP,),
               "The health sequence itself: --help renders, init scaffolds, "
               "index validate passes, recall answers, audit is clean -- all "
               "against a temp HOME so the operator's real config is untouched."),
    ] + _suite_stages()
)

#: TASK 78's OWN WORDS -> the stages that discharge them. The task names eight
#: things the workflow must run plus the fresh-clone proof; this table is how a
#: reviewer checks the workflow still runs all nine WITHOUT re-reading the plan,
#: and how `test_ci_workflow.py` arm C fails by NAME when a stage is dropped.
#: A phrase with no stage, or a stage id that does not exist, is a gate failure.
TASK_COVERAGE = (
    ("the full suite", tuple("suite-" + t for t in SUITE_TIERS)),
    # TASK 83 widened this phrase rather than adding a tenth row: the task-12
    # gate, the object-store surface and the write-side redactor are three
    # halves of ONE claim (this tree is publishable), and listing them here is
    # what makes dropping any of them fail by name instead of by silence.
    ("the portability gate",
     ("portability", "object-store", "neutrality-redaction")),
    ("the parity gate", ("parity", "parity-probes", "parity-sweep")),
    ("the body differ", ("body-differ",)),
    ("the constant differ", ("constant-differ",)),
    ("the modularity ratchet",
     ("ceilings", "layer-direction", "test-ownership", "module-manifest")),
    ("the negative test", ("modularity-negative",)),
    ("steering lint", ("steering-lint",)),
    ("a fresh clone reaches a working state with one documented command",
     ("bootstrap",)),
)


def stages_for(profile=FULL, only=()):
    """The stages to run, in table order, for a profile and optional id filter."""
    chosen = [s for s in STAGES if profile in s["profiles"]]
    if only:
        wanted = set(only)
        unknown = sorted(wanted - set(s["id"] for s in STAGES))
        if unknown:
            raise ValueError("unknown stage(s): %s" % ", ".join(unknown))
        chosen = [s for s in STAGES if s["id"] in wanted]
    return chosen


def child_env(env=None):
    """Environment for a stage child.

    `AGENTWARE_NESTED_UNITTEST=1` is not a speed knob: two verbs measure a test
    pass-rate by shelling `unittest discover -s tests` over the WHOLE tree, and a
    suite run that reaches one of them re-enters the hanging collection inside
    itself (`tests/gates/test_suite_recursion_guard.py`). CI sets it on the
    parent so no stage can start unguarded.
    """
    out = dict(os.environ if env is None else env)
    out["AGENTWARE_NESTED_UNITTEST"] = "1"
    out.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return out


def run_stage(stage, root=None, env=None, echo=True):
    """Run one stage as a subprocess. Returns a result dict; never raises."""
    root = root or REPO_ROOT
    argv = list(stage["argv"])
    if argv and argv[0] == "python3":
        argv[0] = sys.executable          # honour the interpreter we run under
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=root, env=child_env(env),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    output = proc.stdout or ""
    result = {"id": stage["id"], "title": stage["title"],
              "command": " ".join(stage["argv"]), "returncode": proc.returncode,
              "ok": proc.returncode == 0,
              "seconds": round(time.monotonic() - started, 1),
              "output": output}
    if echo:
        sys.stdout.write("%s %-34s %6.1fs  %s\n"
                         % ("PASS" if result["ok"] else "FAIL", stage["id"],
                            result["seconds"], result["command"]))
        if not result["ok"]:
            sys.stdout.write(_tail(output))
        sys.stdout.flush()
    return result


def _tail(output, lines=25):
    """The last few lines of a failed stage, indented, so CI logs stay readable."""
    kept = [ln for ln in output.splitlines() if ln.strip()][-lines:]
    return "".join("     | %s\n" % ln for ln in kept)


def run(profile=FULL, only=(), fail_fast=False, echo=True):
    """Run a profile. Returns (results, ok)."""
    results = []
    for stage in stages_for(profile, only):
        result = run_stage(stage, echo=echo)
        results.append(result)
        if fail_fast and not result["ok"]:
            break
    return results, all(r["ok"] for r in results)


def _summary(results, profile):
    failed = [r for r in results if not r["ok"]]
    total = sum(r["seconds"] for r in results)
    head = ("ci gate (%s): %d stage(s), %d failed, %.1fs\n"
            % (profile, len(results), len(failed), total))
    if not failed:
        return head
    return head + "".join("  FAILED %s: %s\n" % (r["id"], r["command"])
                          for r in failed)


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="tests/_ci_gate.py",
        description="Run every gate CI runs, in CI's order, from one command.")
    ap.add_argument("--profile", choices=PROFILES, default=FULL,
                    help="which set of stages to run (default: full)")
    ap.add_argument("--stage", action="append", default=[], metavar="ID",
                    help="run only this stage (repeatable)")
    ap.add_argument("--list", action="store_true",
                    help="print the declared stage table and exit")
    ap.add_argument("--fail-fast", action="store_true",
                    help="stop at the first red stage (default: run them all, "
                         "so one failure does not hide four others)")
    ap.add_argument("--json", action="store_true", help="emit JSON results")
    args = ap.parse_args(list(sys.argv[1:] if argv is None else argv))

    if args.list:
        rows = [{"id": s["id"], "title": s["title"], "profiles": s["profiles"],
                 "command": " ".join(s["argv"]), "why": s["why"]}
                for s in STAGES]
        if args.json:
            sys.stdout.write(json.dumps(rows, indent=2) + "\n")
        else:
            for row in rows:
                sys.stdout.write("%-22s %-12s %s\n"
                                 % (row["id"], ",".join(row["profiles"]),
                                    row["command"]))
        return 0

    try:
        planned = stages_for(args.profile, tuple(args.stage))
    except ValueError as exc:
        sys.stderr.write("%s\n" % exc)
        return 2
    if not args.json:
        sys.stdout.write("ci gate: profile=%s, %d stage(s)\n"
                         % (args.profile, len(planned)))
    results, ok = run(args.profile, tuple(args.stage), args.fail_fast,
                      echo=not args.json)
    if args.json:
        sys.stdout.write(json.dumps(
            {"profile": args.profile, "ok": ok, "stages": results},
            indent=2) + "\n")
    else:
        sys.stdout.write(_summary(results, args.profile))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
