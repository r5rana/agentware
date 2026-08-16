"""agentware_lib.ops.planlint -- feature-plan structural lint, R1..R16 (layer: ops).

Owns: the plan-lint ENFORCEMENT half of the plan toolkit (feature
260625-plan-lint-gate) — the eleven PURE FORMAT rules R1..R11 as one receiver per
rule group, the `lint_plan` orchestrator that runs ALL SIXTEEN in a FIXED order,
the draft->ready readiness gate (`_PLAN_READINESS_SIGNATURES` /
`_PLAN_ACCEPT_PLACEHOLDER` / `_plan_readiness`), the KB-relative path resolver
R12 keys on (`_plan_rel_path`), the text/JSON emitter (`_emit_plan_lint`) and the
CLI handler (`cmd_plan_lint`).

  R1   the file exists and is readable            _plan_lint_read
  R2   `## Tasks` + `## Acceptance criteria`      _lint_sections
  R3   well-formed task markers                   _lint_markers
  R4   monotonic task numbering                   _lint_markers
  R5   every task carries `*Verify:*`             _lint_task_bodies
  R6   an `[e2e]` task with a non-empty Verify    _lint_task_bodies
  R7   a task references a KB update              _lint_task_bodies
  R8   exactly one <promise> tag                  _lint_promise
  R9   no blocking phrasing without a carve-out   _lint_autonomy
  R10  team-mode plans name an author             _lint_authorship
  R11  `## Target packages` with a real path      _lint_targets
  R12  workorder provenance headers               ops.plangovern
  R13  `> Status:` bound to derived task reality  ops.plangovern
  R14  a load-bearing plan cites `## Premises`    ops.plangovern
  R15  `## Depends-on:` closed grammar            ops.plangovern
  R16  `> Checkpoint:` in vocab, non-none if S-E  ops.plangovern

R1..R11 are decidable from the plan TEXT alone, so they are errors from the day
they land and they live here. R12..R16 additionally need the plan's PLACE in the
knowledge base — its KB-relative path, the KB's schema generation and the
knowledge dir — which is why `lint_plan` takes five arguments rather than two and
why each of those four rides a WARN->ERROR versioned ramp; they are
ops.plangovern, imported downward and re-exported below.

Does NOT own: R12..R16 -> ops.plangovern; the plan-document GRAMMAR every rule
reads (the task-marker regexes, the numbering check, the open-marker counter, the
finding record) -> ops.planmarkers; the DECLARATIVE BLOCK parsers R11/R13/R15/R16
consult (`## Target packages`, `> Status:`, `## Depends-on:`, `> Checkpoint:`) ->
ops.plangraph; the `## Premises` trust boundary R14 consults -> ops.planpremises.
Every one of those is a LEAF this module imports downward, and their names are
RE-EXPORTED below so `CLI.<name>` and the `planauthor` `_planlint` alias resolve
the same objects they always did.

Does NOT own: plan AUTHORING -- the deterministic writers skeleton/new/add-task/
set-status/target-dirs + work-artifact stamping are agentware_lib.ops.planauthor,
which imports THESE names downward as a same-layer ops->ops edge (cmd_plan_new calls
lint_plan; _PLAN_TASK_LINE_RE is the shared marker source; cmd_plan_set_state calls
_plan_readiness for the draft->ready gate). The plan-tainted derive_* reporting
family stays RESIDENT until it moves into agentware_lib.ops.derive, which will import
_PLAN_TASK_LINE_RE / _parse_task_number through this module's re-export. The org/
template lint that lives textually between this domain's blocks is
agentware_lib.ops.fleet (org lint) / RESIDENT (deferred), never here.

WHY THE RULES ARE ONE FUNCTION EACH (feature 260813 task 39)
------------------------------------------------------------
The monolith's `lint_plan` is a single 270-line, 44-branch body. Porting it
verbatim would have been the cheaper move -- it is what tasks 37/38 did, and what
the body differ's length floor rewards -- but the modularity ceiling is 120 lines
and 12 branches per function and the ceiling ratchet is SHRINK-ONLY, so growing
the ported body in place is a hard fail with no debt row available. The rules are
therefore split at the seam the rule numbering already implies: one receiver per
rule group, each named for the rules it enforces, with the orchestrator reduced to
the ORDER they run in. Order is load-bearing -- findings are compared as ordered
lists by the differential probe against the monolith -- so the orchestrator reads
as R2..R16 top to bottom and nothing else.

Layer deps: ops.planlint -> ops.plangovern, ops.planmarkers, ops.plangraph
(same-layer, all one-way) and core.config / core.kblayout (downward). Nothing above
ops is imported: R12's schema generation comes from core.kblayout._kb_schema_read,
which moved down from features.migratecmd for exactly this reason.

Determinism: pure text linting plus two read-only KB lookups (the schema sidecar
here, and for R14's load-bearing test the index, through ops.plangovern); stdlib
re/json/os/sys only (INV-2, INV-6). Nothing here writes.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident
`plan` registrar (set_defaults(func=cmd_plan_lint)), the resident plan-authoring
callers (cmd_plan_new -> lint_plan), the resident fanout open-marker counter
(_PLAN_OPEN_MARKER_PY), and load_cli tests reach every name as CLI.<name>.
Fan-in: 3 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config import resolve_knowledge_dir
from agentware_lib.core.kblayout import _kb_schema_read
from agentware_lib.ops.plangovern import (   # noqa: F401  (R12..R16, re-exported)
    _PLAN_GRAPH_ERROR_SCHEMA,
    _WORKORDER_SLUG_RE,
    _lint_graph,
    _lint_premises,
    _lint_status,
    _lint_workorder,
    _plan_own_slug,
)
from agentware_lib.ops.plangraph import (
    _PLAN_TARGET_PATH_RE,
    _PLAN_TARGET_SECTION_RE,
    plan_target_dirs,
)
from agentware_lib.ops.planmarkers import (
    _check_task_numbering,
    _lint_add,
    _parse_task_number,
    _plan_open_marker_count,
    _PLAN_CHECKBOX_RE,
    _PLAN_HEADING_RE,
    _PLAN_LETTERED_RE,
    _PLAN_MARKER_HINT,
    _PLAN_OPEN_MARKER_PY,
    _PLAN_PROMISE_RE,
    _PLAN_TASK_LINE_RE,
    _PLAN_VERIFY_NONEMPTY_RE,
    _PLAN_VERIFY_RE,
    _posix_ere_to_python,
    PLAN_TASK_MARKER_RE,
)


_PLAN_R7_TOKENS = ("R-KB", "index validate", "learn", "features")
_PLAN_R9_BLOCKING = ("stop and ask", "operator decision required",
                     "await confirmation", "ask the user", "ask the operator")
_PLAN_R9_CARVEOUT = ("r-pkg-03", "r-exec-06", "pre-decided",
                     "proceed deterministically", "deterministic resolution",
                     "carve-out", "carveout")

# The EXACT emitter placeholder signatures `_plan_skeleton` writes for an unfilled
# Context / Target / Verify / Acceptance. `plan lint --readiness` fails while ANY
# of these SIGNATURES remain (signature match, NOT any mention of "TODO"); the
# em-dash and punctuation are load-bearing — keep byte-identical to _plan_skeleton.
_PLAN_READINESS_SIGNATURES = (
    "_TODO: problem, goal, design, constraints, environment, pitfalls._",
    "TODO: replace with the absolute or ~-rooted directory path(s) this "
    "plan targets",
    "*Verify:* TODO — exercise the feature end-to-end and capture the outcome.",
    "- [ ] TODO: define the verifiable acceptance criteria.",
)
_PLAN_ACCEPT_PLACEHOLDER = "TODO: define the verifiable acceptance criteria"


# --- R11: Target packages contract -----------------------------------------
# DEFINED in ops.plangraph, re-exported here. R11 is a RULE and lives in this
# module; the `## Target packages` PARSER is a declarative block parser and sits
# with its four siblings (`## Depends-on`, `## Write set`, `> Checkpoint:`,
# `> Status:`) one edge below. That direction is forced, not chosen: R13/R15/R16
# make this module import plangraph, and plangraph's `infer_write_set` needs
# `plan_target_dirs`, so defining it here would close a real import cycle. The
# names stay in _EXPORTS so `CLI.plan_target_dirs` and planauthor's `_planlint`
# alias resolve the SAME object they always did.


# --- R2: required sections + the Tasks-section line range --------------------

def _lint_sections(lines, errors):
    """R2 — locate `## Tasks` / `## Acceptance criteria` and bound the task scan.

    Returns (tasks_start, tasks_end, scan_range). `tasks_start` is None when the
    section is absent, in which case the scan degrades to the whole file so R3's
    malformed-marker hints still fire on a plan that lost its heading.
    """
    tasks_start = accept_start = None
    headings = []
    for i, ln in enumerate(lines):
        m = _PLAN_HEADING_RE.match(ln)
        if not m:
            continue
        title = m.group(1).strip().lower()
        headings.append(i)
        if title == "tasks" and tasks_start is None:
            tasks_start = i
        elif title.startswith("acceptance criteria") and accept_start is None:
            accept_start = i

    if tasks_start is None:
        _lint_add(errors, "R2", 0, "missing required section '## Tasks'")
    if accept_start is None:
        _lint_add(errors, "R2", 0,
                  "missing required section '## Acceptance criteria'")

    # Tasks section spans from just after '## Tasks' to the next '##' heading.
    if tasks_start is not None:
        tasks_end = len(lines)
        for idx in headings:
            if idx > tasks_start:
                tasks_end = idx
                break
        scan_range = range(tasks_start + 1, tasks_end)
    else:
        tasks_end = len(lines)
        scan_range = range(len(lines))
    return tasks_start, tasks_end, scan_range


# --- R3/R4: task markers and their numbering --------------------------------

def _lint_markers(lines, scan_range, tasks_start, errors):
    """R3/R4 — well-formed markers within `## Tasks`, and monotonic numbering.

    Returns the marker list as (line-index, number-string) pairs, which the task
    bodies are then sliced from. Malformed task-like lines are flagged INSIDE the
    scan so a `- [ ]` checkbox under `## Acceptance criteria` is never flagged.
    """
    markers = []   # (idx, number-string)  — number is flat "N" or phased "N.M"
    for i in scan_range:
        ln = lines[i]
        m = _PLAN_TASK_LINE_RE.match(ln)
        if m:
            markers.append((i, m.group(2)))
        elif _PLAN_CHECKBOX_RE.match(ln) or _PLAN_LETTERED_RE.match(ln):
            _lint_add(errors, "R3", i + 1,
                      "malformed task marker: %r — %s"
                      % (ln.strip(), _PLAN_MARKER_HINT))

    # R3 — at least one well-formed marker.
    if not markers:
        _lint_add(errors, "R3", (tasks_start or 0) + 1,
                  "no well-formed task markers found in '## Tasks' — %s"
                  % _PLAN_MARKER_HINT)

    # R4 — task numbers monotonic from 1, no gaps/dups (flat OR phased N.M).
    if markers:
        msg = _check_task_numbering([n for (_, n) in markers])
        if msg:
            _lint_add(errors, "R4", markers[0][0] + 1, msg)
    return markers


def _plan_tasks(lines, markers, tasks_end):
    """Per-task bodies: each marker line up to the next marker / section end."""
    tasks = []
    for k, (idx, num) in enumerate(markers):
        body_end = markers[k + 1][0] if k + 1 < len(markers) else tasks_end
        tasks.append({
            "line": idx + 1, "num": num,
            "body": "\n".join(lines[idx:body_end]),
        })
    return tasks


# --- R5/R6/R7: what every task, and the task set, must contain --------------

def _lint_task_bodies(lines, tasks, tasks_start, tasks_end, errors):
    """R5 (per-task `*Verify:*`), R6 (an `[e2e]` task), R7 (a KB update)."""
    for t in tasks:
        if not _PLAN_VERIFY_RE.search(t["body"]):
            _lint_add(errors, "R5", t["line"],
                      "task %s (line %d) has no `*Verify:*` line (every task "
                      "must be independently verifiable — R-VERIFY-01)"
                      % (t["num"], t["line"]))

    if tasks and not any("[e2e]" in t["body"]
                         and _PLAN_VERIFY_NONEMPTY_RE.search(t["body"])
                         for t in tasks):
        _lint_add(errors, "R6", (tasks_start or 0) + 1,
                  "no task carries an `[e2e]` token with a non-empty "
                  "`*Verify:*` line (the plan must verify end-to-end before "
                  "completion)")

    tasks_text = "\n".join(lines[(tasks_start or 0):tasks_end])
    if tasks and not any(tok in tasks_text for tok in _PLAN_R7_TOKENS):
        _lint_add(errors, "R7", (tasks_start or 0) + 1,
                  "no task references a knowledge-base update (expected one of: "
                  "R-KB, `index validate`, `learn`, `features` — R-KB-*/R-SI-02)")


def _lint_promise(text, errors):
    """R8 — exactly one <promise>CONTENT</promise> tag."""
    promises = _PLAN_PROMISE_RE.findall(text)
    if len(promises) != 1:
        _lint_add(errors, "R8", 0,
                  "expected exactly one <promise>CONTENT</promise> tag, found %d"
                  % len(promises))


def _lint_autonomy(tasks, strict, errors, warnings):
    """R9 — blocking/interactive phrasing without a deterministic resolution."""
    for t in tasks:
        low = t["body"].lower()
        if any(p in low for p in _PLAN_R9_BLOCKING) and \
                not any(c in low for c in _PLAN_R9_CARVEOUT):
            bucket = errors if strict else warnings
            _lint_add(bucket, "R9", t["line"],
                      "task %s (line %d) uses blocking/interactive phrasing "
                      "without a deterministic resolution or a cited "
                      "R-PKG-03/R-EXEC-06 carve-out (plans must execute "
                      "autonomously)" % (t["num"], t["line"]))


def _lint_authorship(header_text, strict, errors, warnings):
    """R10 — in team mode, plans should carry an author for provenance.

    Warn-only outside `--strict`; a no-op entirely when the KB is flat, so a
    single-operator install never sees it. Detected via a `> Author: <handle>`
    line in the header area (before `## Tasks`).
    """
    has_author = bool(re.search(r">\s*Author:\s*\S", header_text))
    if not has_author and config.resolve_kb_mode() == "team":
        bucket = errors if strict else warnings
        _lint_add(bucket, "R10", 1,
                  "team-mode plan has no `> Author: <handle>` line — provenance "
                  "is unclear (add via `plan new --author <handle>` or manually)")


def _lint_targets(text, lines, errors):
    """R11 — mandatory `## Target packages` section with a real path row."""
    target_dirs = plan_target_dirs(text)
    has_target_section = bool(_PLAN_TARGET_SECTION_RE.search(text))
    if not has_target_section:
        _lint_add(errors, "R11", 0,
                  "missing required section '## Target packages' — every plan "
                  "must declare the directory path(s) it targets (absolute or "
                  "~-rooted)")
    elif not target_dirs:
        # Section exists but has no valid path rows (e.g. only a TODO stub)
        # Find the section line for better error reporting
        sec_line = 0
        for i, ln in enumerate(lines):
            if _PLAN_TARGET_SECTION_RE.match(ln):
                sec_line = i + 1
                break
        _lint_add(errors, "R11", sec_line,
                  "'## Target packages' section has no valid directory paths — "
                  "add at least one absolute or ~-rooted path (e.g. "
                  "`- ~/workspace/pkg`)")


def lint_plan(text, strict=False, rel_path=None, schema_version=0, kdir=None):
    """Apply the structural plan-format rules R1–R16 to `text`.

    Returns (errors, warnings); each is a list of {rule, line, message}. R1 (file
    existence) is handled by the caller before this runs. Read-only (INV-2): never
    mutates anything. R9 is a warning by default and an error under `strict`.

    `rel_path` (the plan's KB-relative POSIX path, e.g. `work/auto/<slug>/plan.md`)
    drives R12 (workorder provenance headers required under work/auto/), whose
    severity flips WARN->error once `schema_version >= 1` (the deterministic,
    versioned flip — no config flag), and gives R15 the plan's own slug so it can
    recognise a self-edge. R13 binds the declared `> Status:` header to derived
    task reality. R14 (feature 260717) requires a LOAD-BEARING plan (self-extension
    OR names a live KB id — needs `kdir` for the id scan) to carry a well-formed
    cited `## Premises` block, on the SAME WARN->ERROR versioned ramp keyed on
    `schema_version >= _PLAN_PREMISES_ERROR_SCHEMA`. R15/R16 (feature 260722) check
    the plan-graph blocks on their own ramp.

    THE ORDER BELOW IS THE CONTRACT. Findings are returned as ordered lists and
    compared as such (the task-39 differential probe diffs this function's output
    against the monolith's element by element), so a rule receiver may be renamed
    or re-implemented but never re-sequenced.
    """
    errors, warnings = [], []
    lines = text.splitlines()

    tasks_start, tasks_end, scan_range = _lint_sections(lines, errors)
    markers = _lint_markers(lines, scan_range, tasks_start, errors)
    tasks = _plan_tasks(lines, markers, tasks_end)
    header_text = "\n".join(lines[:(tasks_start or len(lines))])

    _lint_task_bodies(lines, tasks, tasks_start, tasks_end, errors)
    _lint_promise(text, errors)
    _lint_autonomy(tasks, strict, errors, warnings)
    _lint_authorship(header_text, strict, errors, warnings)
    _lint_targets(text, lines, errors)
    _lint_workorder(rel_path, header_text, schema_version, errors, warnings)
    _lint_status(text, strict, errors, warnings)
    _lint_premises(text, strict, schema_version, kdir, errors, warnings)
    _lint_graph(text, rel_path, strict, schema_version, errors, warnings)
    return errors, warnings


def _emit_plan_lint(result, as_json):
    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PLAN LINT %s" % ("PASSED" if result["ok"] else "FAILED"))
        for e in result["errors"]:
            print("  [%s] line %d: %s" % (e["rule"], e["line"], e["message"]))
        for w in result["warnings"]:
            print("  [warn %s] line %d: %s" % (w["rule"], w["line"], w["message"]))
    return 0 if result["ok"] else 1


def _plan_readiness(text):
    """draft->ready gate: (ok, reasons). Fails while ANY emitter placeholder
    SIGNATURE remains, or there is no real acceptance-criteria checkbox. Prose that
    merely mentions a placeholder token still passes (signature match)."""
    reasons = []
    for sig in _PLAN_READINESS_SIGNATURES:
        if sig in (text or ""):
            reasons.append("unfilled skeleton placeholder still present: %r"
                           % (sig[:60] + ("…" if len(sig) > 60 else "")))
    real_accept = 0
    in_accept = False
    for ln in (text or "").splitlines():
        hm = _PLAN_HEADING_RE.match(ln)
        if hm:
            in_accept = "acceptance" in hm.group(1).lower()
            continue
        if in_accept and _PLAN_CHECKBOX_RE.match(ln):
            if _PLAN_ACCEPT_PLACEHOLDER in ln:
                continue
            real_accept += 1
    if real_accept < 1:
        reasons.append("no real acceptance-criteria checkbox "
                       "(only the skeleton placeholder)")
    return (not reasons, reasons)


def _plan_rel_path(path):
    """The plan's knowledge-dir-relative POSIX path (e.g. work/auto/<slug>/
    plan.md), or None when unresolved or outside the KB (so R12 degrades to a
    no-op for an arbitrary --path)."""
    kdir = resolve_knowledge_dir()
    if not kdir:
        return None
    try:
        rel = os.path.relpath(os.path.abspath(path), os.path.abspath(kdir))
    except (ValueError, OSError):
        return None
    rel = rel.replace(os.sep, "/")
    if rel.startswith("../") or rel == "..":
        return None
    return rel


def _plan_lint_read(path, as_json):
    """R1 — read the plan, or emit the R1 finding. Returns (text, rc): exactly one
    of the two is None, so the caller branches once."""
    if not os.path.isfile(path):
        return None, _emit_plan_lint(
            {"ok": False, "warnings": [],
             "errors": [{"rule": "R1", "line": 0,
                         "message": "plan file not found or not readable: %s"
                                    % path}]}, as_json)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read(), None
    except OSError as exc:
        return None, _emit_plan_lint(
            {"ok": False, "warnings": [],
             "errors": [{"rule": "R1", "line": 0,
                         "message": "cannot read plan file %s: %s" % (path, exc)}]},
            as_json)


def cmd_plan_lint(args):
    path = args.path
    strict = bool(getattr(args, "strict", False))
    readiness = bool(getattr(args, "readiness", False))
    as_json = getattr(args, "format", "text") == "json"

    text, rc = _plan_lint_read(path, as_json)
    if text is None:
        return rc

    # --readiness — the mechanical draft->ready gate (placeholder SIGNATURES gone
    # + >=1 real acceptance checkbox). Distinct from structural lint.
    if readiness:
        ok, reasons = _plan_readiness(text)
        errs = [{"rule": "readiness", "line": 0, "message": r} for r in reasons]
        return _emit_plan_lint(
            {"ok": ok, "errors": errs, "warnings": []}, as_json)

    rel_path = _plan_rel_path(path)
    kdir = resolve_knowledge_dir()
    schema_version = _kb_schema_read(kdir) if kdir else 0
    errors, warnings = lint_plan(text, strict=strict, rel_path=rel_path,
                                 schema_version=schema_version, kdir=kdir)
    result = {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
    return _emit_plan_lint(result, as_json)


_EXPORTS = [
    "PLAN_TASK_MARKER_RE", "_posix_ere_to_python", "_PLAN_OPEN_MARKER_PY",
    "_PLAN_TASK_LINE_RE", "_PLAN_CHECKBOX_RE", "_PLAN_LETTERED_RE",
    "_PLAN_HEADING_RE", "_PLAN_VERIFY_RE", "_PLAN_VERIFY_NONEMPTY_RE",
    "_PLAN_PROMISE_RE", "_PLAN_MARKER_HINT", "_PLAN_R7_TOKENS",
    "_PLAN_R9_BLOCKING", "_PLAN_R9_CARVEOUT", "_parse_task_number",
    "_check_task_numbering", "_PLAN_TARGET_SECTION_RE", "_PLAN_TARGET_PATH_RE",
    "plan_target_dirs", "_lint_add", "_lint_sections", "_lint_markers",
    "_plan_tasks", "_lint_task_bodies", "_lint_promise", "_lint_autonomy",
    "_lint_authorship", "_lint_targets", "_WORKORDER_SLUG_RE", "_lint_workorder",
    "_plan_open_marker_count", "_lint_status", "_lint_premises",
    "_PLAN_GRAPH_ERROR_SCHEMA", "_plan_own_slug", "_lint_graph", "lint_plan",
    "_emit_plan_lint", "_PLAN_READINESS_SIGNATURES", "_PLAN_ACCEPT_PLACEHOLDER",
    "_plan_readiness", "_plan_rel_path", "_plan_lint_read", "cmd_plan_lint",
]
__all__ = list(_EXPORTS)
