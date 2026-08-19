"""agentware_lib.ops.planmarkers -- plan-document grammar primitives (layer: ops).

Owns: the SHARED VOCABULARY of the plan-lint family -- everything a rule READS
about a plan.md's task list, and the one record shape every rule WRITES.

  reads   PLAN_TASK_MARKER_RE (the SINGLE SOURCE OF TRUTH for "what counts as an
          open task marker", whose bytes MUST stay byte-identical to
          agentware.sh:open_markers()'s `grep -cE` pattern), the POSIX-ERE ->
          Python translator the linter applies to it at load time
          (_posix_ere_to_python), every derived plan regex (_PLAN_TASK_LINE_RE /
          _PLAN_CHECKBOX_RE / _PLAN_LETTERED_RE / _PLAN_HEADING_RE /
          _PLAN_VERIFY_* / _PLAN_PROMISE_RE / _PLAN_MARKER_HINT), task-number
          parsing and the monotonic-numbering check (_parse_task_number /
          _check_task_numbering), and the open-marker counter R13 and the loop's
          done-gate both ask for (_plan_open_marker_count).
  writes  _lint_add -- the {rule, line, message} finding record. One definition,
          so every rule in the family emits the same shape and a reader auditing
          "what does a finding look like" reads one function.

Does NOT own: any RULE. R1..R16 live in ops.planlint (the enforcer), which
imports this module downward. The DECLARATIVE BLOCK parsers -- `## Target
packages`, `## Depends-on:`, `## Write set`, `> Checkpoint:`, `> Status:` --
live one module over in ops.plangraph; those describe the plan AS A NODE, while
these describe its TASKS. Plan AUTHORING (the writers that emit markers rather
than read them) is ops.planauthor, which imports this through planlint's
re-export.

WHY THIS IS A SEPARATE MODULE (feature 260813 task 39)
------------------------------------------------------
Splitting the grammar out of the linter is what lets the enforcer grow to its
full R1..R16 rule set without pushing planlint at the 600-line lib ceiling, and
it gives the ONE surface with a cross-language drift contract its own file: the
bytes in PLAN_TASK_MARKER_RE are shared with a grep pattern in agentware.sh, and
an auditor asking "can the linter and the loop disagree about what an open task
is" should be able to answer it by reading a single short module.

Layer deps: ops.planmarkers -> stdlib `re` only. Nothing from agentware_lib is
imported, so this is a true leaf: every plan module may import it and none of
them can create a cycle through it.

Determinism: pure text in, records out. No I/O, no clock, no KB access (INV-2).

_EXPORTS is imported BY NAME onto the scripts/agentware facade (via planlint's
re-export, which preserves `is`-identity) so every historical `CLI.<name>` call
site -- the resident fanout open-marker counter, the plan-authoring writers and
the load_cli tests -- resolves the SAME object it always did.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import re


# --- Plan lint (feature 260625-plan-lint-gate) -------------------------------
# Canonical task-marker regex — the SINGLE SOURCE OF TRUTH for "what counts as an
# open task marker" in a feature plan. Its bytes MUST stay byte-identical to the
# `grep -cE '…'` pattern in agentware.sh:open_markers() (pinned by the contract
# test in tests/unit/ops/test_plan_lint.py). This is the POSIX-ERE form exactly as grep
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


def _lint_add(bucket, rule, line, message):
    """Append one {rule, line, message} finding to `bucket` (errors or warnings).

    The single definition of a plan-lint finding. `bucket` is chosen by the
    CALLER, which is how the WARN->ERROR severity ramps (R9/R10 under --strict,
    R12/R14/R15/R16 under the versioned schema ramps) are expressed: the rule
    picks a list, never a severity flag, so a rule can never emit a record shape
    the emitter does not know how to render.
    """
    bucket.append({"rule": rule, "line": int(line), "message": message})


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


def _plan_open_marker_count(text):
    """Count OPEN (⬜/🟡) task markers in `text` via the canonical marker regex."""
    open_n = 0
    for ln in (text or "").splitlines():
        m = _PLAN_TASK_LINE_RE.match(ln)
        if m and m.group(1) != "✅":
            open_n += 1
    return open_n


_EXPORTS = [
    "PLAN_TASK_MARKER_RE", "_posix_ere_to_python", "_PLAN_OPEN_MARKER_PY",
    "_PLAN_TASK_LINE_RE", "_PLAN_CHECKBOX_RE", "_PLAN_LETTERED_RE",
    "_PLAN_HEADING_RE", "_PLAN_VERIFY_RE", "_PLAN_VERIFY_NONEMPTY_RE",
    "_PLAN_PROMISE_RE", "_PLAN_MARKER_HINT", "_lint_add",
    "_parse_task_number", "_check_task_numbering", "_plan_open_marker_count",
]
__all__ = list(_EXPORTS)
