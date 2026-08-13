"""agentware_lib.ops.planauthor.planedit -- deterministic plan.md authoring (layer: ops).

Owns: the EDITING half of the plan toolkit (feature 260625-plan-authoring-toolkit) --
the deterministic writers that keep an EXISTING plan.md lint-passing BY CONSTRUCTION
(_plan_promise / _plan_path / _plan_renumber / _plan_marker_indices /
_load_plan_for_edit / _atomic_write / the _PLAN_STATUS_GLYPH marker map), the CLI handlers
cmd_plan_add_task / cmd_plan_set_status / cmd_plan_target_dirs, and the
`plan` verb registrar (register_plan), moved WITH its handlers.

Does NOT own: the GENERATION half -- building a plan.md from nothing (_plan_skeleton /
cmd_plan_new) plus the g3 workorder lifecycle cap -> agentware_lib.ops.planauthor.plannew,
split out at task 39 because creation and editing are different obligations and the
union skeleton would have taken this file to ~663 of its 600 lines. plannew imports THIS
module for the shared writers; the one reference back (cmd_plan_new, needed by
register_plan) is a FUNCTION-LOCAL import inside the registrar, so the edge stays
one-way and no cycle exists. The work-artifact team/author STAMPING family (_WORK_TEAM_STAMP_RE /
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
namespace. The `plan` registrar is declared in THIS module's `__verbs__`
(the aggregator still re-imports it for pre-split call sites), NOT re-exported.

Determinism: pure deterministic text generation, stdlib json/os/re/sys only (INV-2,
INV-6). Plan structure/order is a pure function of inputs; no wall-clock enters ranking
(INV-1); the only time input on this side is dates._today() (the canonical date facade),
which plannew reaches through this module's alias.

Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import importlib
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


def _register_plan_lint(plan_sub, fmt_parent):
    """Register the R1..R16 structural linter."""
    p_plan_lint = plan_sub.add_parser(
        "lint", parents=[fmt_parent],
        help="lint a feature plan against the R1–R16 structural contract")
    p_plan_lint.add_argument("--path", required=True, help="path to a plan.md")
    p_plan_lint.add_argument(
        "--strict", action="store_true",
        help="treat the R9 autonomy rule (and the R13 done-reality binding) as "
             "hard failures (used by the loop pre-hook so a non-autonomous plan "
             "aborts before the run)")
    p_plan_lint.add_argument(
        "--readiness", action="store_true",
        help="check ONLY the mechanical draft->ready gate: no emitter placeholder "
             "signatures remain and >=1 real acceptance checkbox exists")
    p_plan_lint.set_defaults(func=_planlint.cmd_plan_lint)


def _register_plan_premises(plan_sub, fmt_parent):
    """Register the R-GND-01 premise grounding gate."""
    # `verify-premises` is owned by ops.planpremises (the grounding gate the loop
    # pre-hook runs before phase 1). Imported at registration time for symmetry
    # with the other cross-module leaves below.
    import agentware_lib.ops.planpremises as planpremises
    p_plan_verify = plan_sub.add_parser(
        "verify-premises", parents=[fmt_parent],
        help="re-verify a plan's declared `## Premises` against LIVE state "
             "(read-only; a reproduced falsification exits 1 — the loop pre-hook "
             "halts a load-bearing run before phase 1)")
    p_plan_verify.add_argument("--path", required=True, help="path to a plan.md")
    p_plan_verify.add_argument(
        "--strict", action="store_true",
        help="fail-CLOSED on an unresolvable citation for a self-extension plan "
             "(used by the loop pre-hook); a reproduced falsification always "
             "exits 1 regardless of --strict")
    p_plan_verify.set_defaults(func=planpremises.cmd_plan_verify_premises)


def _register_plan_new(plan_sub, fmt_parent):
    """Register plan GENERATION (owned by plannew)."""
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
    p_plan_new.add_argument(
        "--auto", action="store_true",
        help="emit a WORKORDER under work/auto/<slug>/ (the emitter prepends "
             "'auto/'); requires --generated-by and --trigger, seeds provenance "
             "headers + a knowledge-dir target, and enforces the emit-time "
             "governance guards g1/g2/g3")
    p_plan_new.add_argument("--generated-by", dest="generated_by", default=None,
                            help="[--auto] the tool/agent that generated this "
                                 "workorder (provenance)")
    p_plan_new.add_argument("--trigger", default=None,
                            help="[--auto] the event that triggered it (provenance)")
    p_plan_new.add_argument("--evidence", default=None,
                            help="[--auto] optional evidence path(s) (provenance)")
    p_plan_new.add_argument("--target", action="append", default=None,
                            help="[--auto] target directory (repeatable; default: "
                                 "the knowledge dir). Kernel paths are refused (g1).")
    p_plan_new.add_argument(
        "--context", default=None,
        help="UNTRUSTED caller-supplied candidate text, quoted verbatim inside a "
             "fenced block in the Context section ONLY (never a Verify/exec line). "
             "Forged headers and fence-breakouts are neutralized; kernel-path "
             "candidates are refused (g1). The emit seam the dream classify drain "
             "uses for mined preferences.")
    # `plan new` is owned by the sibling GENERATION module. Imported HERE rather
    # than at module scope because plannew imports THIS module for the shared
    # writers (_plan_path/_atomic_write/_plan_promise); a module-level import back
    # would close that cycle. Registration runs once, at parser build, so the
    # deferred import costs nothing and keeps the edge strictly one-way.
    import agentware_lib.ops.planauthor.plannew as plannew
    p_plan_new.set_defaults(func=plannew.cmd_plan_new)


def _register_plan_edit(plan_sub, fmt_parent):
    """Register the in-place editors: add-task + set-status."""
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


def _register_plan_state(plan_sub, fmt_parent):
    """Register the lifecycle STATE MACHINE: set-state + state-backfill + claim."""
    # The lifecycle STATE MACHINE lives in the sibling planstate module; `plan
    # state-backfill`'s payload needs a features-layer live-session detector, so
    # its one-line CLI adapter lives in features.migratecmd and is resolved by
    # (module, attr) at REGISTRATION time — the same importlib late-resolution
    # idiom migratecmd itself uses for _KB_WRITER_TARGETS. Resolving it here keeps
    # ops free of a module-level ops->features import, which would be an UPWARD
    # cross-layer edge.
    import agentware_lib.ops.planauthor.planstate as planstate
    import agentware_lib.ops.plangraph as plangraph
    _backfill = getattr(
        importlib.import_module("agentware_lib.features.migratecmd"),
        "cmd_plan_state_backfill")

    p_plan_state = plan_sub.add_parser(
        "set-state", parents=[fmt_parent],
        help="transition a plan's lifecycle state (draft|ready|running|done|"
             "superseded|cancelled) via the legal-transition matrix")
    p_plan_state.add_argument("feature", help="feature slug; dir under work/")
    p_plan_state.add_argument("state", choices=plangraph._PLAN_STATES,
                              help="target state")
    p_plan_state.add_argument(
        "--actor", choices=("operator", "loop"), default="operator",
        help="who is requesting the transition (worker-restricted edges refuse "
             "actor=loop)")
    p_plan_state.add_argument(
        "--reason", default=None,
        help="required for demotions / cancellations / the cancelled->draft reopen")
    p_plan_state.add_argument(
        "--superseded-by", dest="superseded_by", default=None,
        help="required for any X -> superseded (the replacing feature slug)")
    p_plan_state.add_argument("--handle", default=None,
                              help="claim handle (default: resolved user handle)")
    p_plan_state.add_argument("--host", default=None,
                              help="claim host (default: short hostname)")
    p_plan_state.add_argument("--now", default=None,
                              help="ISO timestamp override (determinism/testing)")
    p_plan_state.set_defaults(func=planstate.cmd_plan_set_state)

    p_plan_backfill = plan_sub.add_parser(
        "state-backfill", parents=[fmt_parent],
        help="stamp a derived `> Status:` on every legacy plan lacking one "
             "(migration 001; never overwrites, skips the live feature)")
    p_plan_backfill.set_defaults(func=_backfill)

    p_plan_claim = plan_sub.add_parser(
        "claim", parents=[fmt_parent],
        help="loop START-edge: bring a plan to running (draft->ready->running / "
             "ready->running / running re-claim), or refuse a terminal plan")
    p_plan_claim.add_argument("feature", help="feature slug; dir under work/")
    p_plan_claim.add_argument("--actor", choices=("operator", "loop"),
                              default="loop", help="requesting actor")
    p_plan_claim.add_argument("--handle", default=None, help="claim handle")
    p_plan_claim.add_argument("--host", default=None, help="claim host")
    p_plan_claim.add_argument("--now", default=None, help="ISO ts override (tests)")
    p_plan_claim.set_defaults(func=planstate.cmd_plan_claim)


def _register_plan_tdirs(plan_sub, fmt_parent):
    """Register the target-package reader."""
    p_plan_tdirs = plan_sub.add_parser(
        "target-dirs",
        help="print declared target-package dirs from a plan (one per line)")
    p_plan_tdirs.add_argument("--path", required=True, help="path to a plan.md")
    p_plan_tdirs.set_defaults(func=cmd_plan_target_dirs)


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("plan", 3400, "register_plan"),
)


def register_plan(sub, fmt_parent):
    # plan -------------------------------------------------------------------
    # ASSEMBLY ONLY. Each subverb group is registered by its own `_register_plan_*`
    # helper, in the pre-split ORDER, so the nine leaves and their flags stay
    # byte-identical to the monolith's parser while this function holds the
    # per-function cap (task 39).
    p_plan = sub.add_parser("plan", help="feature-plan structural tooling")
    plan_sub = p_plan.add_subparsers(dest="plan_command", metavar="<subcommand>")
    plan_sub.required = True
    _register_plan_lint(plan_sub, fmt_parent)
    _register_plan_premises(plan_sub, fmt_parent)
    _register_plan_new(plan_sub, fmt_parent)
    _register_plan_edit(plan_sub, fmt_parent)
    _register_plan_state(plan_sub, fmt_parent)
    _register_plan_tdirs(plan_sub, fmt_parent)



_EXPORTS = [
    "_PLAN_STATUS_GLYPH",
    "_atomic_write",
    "_plan_promise",
    "_plan_path",
    "_plan_renumber",
    "_plan_marker_indices",
    "_load_plan_for_edit",
    "cmd_plan_add_task",
    "cmd_plan_set_status",
    "cmd_plan_target_dirs",
]
__all__ = list(_EXPORTS)
