"""agentware_lib.ops.planauthor.planedit -- deterministic plan.md authoring (layer: ops).

Owns: the GENERATION half of the plan toolkit (feature 260625-plan-authoring-toolkit) --
the deterministic writers that produce lint-passing plan.md structure BY CONSTRUCTION
(_plan_skeleton / _plan_promise / _plan_path / _plan_renumber / _plan_marker_indices /
_load_plan_for_edit / _atomic_write / the _PLAN_STATUS_GLYPH marker map), the CLI handlers
cmd_plan_new / cmd_plan_add_task / cmd_plan_set_status / cmd_plan_target_dirs, and the
`plan` verb registrar (register_plan), moved WITH its handlers.

Does NOT own: the work-artifact team/author STAMPING family (_WORK_TEAM_STAMP_RE /
_is_derived_roster / _stamp_work_artifact / _ensure_work_frontmatter / cmd_work_stamp and
the `work` registrar) -> agentware_lib.ops.planauthor.workstamp, a SIBLING submodule this
module does not import (the two halves share no symbol, so the seam is a clean cut); the
ENFORCEMENT half -- plan LINT (lint_plan / plan_target_dirs / the _PLAN_*_RE regexes /
cmd_plan_lint) -> agentware_lib.ops.planlint, reached DOWNWARD as a same-layer ops->ops
edge. The `plan lint` leaf still dispatches to ops.planlint.cmd_plan_lint (via the
_planlint alias). The plan-tainted derive_* reporting family (derive_plan_authoring etc.)
+ _plan_marker_status_counts stay RESIDENT until they move into agentware_lib.ops.derive.

Layer deps: ops.planauthor.planedit -> core (config resolvers, dates._today,
routing.resolve_user_handle) + a same-layer ops edge to ops.planlint -- all DOWNWARD or
same-layer. Cross-module FUNCTIONS are reached by ATTRIBUTE through module aliases
(config / dates / routing / _planlint) so the bytecode global-resolve gate never cascades
into them; the one planlint CONSTANT read bare (_PLAN_TASK_LINE_RE) is imported BY NAME (a
compiled regex, no __code__, no cascade).

_EXPORTS is imported BY NAME onto the scripts/agentware facade because RESIDENT code
references moved names (_atomic_write from fanout _fanout_provision_lane /
_install_merge_policy / save_lanes; _plan_path from cmd_metrics) and the parser-golden
dispatch contract requires each plan leaf's func=cmd_* to be an attribute of the entrypoint
namespace. The `plan` registrar is resolved via importlib from _PARSER_REGISTRATION
(pointing at the PACKAGE agentware_lib.ops.planauthor, whose aggregator re-imports it),
NOT re-exported.

Determinism: pure deterministic text generation, stdlib json/os/re/sys only (INV-2,
INV-6). Plan structure/order is a pure function of inputs; no wall-clock enters ranking
(INV-1); _plan_skeleton's only time input is dates._today() (the canonical date facade).

Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config
import agentware_lib.core.dates as dates
import agentware_lib.core.routing as routing
import agentware_lib.ops.planlint as _planlint
from agentware_lib.ops.planlint import _PLAN_TASK_LINE_RE


# --- Plan authoring (feature 260625-plan-authoring-toolkit) -------------------
# The GENERATION half of the generation/enforcement split: deterministic writers
# that produce lint-passing plan structure BY CONSTRUCTION. The LLM planner
# decides WHAT the tasks are; these commands guarantee the FORM (markers,
# numbering, required sections, the <promise> tag, and the mandatory trailing
# [e2e]+[kb] pair). They reuse the SINGLE marker source-of-truth from the
# lint gate (_PLAN_TASK_LINE_RE, any-status) — no second pattern.

# Maps the CLI status word to the canonical marker glyph.
_PLAN_STATUS_GLYPH = {"todo": "⬜", "wip": "🟡", "done": "✅"}


def _atomic_write(path, text):
    """Write `text` to `path` atomically (temp sibling + os.replace rename).

    The single writer all three emitter commands route through, so a crash mid-
    write can never leave a half-written plan.md.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _plan_promise(feature):
    """Derive the deterministic <promise> content from a feature name.

    `260625-plan-authoring-toolkit` -> `260625_PLAN_AUTHORING_TOOLKIT_COMPLETE`.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "_", feature).strip("_").upper()
    return "%s_COMPLETE" % slug


def _plan_path(kdir, feature):
    return os.path.join(kdir, "work", feature, "plan.md")


def _plan_skeleton(feature, title, max_iterations, self_extension, author=None,
                   team=None):
    """Build the canonical, lint-passing plan skeleton.

    ALWAYS seeds the mandatory trailing pair: a penultimate `[e2e]` task (literal
    token + non-empty Verify -> R6) and a final `[kb]` task embedding an R7 token
    (`index validate`/`learn`/`features` -> R7). There is no flag to omit them.

    Org mode (feature 260721): stamps `> Team:` alongside `> Author:` so every work
    artifact carries team provenance (operator direction). Both omitted on a flat KB.
    """
    warning = ""
    if self_extension:
        warning = (
            "## ‼️ Self-extension warning (read before executing)\n\n"
            "This plan edits agentware **package** files (`R-PKG-01`). Per the "
            "reinterpreted `R-PKG-03`, approving and running this prominently-"
            "warned plan IS the confirmation — the loop RECORDS it and proceeds "
            "(no mid-run stop). Per `R-PKG-04`/`R-PKG-05`, run `scripts/agentware "
            "steering lint` and `scripts/agentware eval --record --gate` after "
            "editing; a reliability-neutral-or-better A/B passes, a pre-existing-"
            "drift FAIL is a recorded `> DECISION:` known-issue, and a genuine "
            "diff-caused regression stops.\n\n---\n\n")
    selfext_line = "YES (`R-PKG-03` record-and-proceed)" if self_extension else "NO"
    # Author line: included when a handle is available (team mode provenance).
    author_line = ""
    if author:
        author_line = "> Author: %s\n" % author
    # Team line: included in org mode so every work artifact carries team provenance.
    if team:
        author_line = "> Team: %s\n%s" % (team, author_line)
    return (
        "# Plan — %s\n\n"
        "> Feature: `%s`\n"
        "> Created: %s\n"
        "%s"
        "> Type: %s\n\n"
        "---\n\n"
        "%s"
        "## Context\n\n"
        "_TODO: problem, goal, design, constraints, environment, pitfalls._\n\n"
        "---\n\n"
        "## Target packages\n\n"
        "- TODO: replace with the absolute or ~-rooted directory path(s) this "
        "plan targets\n\n"
        "---\n\n"
        "## Tasks\n\n"
        "- ⬜ **1** [e2e] End-to-end verification of the feature.\n"
        "  *Verify:* TODO — exercise the feature end-to-end and capture the outcome.\n"
        "- ⬜ **2** [kb] Knowledge-base update.\n"
        "  Run `scripts/agentware audit --stale`, add a learning via "
        "`scripts/agentware learn`, then `scripts/agentware features`.\n"
        "  *Verify:* `scripts/agentware index validate` exits 0; "
        "`scripts/agentware worklog scan` reports 0 unpromoted markers.\n\n"
        "---\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] TODO: define the verifiable acceptance criteria.\n\n"
        "---\n\n"
        "## Operational\n\n"
        "- **Max iterations:** %d\n"
        "- **Self-extension:** %s\n\n"
        "<promise>%s</promise>\n"
        % (title, feature, dates._today(), author_line,
           "**self-extension (package change)**" if self_extension
           else "feature work",
           warning, int(max_iterations), selfext_line, _plan_promise(feature))
    )


def _plan_renumber(lines):
    """Rewrite ONLY the `**N**` token of each task marker to a monotonic 1..N.

    Uses the any-status matcher so ✅ tasks are renumbered too (never skipped).
    """
    out, n = [], 0
    for ln in lines:
        if _PLAN_TASK_LINE_RE.match(ln):
            n += 1
            ln = re.sub(r"(\*\*)\d+(\*\*)", r"\g<1>%d\g<2>" % n, ln, count=1)
        out.append(ln)
    return out


def _plan_marker_indices(lines):
    """Indices of every task-marker line, in file order."""
    return [i for i, ln in enumerate(lines) if _PLAN_TASK_LINE_RE.match(ln)]


def _load_plan_for_edit(kdir, feature):
    """Read a plan for mutation, refusing if it is missing or fails lint.

    Returns (text, None) on success or (None, error_message). The read-modify-
    write guard: never edit (and so never corrupt) a plan that does not already
    satisfy the structural contract.
    """
    path = _plan_path(kdir, feature)
    if not os.path.isfile(path):
        return None, "no plan at %s (run `plan new %s` first)" % (path, feature)
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    errors, _ = _planlint.lint_plan(text, strict=False)
    if errors:
        return None, ("refusing to edit a plan that fails `plan lint` "
                      "(%d error(s)); fix it first" % len(errors))
    return text, None


def cmd_plan_new(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    path = _plan_path(kdir, args.feature)
    if os.path.exists(path) and not getattr(args, "force", False):
        sys.stderr.write(
            "agentware: plan already exists (use --force to overwrite): %s\n"
            % path)
        return 1
    # Resolve the author: explicit --author override, else the per-user handle.
    author = getattr(args, "author", None) or routing.resolve_user_handle()
    team = config._infer_team_from_kdir(kdir)
    text = _plan_skeleton(args.feature, args.title,
                          getattr(args, "max_iterations", 30),
                          bool(getattr(args, "self_extension", False)),
                          author=author, team=team)
    _atomic_write(path, text)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "path": path,
                   "promise": _plan_promise(args.feature)},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PLAN created '%s' -> %s" % (args.feature, path))
    return 0


def cmd_plan_add_task(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    text, err = _load_plan_for_edit(kdir, args.feature)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    lines = text.splitlines()
    markers = _plan_marker_indices(lines)
    # Insert BEFORE the mandatory trailing [e2e]+[kb] pair (the last two tasks).
    # With <2 tasks present the plan is non-canonical; the lint guard above would
    # have caught a missing Tasks section, so fall back to "before the last".
    insert_at = markers[-2] if len(markers) >= 2 else (
        markers[-1] if markers else len(lines))

    tag = ""
    verify = (args.verify or "").strip()
    if getattr(args, "e2e", False):
        tag = "[e2e] "
        verify = verify or "TODO — exercise this end-to-end and capture the outcome."
    elif getattr(args, "kb", False):
        tag = "[kb] "
        verify = verify or "`scripts/agentware index validate` exits 0."
    verify = verify or "TODO — add a verification command for this task."

    block = ["- ⬜ **0** %s%s" % (tag, args.description),
             "  *Verify:* %s" % verify]
    new_lines = lines[:insert_at] + block + lines[insert_at:]
    new_lines = _plan_renumber(new_lines)
    out = "\n".join(new_lines) + ("\n" if text.endswith("\n") else "")
    _atomic_write(_plan_path(kdir, args.feature), out)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "tasks": len(_plan_marker_indices(new_lines))},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PLAN add-task '%s': %d task(s)"
              % (args.feature, len(_plan_marker_indices(new_lines))))
    return 0


def cmd_plan_set_status(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    glyph = _PLAN_STATUS_GLYPH.get(args.status)
    if glyph is None:
        sys.stderr.write("agentware: status must be todo|wip|done\n")
        return 1
    text, err = _load_plan_for_edit(kdir, args.feature)
    if err is not None:
        sys.stderr.write("agentware: %s\n" % err)
        return 1
    lines = text.splitlines()
    target = int(args.number)
    hit = False
    for i, ln in enumerate(lines):
        m = _PLAN_TASK_LINE_RE.match(ln)
        # Toolkit-managed plans use flat `**N**`; phased `**N.M**` markers (which
        # int() cannot parse) are skipped rather than crashing set-status.
        if m and m.group(2).isdigit() and int(m.group(2)) == target:
            lines[i] = re.sub(r"(⬜|🟡|✅)", glyph, ln, count=1)
            hit = True
            break
    if not hit:
        sys.stderr.write("agentware: no task numbered %d in plan\n" % target)
        return 1
    out = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _atomic_write(_plan_path(kdir, args.feature), out)
    if getattr(args, "format", "text") == "json":
        json.dump({"ok": True, "task": target, "status": args.status},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PLAN set-status '%s': task %d -> %s"
              % (args.feature, target, args.status))
    return 0


def cmd_plan_target_dirs(args):
    """Print declared target-package directories from a plan (one per line)."""
    path = args.path
    if not os.path.isfile(path):
        sys.stderr.write("agentware: plan not found: %s\n" % path)
        return 1
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    dirs = _planlint.plan_target_dirs(text)
    for d in dirs:
        print(d)
    return 0


def register_plan(sub, fmt_parent):
    # plan -------------------------------------------------------------------
    p_plan = sub.add_parser("plan", help="feature-plan structural tooling")
    plan_sub = p_plan.add_subparsers(dest="plan_command", metavar="<subcommand>")
    plan_sub.required = True
    p_plan_lint = plan_sub.add_parser(
        "lint", parents=[fmt_parent],
        help="lint a feature plan against the R1–R9 structural contract")
    p_plan_lint.add_argument("--path", required=True, help="path to a plan.md")
    p_plan_lint.add_argument(
        "--strict", action="store_true",
        help="treat the R9 autonomy rule as a hard failure (used by the loop "
             "pre-hook so a non-autonomous plan aborts before the run)")
    p_plan_lint.set_defaults(func=_planlint.cmd_plan_lint)

    p_plan_new = plan_sub.add_parser(
        "new", parents=[fmt_parent],
        help="scaffold a lint-passing plan.md (seeds the mandatory [e2e]+[kb] pair)")
    p_plan_new.add_argument("feature", help="feature slug; dir under work/")
    p_plan_new.add_argument("--title", required=True, help="human plan title")
    p_plan_new.add_argument("--max-iterations", type=int, default=30,
                            dest="max_iterations", help="loop iteration budget")
    p_plan_new.add_argument("--self-extension", action="store_true",
                            dest="self_extension",
                            help="seed the R-PKG-03 self-extension warning block")
    p_plan_new.add_argument("--force", action="store_true",
                            help="overwrite an existing plan.md")
    p_plan_new.add_argument("--author", default=None,
                            help="provenance author handle (default: the per-user "
                                 "handle if set). Visible metadata only — no effect "
                                 "on execution or retrieval.")
    p_plan_new.set_defaults(func=cmd_plan_new)

    p_plan_add = plan_sub.add_parser(
        "add-task", parents=[fmt_parent],
        help="insert a task before the trailing [e2e]+[kb] pair and renumber")
    p_plan_add.add_argument("feature", help="feature slug; dir under work/")
    p_plan_add.add_argument("description", help="task description")
    p_plan_add.add_argument("--verify", default=None, help="the *Verify:* command")
    p_plan_add.add_argument("--e2e", action="store_true",
                            help="tag this as an additional [e2e] task")
    p_plan_add.add_argument("--kb", action="store_true",
                            help="tag this as an additional [kb] task")
    p_plan_add.set_defaults(func=cmd_plan_add_task)

    p_plan_status = plan_sub.add_parser(
        "set-status", parents=[fmt_parent],
        help="flip a task marker (todo|wip|done) in place, line-local")
    p_plan_status.add_argument("feature", help="feature slug; dir under work/")
    p_plan_status.add_argument("number", type=int, help="task number to flip")
    p_plan_status.add_argument("status", choices=("todo", "wip", "done"),
                               help="new status")
    p_plan_status.set_defaults(func=cmd_plan_set_status)

    p_plan_tdirs = plan_sub.add_parser(
        "target-dirs",
        help="print declared target-package dirs from a plan (one per line)")
    p_plan_tdirs.add_argument("--path", required=True, help="path to a plan.md")
    p_plan_tdirs.set_defaults(func=cmd_plan_target_dirs)


_EXPORTS = [
    "_PLAN_STATUS_GLYPH",
    "_atomic_write",
    "_plan_promise",
    "_plan_path",
    "_plan_skeleton",
    "_plan_renumber",
    "_plan_marker_indices",
    "_load_plan_for_edit",
    "cmd_plan_new",
    "cmd_plan_add_task",
    "cmd_plan_set_status",
    "cmd_plan_target_dirs",
]
__all__ = list(_EXPORTS)
