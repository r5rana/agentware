"""agentware_lib.ops.steering -- Deterministic Steering Format (DSF) lint (layer: ops).

Owns: the steering-lint domain that keeps the always-loaded steering corpus
(CLAUDE.md/AGENTS.md + steering/*.md) in Deterministic Steering Format. The DSF
vocabularies (DSF_VERBS allowed directive verbs, DSF_BANNED_WORDS hedge words),
the STEERING_CHAR_BUDGET, the always-loaded set (STEERING_EXPLICIT + STEERING_GLOB),
the rule-id / bullet regexes (DSF_RULE_ID_RE / DSF_BULLET_RE); the Gate-1
content/intent-preservation constants (REQUIRED_RULE_IDS protected IDs,
REQUIRED_SECTIONS AGENTS.md headings) + the rule-count no-regression margin
(RULE_COUNT_REGRESSION_MARGIN); the lint engine (_steering_files / _iter_rule_lines /
_first_token_verb / lint_steering) and the committed rule-count baseline
read/write (_read_rule_count_baseline / _write_rule_count_baseline); the
cmd_steering_lint handler and the `steering` verb registrar (register_steering).

Does NOT own: the release-gate harness that CONSUMES lint_steering -- _gate1_content_lint
+ the other gates + cmd_gate_release stay RESIDENT in scripts/agentware this seam and move
to agentware_lib.ops.releasegate, which imports lint_steering from HERE downward (this
seam UNBLOCKS that move). The audit check _audit_steering_lint_check (also -> lint_steering)
likewise stays resident and moves to agentware_lib.ops.audit. The repo-root anchor
REPO_ROOT lives in agentware_lib.core.paths (marker-walk); STEERING_GLOB derives from it.

Layer deps: ops.steering -> core.paths (REPO_ROOT) only -- a downward edge. Nothing above
ops imports it; ops.releasegate + ops.audit import lint_steering downward (later seams).
REPO_ROOT is imported BY NAME (a plain str constant -- the global-resolve gate never
descends into a non-code object, so no cascade), and the moved functions reference it,
os, re, glob, json and sys exactly as they did in the monolith (pure verbatim, zero
requalification).

Determinism: read-only reporting (INV-2) -- lints files, NEVER mutates the corpus.
_steering_files enumerates STEERING_EXPLICIT (if present) then sorted(glob(STEERING_GLOB)),
so the file order and thus the lint output are deterministic. lint_steering is a pure
function of the on-disk corpus + the committed .rule-count-baseline; no wall-clock, no
network. _write_rule_count_baseline is the only writer and touches ONLY .rule-count-baseline
(deliberate, operator-invoked baseline update -- never called during a lint).

_EXPORTS is imported BY NAME onto the scripts/agentware facade so the resident consumers
_audit_steering_lint_check + _gate1_content_lint (both -> lint_steering) resolve every name
as a bare name and load_cli tests reach them as CLI.<name>. register_steering is NOT in
_EXPORTS: it moved WITH its handler and is declared in this module's `__verbs__`,
so the `steering` --help surface is unchanged.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import glob
import json
import os
import re
import sys

from agentware_lib.core.paths import REPO_ROOT


# --- Steering (Deterministic Steering Format) lint config --------------------
# These operate on the IN-REPO always-loaded steering, which is framework and
# ships with the clone — unrelated to the external knowledge dir.

# Allowed directive verbs for Deterministic Steering Format (DSF-2).
DSF_VERBS = ("MUST", "NEVER", "ALWAYS", "RUN", "ASK", "STOP", "READ", "IF")

# Banned hedge words for always-loaded steering (DSF-5).
DSF_BANNED_WORDS = (
    "should", "try", "consider", "ideally", "generally",
    "usually", "maybe", "prefer", "when possible", "as needed",
)

# Total character budget for always-loaded steering (DSF-budget).
STEERING_CHAR_BUDGET = 25000

# Always-loaded steering set under Claude Code: `CLAUDE.md` is auto-loaded and
# imports `@AGENTS.md` plus the files in `steering/`. The linter lints all of
# them so the always-on layer can never drift out of the Deterministic Steering
# Format. (The `@`-import lines in CLAUDE.md are bare paths, not bullets, so they
# are not treated as rules.)
STEERING_EXPLICIT = ("CLAUDE.md", "AGENTS.md")
STEERING_GLOB = os.path.join(REPO_ROOT, "steering", "*.md")

# DSF-6: every rule bullet carries a stable rule ID like R-KB-01.
DSF_RULE_ID_RE = re.compile(r"R-[A-Z]+-\d+")
# A lintable rule line is a markdown bullet (`-` or `*`). Numbered lists,
# headings, tables, blockquotes, code fences and prose are NOT rules.
DSF_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+)$")

# --- Content/intent preservation (Gate 1 of the release gate) ---------------
# Protected rule IDs: removal from the corpus is a hard FAIL regardless of any
# other edits. These encode foundational safety invariants.
REQUIRED_RULE_IDS = (
    "R-PKG-01", "R-PKG-02", "R-PKG-03", "R-PKG-04", "R-PKG-05", "R-PKG-06",
    "R-EXEC-01", "R-EXEC-02", "R-EXEC-05",
    "R-FAIL-01",
    "R-VERIFY-01",
    "R-AUTO-02",
    "R-KB-01",
    "R-LOC-01", "R-LOC-03",
    "R-SEC-02",
    "R-PLAN-01",
)

# Required section headings in AGENTS.md (checked by content-preservation lint).
REQUIRED_SECTIONS = (
    "Execution loop",
    "Build and complete",
    "Planning",
    "Knowledge base location",
    "Package immutability & self-extension",
    "Drill-down architecture",
    "Toolkit mandate (structured data)",
    "Context discovery",
    "Verification gates",
    "Failure handling (escalation ladder)",
    "Anti-patterns",
    "Git and dependencies",
    "Self-improvement",
    "Autonomous decisions",
    "Conventions",
)

# Rule-count no-regression margin: the corpus may drop this many rule-IDs from
# the committed baseline without failing (accounts for normal consolidation).
RULE_COUNT_REGRESSION_MARGIN = 3


# --- Steering DSF lint -------------------------------------------------------
def _steering_files():
    files = []
    for name in STEERING_EXPLICIT:
        p = os.path.join(REPO_ROOT, name)
        if os.path.exists(p):
            files.append(p)
    files.extend(sorted(glob.glob(STEERING_GLOB)))
    return files


def _iter_rule_lines(text):
    lines = text.splitlines()
    in_front = in_code = False
    for i, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if i == 1 and stripped == "---":
            in_front = True
            continue
        if in_front:
            if stripped == "---":
                in_front = False
            continue
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        m = DSF_BULLET_RE.match(raw)
        if not m:
            continue
        body = m.group(1).strip()
        if body:
            yield i, body


def _first_token_verb(body):
    first = body.split()[0]
    return first.strip("*_`").rstrip(":.,")


def _dsf_scan_rule_line(path, lineno, body, violations, banned_words, all_rule_ids):
    """DSF-2 / DSF-5 / DSF-6 for ONE rule bullet.

    A single line can violate all three, and each is appended independently: a
    bullet that leads with a hedge word AND carries no rule id is two findings,
    not one, because they are fixed by two different edits. DSF-5 is likewise
    per-WORD, so one bullet can contribute several banned-word rows.
    """
    verb = _first_token_verb(body)
    if verb not in DSF_VERBS:
        violations.append({"rule": "DSF-2", "file": path, "line": lineno,
                           "text": body})
    ids_in_line = DSF_RULE_ID_RE.findall(body)
    if not ids_in_line:
        violations.append({"rule": "DSF-6", "file": path, "line": lineno,
                           "text": body})
    all_rule_ids.update(ids_in_line)
    low = body.lower()
    for w in DSF_BANNED_WORDS:
        if re.search(r"\b" + re.escape(w) + r"\b", low):
            banned_words.append({"file": path, "line": lineno,
                                 "word": w, "text": body})


def _dsf_scan():
    """Walk the always-loaded corpus → (violations, banned, chars, ids, texts).

    Reads every file ONCE and hands the text back to the caller, because the
    content-preservation half needs AGENTS.md's body and re-reading it would
    make the two halves disagree the moment the file changes between them.
    An unreadable file is a DSF-read violation and never aborts the walk.
    """
    violations, banned_words = [], []
    total_chars = 0
    all_rule_ids = set()
    file_texts = {}
    for path in _steering_files():
        try:
            # `with`, not a bare `open(...).read()`: the corpus is re-linted on
            # every package edit and by the release gate, and the bare form
            # leaks a handle per file under any interpreter that does not
            # refcount it away — visible as a ResourceWarning storm the moment
            # anything drives this more than once in a process.
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            violations.append({"rule": "DSF-read", "file": path,
                               "line": 0, "text": str(exc)})
            continue
        file_texts[path] = text
        total_chars += len(text)
        for lineno, body in _iter_rule_lines(text):
            _dsf_scan_rule_line(path, lineno, body, violations, banned_words,
                                all_rule_ids)
    return violations, banned_words, total_chars, all_rule_ids, file_texts


def _content_preservation_errors(all_rule_ids, file_texts):
    """Gate-1 CP-1/CP-2/CP-3 → (content_errors, rule_count, baseline_count).

    Its inputs are the DSF scan's OUTPUTS, not the filesystem, which is what
    makes it a pure function of one corpus read. CP-2 reads AGENTS.md out of
    `file_texts`, so a corpus in which AGENTS.md is absent (or unreadable, and
    therefore never added) reports every required section missing rather than
    raising — the arm that matters, since CP-2 exists to notice exactly that.
    """
    content_errors = []

    # CP-1: Required rule IDs must be present somewhere in the corpus.
    missing_ids = [rid for rid in REQUIRED_RULE_IDS if rid not in all_rule_ids]
    for mid in missing_ids:
        content_errors.append({"rule": "CP-1", "text":
                               "required rule ID %s is missing from the corpus" % mid})

    # CP-2: Required sections must appear in AGENTS.md.
    agents_path = os.path.join(REPO_ROOT, "AGENTS.md")
    agents_text = file_texts.get(agents_path, "")
    agents_headings = set()
    for line in agents_text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            agents_headings.add(m.group(1))
    missing_sections = [s for s in REQUIRED_SECTIONS if s not in agents_headings]
    for ms in missing_sections:
        content_errors.append({"rule": "CP-2", "text":
                               "required section '%s' missing from AGENTS.md" % ms})

    # CP-3: Rule-count no-regression. The committed baseline is the count of
    # UNIQUE rule IDs in the corpus at the last known-good state. We store the
    # baseline in a file so it can be committed and updated deliberately.
    rule_count = len(all_rule_ids)
    baseline_count = _read_rule_count_baseline()
    if baseline_count is not None:
        if rule_count < baseline_count - RULE_COUNT_REGRESSION_MARGIN:
            content_errors.append({"rule": "CP-3", "text":
                "rule-count regression: %d < baseline %d (margin %d)"
                % (rule_count, baseline_count, RULE_COUNT_REGRESSION_MARGIN)})
    return content_errors, rule_count, baseline_count


def lint_steering(strict=False):
    """DSF lint + Gate-1 content preservation over the always-loaded corpus.

    The two halves are separately reportable and only JOINTLY decide `ok` under
    --strict: an unstrict run is green with content errors present, which is why
    `content_errors` and `content_ok` are in the payload either way. The release
    gate runs it strict; the R-PKG-04 post-edit check does not.
    """
    violations, banned_words, total_chars, all_rule_ids, file_texts = _dsf_scan()
    budget_ok = total_chars <= STEERING_CHAR_BUDGET

    # --- Content-preservation checks (Gate 1) --------------------------------
    content_errors, rule_count, baseline_count = _content_preservation_errors(
        all_rule_ids, file_texts)

    # --strict makes content-preservation errors hard failures.
    content_ok = not content_errors
    dsf_ok = (not violations) and (not banned_words) and budget_ok
    if strict:
        ok = dsf_ok and content_ok
    else:
        ok = dsf_ok
    return {"ok": ok, "total_chars": total_chars, "char_budget": STEERING_CHAR_BUDGET,
            "budget_ok": budget_ok, "violations": violations,
            "banned_words": banned_words,
            "content_errors": content_errors, "content_ok": content_ok,
            "rule_count": rule_count, "rule_count_baseline": baseline_count}


def _read_rule_count_baseline():
    """Read the committed rule-count baseline from .rule-count-baseline.

    Returns the integer baseline, or None if the file does not exist.
    """
    path = os.path.join(REPO_ROOT, ".rule-count-baseline")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            val = f.read().strip()
        return int(val)
    except (ValueError, OSError):
        return None


def _write_rule_count_baseline(count):
    """Write the rule-count baseline file."""
    path = os.path.join(REPO_ROOT, ".rule-count-baseline")
    with open(path, "w", encoding="utf-8") as f:
        f.write("%d\n" % count)


def cmd_steering_lint(args):
    strict = getattr(args, "strict", False)
    result = lint_steering(strict=strict)
    as_json = getattr(args, "format", "text") == "json"
    if as_json:
        json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("STEERING LINT %s" % ("PASSED" if result["ok"] else "FAILED"))
        print("  chars: %d / %d (budget %s)" % (
            result["total_chars"], result["char_budget"],
            "ok" if result["budget_ok"] else "EXCEEDED"))
        if result.get("rule_count") is not None:
            print("  rule-count: %d (baseline: %s)" % (
                result["rule_count"],
                str(result["rule_count_baseline"]) if result["rule_count_baseline"] is not None else "none"))
        for v in result["violations"]:
            print("  [%s] %s:%d  %s" % (v["rule"], v["file"], v["line"], v["text"]))
        for b in result["banned_words"]:
            print("  [DSF-5] %s:%d  banned %r in: %s" % (
                b["file"], b["line"], b["word"], b["text"]))
        for ce in result.get("content_errors", []):
            print("  [%s] %s" % (ce["rule"], ce["text"]))
    return 0 if result["ok"] else 1


#: Verb declaration — the ONE place this verb is registered. Discovered by
#: `agentware_lib.verbs`, whose docstring explains the dunder name and the
#: sparse order key (the key IS the top-level --help position).
__verbs__ = (
    ("steering", 2900, "register_steering"),
)


def register_steering(sub, fmt_parent):
    # steering ---------------------------------------------------------------
    p_steer = sub.add_parser("steering", help="Deterministic Steering Format tooling")
    steer_sub = p_steer.add_subparsers(dest="steering_command", metavar="<subcommand>")
    steer_sub.required = True
    p_lint = steer_sub.add_parser(
        "lint", parents=[fmt_parent], help="enforce DSF on always-loaded steering")
    p_lint.add_argument("--strict", action="store_true",
                        help="treat content-preservation errors (CP-1/2/3) as hard failures")
    p_lint.set_defaults(func=cmd_steering_lint)


_EXPORTS = [
    "DSF_VERBS", "DSF_BANNED_WORDS", "STEERING_CHAR_BUDGET", "STEERING_EXPLICIT",
    "STEERING_GLOB", "DSF_RULE_ID_RE", "DSF_BULLET_RE", "REQUIRED_RULE_IDS",
    "REQUIRED_SECTIONS", "RULE_COUNT_REGRESSION_MARGIN", "_steering_files",
    "_iter_rule_lines", "_first_token_verb", "_dsf_scan_rule_line",
    "_dsf_scan", "_content_preservation_errors", "lint_steering",
    "_read_rule_count_baseline", "_write_rule_count_baseline", "cmd_steering_lint",
]
__all__ = list(_EXPORTS)
