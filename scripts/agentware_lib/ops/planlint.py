"""agentware_lib.ops.planlint -- feature-plan structural lint (layer: ops).

Owns: the plan-lint ENFORCEMENT half of the plan toolkit (feature 260625-plan-lint-gate).
The canonical open-task-marker regex PLAN_TASK_MARKER_RE -- the SINGLE SOURCE OF TRUTH
whose bytes MUST stay byte-identical to agentware.sh:open_markers()'s grep pattern (pinned
by the drift contract test tests/test_plan_lint.py:631) -- plus the POSIX-ERE -> Python
translator (_posix_ere_to_python) the linter uses at load time, every derived plan regex
(_PLAN_*_RE / _PLAN_R7_TOKENS / _PLAN_R9_* / _PLAN_MARKER_HINT), task-number parsing +
monotonic-numbering check (_parse_task_number / _check_task_numbering), the R11
target-packages contract (_PLAN_TARGET_*_RE / plan_target_dirs), the R1..R11 linter
(lint_plan), its text/JSON emitter (_emit_plan_lint) and the CLI handler (cmd_plan_lint).

Does NOT own: plan AUTHORING -- the deterministic writers skeleton/new/add-task/set-status/
target-dirs + work-artifact stamping (still RESIDENT in scripts/agentware this seam; they
move to agentware_lib.ops.planauthor, which imports THESE regexes downward as a same-layer
ops->ops edge -- e.g. cmd_plan_new calls lint_plan, _PLAN_TASK_LINE_RE is the shared marker
source). The plan-tainted derive_* reporting family (derive_plan_size / derive_plan_authoring
/ derive_agents / derive_outcome / _plan_marker_status_counts) stays RESIDENT until it moves
into agentware_lib.ops.derive, which will import _PLAN_TASK_LINE_RE / _parse_task_number from
HERE (this seam UNBLOCKS that move by providing the regexes). The org/template lint that lives
textually between this domain's blocks is agentware_lib.ops.fleet (org lint) / RESIDENT
(deferred), never here.

Layer deps: ops.planlint -> core only (core.config.resolve_kb_mode for the R2 team-mode
author check). Nothing above ops imports it; ops.planauthor and ops.derive import it downward.

Determinism: pure text linting over plan/markdown strings, stdlib re/json/os/sys only
(INV-2, INV-6). PLAN_TASK_MARKER_RE bytes are the single source shared with agentware.sh's
grep so linter and loop can never drift.

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident `plan`
registrar (set_defaults(func=cmd_plan_lint)), the resident plan-authoring callers
(cmd_plan_new -> lint_plan), the resident fanout open-marker counter (_PLAN_OPEN_MARKER_PY),
and load_cli tests reach every name as CLI.<name>.
Fan-in: 3 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import sys

import agentware_lib.core.config as config


# --- Plan lint (feature 260625-plan-lint-gate) -------------------------------
# Canonical task-marker regex — the SINGLE SOURCE OF TRUTH for "what counts as an
# open task marker" in a feature plan. Its bytes MUST stay byte-identical to the
# `grep -cE '…'` pattern in agentware.sh:open_markers() (pinned by the contract
# test in tests/test_plan_lint.py). This is the POSIX-ERE form exactly as grep
# consumes it; Python's `re` does NOT honour POSIX `[[:class:]]` syntax, so the
# linter derives a Python-usable pattern via _posix_ere_to_python() at load time
# rather than feeding these bytes to re.compile directly.
PLAN_TASK_MARKER_RE = r"^[[:space:]]*-[[:space:]]*(⬜|🟡)[[:space:]]*\*\*[0-9]"


def _posix_ere_to_python(pattern):
    """Deterministically translate the POSIX-ERE classes we use into Python re.

    grep's POSIX bracket classes (`[[:space:]]`, `[[:digit:]]`, `[[:alpha:]]`)
    are not honoured by Python's `re`; translate the ones the canonical marker
    regex uses so the linter and the loop derive from the same source string.
    """
    return (pattern
            .replace("[[:space:]]", r"\s")
            .replace("[[:digit:]]", r"\d")
            .replace("[[:alpha:]]", "[A-Za-z]"))


# Open-marker matcher (⬜/🟡), derived from the canonical constant so it can never
# drift from the loop. Used only to assert the translation round-trips.
_PLAN_OPEN_MARKER_PY = re.compile(_posix_ere_to_python(PLAN_TASK_MARKER_RE))

# Well-formed task marker of ANY status. Extends the open-marker form with the
# done glyph ✅, since a complete plan's tasks are legitimately well-formed and
# must still lint clean (all-✅ plans). Captures the task number, which may be a
# FLAT `**N**` or a PHASED `**N.M**` marker (the documented phased form in
# docs/loop.md, e.g. `**1.1**`, `**1.2**`, `**2.1**`). The loop's grep counter
# (`PLAN_TASK_MARKER_RE`, `\*\*[0-9]`) already matches both, so this re-aligns the
# linter with the loop and with the documented task format.
_PLAN_TASK_LINE_RE = re.compile(r"^\s*-\s*(⬜|🟡|✅)\s*\*\*(\d+(?:\.\d+)?)\*\*")
# Malformed task-like lines we flag with a specific fix hint (R3).
_PLAN_CHECKBOX_RE = re.compile(r"^\s*-\s*\[[ xX]?\]")        # GitHub `- [ ]`
# A lettered TASK ID someone wrote instead of the canonical marker — `- **T1**`,
# `- **T12**`, `- **A3**`: a compact identifier (letters then digits) inside the
# bold, mirroring the `- **N**` shape but with a letter prefix. The trailing
# `\d+` requirement is deliberate: it flags real task-id mistakes WITHOUT
# false-flagging ordinary prose bullets like `- **Note:** …` or
# `- **Foundation first:** …` (a word/phrase, no compact `<letters><digits>**`).
_PLAN_LETTERED_RE = re.compile(r"^\s*-\s*\*\*[A-Za-z]+\d+\*\*")  # `- **T1**`
_PLAN_HEADING_RE = re.compile(r"^##\s+(.*\S)\s*$")
_PLAN_VERIFY_RE = re.compile(r"\*Verify:\*")
_PLAN_VERIFY_NONEMPTY_RE = re.compile(r"\*Verify:\*\s*\S")
_PLAN_PROMISE_RE = re.compile(r"<promise>[A-Z0-9_]+</promise>")

_PLAN_MARKER_HINT = ("expected a well-formed marker like `- ⬜ **N**` "
                     "(emoji ⬜/🟡/✅ + bold digit)")
_PLAN_R7_TOKENS = ("R-KB", "index validate", "learn", "features")
_PLAN_R9_BLOCKING = ("stop and ask", "operator decision required",
                     "await confirmation", "ask the user", "ask the operator")
_PLAN_R9_CARVEOUT = ("r-pkg-03", "r-exec-06", "pre-decided",
                     "proceed deterministically", "deterministic resolution",
                     "carve-out", "carveout")


def _parse_task_number(s):
    """'1' -> (1,); '1.2' -> (1, 2). Tuple form drives the R4 ordering check."""
    return tuple(int(p) for p in s.split("."))


def _check_task_numbering(number_strings):
    """Validate task-number ordering for FLAT or PHASED markers (R4).

    Returns an error message string, or None when the numbering is well-formed.

    - Flat plans (`**1**`, `**2**`, …): numbers must be 1..N, no gaps/dups.
    - Phased plans (`**1.1**`, `**1.2**`, `**2.1**`, …): the MAJOR groups must
      appear contiguously as 1..K, and within each major the MINORS must run
      1..m (so `set-status` / the burndown stay unambiguous). A flat marker is
      not mixed into a phased plan.
    """
    parsed = [_parse_task_number(s) for s in number_strings]
    phased = any(len(p) > 1 for p in parsed)
    if not phased:
        nums = [p[0] for p in parsed]
        expected = list(range(1, len(nums) + 1))
        if nums != expected:
            return ("task numbers must increase monotonically from 1 with no "
                    "gaps or duplicates; found %s, expected %s"
                    % (nums, expected))
        return None
    # Phased: walk the markers, grouping contiguous runs by major number.
    majors_in_order = []
    minors = {}
    for p in parsed:
        if len(p) != 2:
            return ("phased plans must use `**N.M**` markers throughout; found "
                    "a flat marker %r mixed in" % ".".join(map(str, p)))
        major, minor = p
        if not majors_in_order or majors_in_order[-1] != major:
            if major in majors_in_order:
                return ("phased major groups must be contiguous; major %d "
                        "reappears out of order" % major)
            majors_in_order.append(major)
        minors.setdefault(major, []).append(minor)
    if majors_in_order != list(range(1, len(majors_in_order) + 1)):
        return ("phased major numbers must increase from 1 with no gaps; "
                "found %s" % majors_in_order)
    for major in majors_in_order:
        ms = minors[major]
        if ms != list(range(1, len(ms) + 1)):
            return ("phased minors under major %d must run 1..%d with no gaps "
                    "or duplicates; found %s" % (major, len(ms), ms))
    return None


# --- R11: Target packages contract -----------------------------------------
_PLAN_TARGET_SECTION_RE = re.compile(r"^##\s+Target\s+packages\s*$", re.IGNORECASE | re.MULTILINE)
_PLAN_TARGET_PATH_RE = re.compile(r"^\s*-\s+(~[/\\]|/)\S")


def plan_target_dirs(text):
    """Extract declared target-package directory paths from a plan.

    Parses the `## Target packages` section and returns a list of expanded
    directory paths. Expands `~` to $HOME and environment variables. Returns
    an empty list when the section is missing or has no path rows.
    """
    lines = text.splitlines()
    in_section = False
    dirs = []
    for ln in lines:
        if _PLAN_TARGET_SECTION_RE.match(ln):
            in_section = True
            continue
        if in_section:
            # End of section on next heading
            if re.match(r"^##\s+", ln):
                break
            if _PLAN_TARGET_PATH_RE.match(ln):
                # Extract the path (strip leading `- `)
                path = ln.strip().lstrip("-").strip()
                # Expand ~ and env vars
                path = os.path.expanduser(os.path.expandvars(path))
                dirs.append(path)
    return dirs


def lint_plan(text, strict=False):
    """Apply the structural plan-format rules R1–R11 to `text`.

    Returns (errors, warnings); each is a list of {rule, line, message}. R1 (file
    existence) is handled by the caller before this runs. Read-only (INV-2): never
    mutates anything. R9 is a warning by default and an error under `strict`.
    """
    errors, warnings = [], []

    def add(bucket, rule, line, message):
        bucket.append({"rule": rule, "line": int(line), "message": message})

    lines = text.splitlines()

    # Locate the required sections (R2) and the Tasks-section line range.
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
        add(errors, "R2", 0, "missing required section '## Tasks'")
    if accept_start is None:
        add(errors, "R2", 0, "missing required section '## Acceptance criteria'")

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

    # Find well-formed markers and malformed task-like lines within Tasks only
    # (so legit `- [ ]` checkboxes under '## Acceptance criteria' are NOT flagged).
    markers = []   # (idx, number-string)  — number is flat "N" or phased "N.M"
    for i in scan_range:
        ln = lines[i]
        m = _PLAN_TASK_LINE_RE.match(ln)
        if m:
            markers.append((i, m.group(2)))
        elif _PLAN_CHECKBOX_RE.match(ln) or _PLAN_LETTERED_RE.match(ln):
            add(errors, "R3", i + 1,
                "malformed task marker: %r — %s" % (ln.strip(), _PLAN_MARKER_HINT))

    # R3 — at least one well-formed marker.
    if not markers:
        add(errors, "R3", (tasks_start or 0) + 1,
            "no well-formed task markers found in '## Tasks' — %s"
            % _PLAN_MARKER_HINT)

    # R4 — task numbers monotonic from 1, no gaps/dups (flat OR phased N.M).
    if markers:
        msg = _check_task_numbering([n for (_, n) in markers])
        if msg:
            add(errors, "R4", markers[0][0] + 1, msg)

    # Build per-task bodies (marker line .. next marker / section end).
    tasks = []
    for k, (idx, num) in enumerate(markers):
        body_end = markers[k + 1][0] if k + 1 < len(markers) else tasks_end
        tasks.append({
            "line": idx + 1, "num": num,
            "body": "\n".join(lines[idx:body_end]),
        })

    # R5 — every task has a `*Verify:*` line.
    for t in tasks:
        if not _PLAN_VERIFY_RE.search(t["body"]):
            add(errors, "R5", t["line"],
                "task %s (line %d) has no `*Verify:*` line (every task must be "
                "independently verifiable — R-VERIFY-01)" % (t["num"], t["line"]))

    # R6 — at least one `[e2e]` task with a non-empty Verify line.
    if tasks and not any("[e2e]" in t["body"]
                         and _PLAN_VERIFY_NONEMPTY_RE.search(t["body"])
                         for t in tasks):
        add(errors, "R6", (tasks_start or 0) + 1,
            "no task carries an `[e2e]` token with a non-empty `*Verify:*` line "
            "(the plan must verify end-to-end before completion)")

    # R7 — at least one task references a knowledge-base update.
    tasks_text = "\n".join(lines[(tasks_start or 0):tasks_end])
    if tasks and not any(tok in tasks_text for tok in _PLAN_R7_TOKENS):
        add(errors, "R7", (tasks_start or 0) + 1,
            "no task references a knowledge-base update (expected one of: "
            "R-KB, `index validate`, `learn`, `features` — R-KB-*/R-SI-02)")

    # R8 — exactly one <promise>CONTENT</promise> tag.
    promises = _PLAN_PROMISE_RE.findall(text)
    if len(promises) != 1:
        add(errors, "R8", 0,
            "expected exactly one <promise>CONTENT</promise> tag, found %d"
            % len(promises))

    # R9 — blocking/interactive phrasing without a deterministic resolution.
    for t in tasks:
        low = t["body"].lower()
        if any(p in low for p in _PLAN_R9_BLOCKING) and \
                not any(c in low for c in _PLAN_R9_CARVEOUT):
            bucket = errors if strict else warnings
            add(bucket, "R9", t["line"],
                "task %s (line %d) uses blocking/interactive phrasing without a "
                "deterministic resolution or a cited R-PKG-03/R-EXEC-06 carve-out "
                "(plans must execute autonomously)" % (t["num"], t["line"]))

    # R10 — in team mode, plans should carry an author for provenance (warn-only).
    # Detect via `> Author: <handle>` line in the header area (before ## Tasks).
    header_text = "\n".join(lines[:(tasks_start or len(lines))])
    has_author = bool(re.search(r">\s*Author:\s*\S", header_text))
    if not has_author and config.resolve_kb_mode() == "team":
        bucket = errors if strict else warnings
        add(bucket, "R10", 1,
            "team-mode plan has no `> Author: <handle>` line — provenance is "
            "unclear (add via `plan new --author <handle>` or manually)")

    # R11 — mandatory `## Target packages` section with at least one path row.
    target_dirs = plan_target_dirs(text)
    has_target_section = bool(_PLAN_TARGET_SECTION_RE.search(text))
    if not has_target_section:
        add(errors, "R11", 0,
            "missing required section '## Target packages' — every plan must "
            "declare the directory path(s) it targets (absolute or ~-rooted)")
    elif not target_dirs:
        # Section exists but has no valid path rows (e.g. only a TODO stub)
        # Find the section line for better error reporting
        sec_line = 0
        for i, ln in enumerate(lines):
            if _PLAN_TARGET_SECTION_RE.match(ln):
                sec_line = i + 1
                break
        add(errors, "R11", sec_line,
            "'## Target packages' section has no valid directory paths — add "
            "at least one absolute or ~-rooted path (e.g. `- ~/workspace/pkg`)")

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


def cmd_plan_lint(args):
    path = args.path
    strict = bool(getattr(args, "strict", False))
    as_json = getattr(args, "format", "text") == "json"

    # R1 — the file exists and is readable.
    if not os.path.isfile(path):
        return _emit_plan_lint(
            {"ok": False, "warnings": [],
             "errors": [{"rule": "R1", "line": 0,
                         "message": "plan file not found or not readable: %s"
                                    % path}]}, as_json)
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        return _emit_plan_lint(
            {"ok": False, "warnings": [],
             "errors": [{"rule": "R1", "line": 0,
                         "message": "cannot read plan file %s: %s" % (path, exc)}]},
            as_json)

    errors, warnings = lint_plan(text, strict=strict)
    result = {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}
    return _emit_plan_lint(result, as_json)


_EXPORTS = [
    "PLAN_TASK_MARKER_RE", "_posix_ere_to_python", "_PLAN_OPEN_MARKER_PY",
    "_PLAN_TASK_LINE_RE", "_PLAN_CHECKBOX_RE", "_PLAN_LETTERED_RE",
    "_PLAN_HEADING_RE", "_PLAN_VERIFY_RE", "_PLAN_VERIFY_NONEMPTY_RE",
    "_PLAN_PROMISE_RE", "_PLAN_MARKER_HINT", "_PLAN_R7_TOKENS",
    "_PLAN_R9_BLOCKING", "_PLAN_R9_CARVEOUT", "_parse_task_number",
    "_check_task_numbering", "_PLAN_TARGET_SECTION_RE", "_PLAN_TARGET_PATH_RE",
    "plan_target_dirs", "lint_plan", "_emit_plan_lint",
    "cmd_plan_lint",
]
__all__ = list(_EXPORTS)
