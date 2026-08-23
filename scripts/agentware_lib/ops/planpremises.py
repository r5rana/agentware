"""agentware_lib.ops.planpremises — the R-GND-01 premise-grounding gate (layer: ops).

Owns: the TRUST BOUNDARY of the premise surface — the block locator
(`_premise_region`), the CLOSED check grammar and its non-executing parser
(`_PREMISE_CHECK_KINDS` / `_CORPUS_METRICS` / `_CORPUS_OPS` / `_PATH_MODES`,
`_parse_premise_check`), the record parser (`plan_premises`), the load-bearing
predicates (`_plan_is_self_extension` / `_plan_names_live_kb_id` /
`_plan_is_load_bearing`), the run-time verifier (`verify_premises`) and the
`plan verify-premises` handler (`cmd_plan_verify_premises`). Also the R14
WARN->ERROR ramp threshold (`_PLAN_PREMISES_ERROR_SCHEMA`), which `ops.planlint`
imports for the STRUCTURAL half of the same rule.

Does NOT own: the EVALUATORS — what live state answers for a parsed record is
`ops.premisecheck`, which this module calls through `evaluate_premise` and which
never parses. Nor R14 itself: lint only checks that premise rows EXIST and PARSE
(it imports the parser + ramp constant from here); re-checking them against live
state is what this module drives. Nor the plan document's other blocks
(`## Depends-on` / `## Write set` / `> Checkpoint:` -> `ops.plangraph`), nor plan
authoring (`ops.planauthor`).

WHY THIS IS A GATE AND NOT ADVICE (R-GND-01)
--------------------------------------------
`R-FAIL-08` says to treat a point-in-time value in a learning — an id, a path, an
IP, a version — as suspect and verify it against live state. As prose that is a
reminder an agent can skip. This module promotes it to a MECHANICAL, deterministic
gate: a load-bearing plan declares machine-checkable premises, and the loop
pre-hook re-evaluates every one of them before phase 1, HALTING the run on a
reproduced falsification rather than executing a plan whose reasoning has already
expired.

THREE INVARIANTS, AND THE SECURITY ONE IS THE LOAD-BEARING ONE
--------------------------------------------------------------
INV-1 (deterministic / offline): no LLM, no network, no `now()` in any decision.
  The sole time comparison (`path mtime-before`) comes from an AUTHOR-SUPPLIED ISO
  stamp, never the clock, so the same plan against the same corpus always decides
  the same way.
INV-2 (read-only): every evaluator only reads. The corpus-metric folds run over
  THROWAWAY in-memory state so grounding a premise can never mutate real miner
  state.
SECURITY: the grammar is CLOSED with ZERO command-execution surface. Nothing a
  plan.md declares is ever shell-executed — which matters because auto-workorders
  are seeded from UNTRUSTED mined candidates, so a plan must never be able to name
  a command to run. Defense in depth: a `check:` bearing a shell metacharacter is
  rejected at PARSE and never even stored as a runnable record.

FAIL-OPEN vs FAIL-CLOSED, DELIBERATELY SPLIT
--------------------------------------------
An UNCHECKABLE premise (source log rotated away, corpus absent) is NOT a
falsification: it fails OPEN, so a plan grounded at authoring time can never DoS a
later run just because its evidence aged out. A REPRODUCED falsification always
exits 1 regardless of `--strict`. `--strict` adds only one thing: an unresolvable
CITATION on a self-extension plan becomes fatal, which is what the loop pre-hook
wants and what an interactive author does not.

Layer deps: ops.planpremises -> core.config (knowledge-dir), knowledge.index
(`load_index`, for the live-id scan) and a same-layer ops edge to
ops.premisecheck (`evaluate_premise`) — all DOWNWARD or same-layer.
`ops.planlint` imports THIS module, not the reverse, and `ops.premisecheck`
imports neither.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import json
import os
import re
import sys

from agentware_lib.core.config import resolve_knowledge_dir
from agentware_lib.knowledge.index import load_index
from agentware_lib.ops.premisecheck import evaluate_premise


_PLAN_PREMISES_SECTION_RE = re.compile(r"^##\s+Premises\s*$",
                                       re.IGNORECASE | re.MULTILINE)


_PLAN_PREMISES_FENCE_RE = re.compile(r"^\s*```+\s*premises\s*$", re.IGNORECASE)


_PLAN_PREMISE_ROW_RE = re.compile(r"^\s*-\s+premise:\s*(.+?)\s*$", re.IGNORECASE)


_PLAN_CITE_ROW_RE = re.compile(r"^\s*cite:\s*(.+?)\s*$", re.IGNORECASE)


_PLAN_CHECK_ROW_RE = re.compile(r"^\s*check:\s*(.+?)\s*$", re.IGNORECASE)


# Defense in depth: a command-injection metacharacter in a `check:` is a SECURITY
# reject at parse. The grammar is closed and nothing is ever shell-executed, but
# such a check is never even stored as a runnable record. The set is the
# command-CHAINING / SUBSTITUTION characters (`; & | ` $` + newlines) — NOT the
# numeric comparison operators `< >`, which corpus-metric legitimately uses
# (`>= <= > <`) and which are inert in a grammar that is never shell-executed.
_CHECK_METACHAR_RE = re.compile(r"[;&|`$\n\r]")


_PREMISE_CHECK_KINDS = ("cite-live", "config-eq", "path", "corpus-metric")


_CORPUS_METRICS = ("recurrence-keys-at-n", "claude-error-records")


_CORPUS_OPS = (">=", "<=", "==", "!=", ">", "<")


_PATH_MODES = ("exists", "absent", "mtime-before")


# R14 WARN->ERROR versioned ramp (mirrors R12). The lint rule stays WARN under the
# current KB schema (=1) — so the strict pre-hook cannot retroactively hard-fail an
# existing self-extension plan lacking a `## Premises` block — and gains its hard
# error only once a future migration advances the schema to this threshold. The
# real halt-on-falsification enforcement lives in the `verify-premises` pre-hook.
_PLAN_PREMISES_ERROR_SCHEMA = 2


def _premise_region(text):
    """(start, end) line-index span of the premises block, or None.

    Collects every candidate span — `## Premises` SECTIONs (heading .. next `##`,
    in file order) THEN ```premises FENCED blocks — and returns the FIRST span that
    actually CONTAINS a `- premise:` row. So a populated section still wins over a
    fence (section spans come first), but an EMPTY `## Premises` heading never
    silently suppresses a populated fenced block. If none are populated, the first
    section span is returned (so R14 still flags an empty premises block). Pure."""
    lines = (text or "").splitlines()
    candidates = []
    for i, ln in enumerate(lines):
        if _PLAN_PREMISES_SECTION_RE.match(ln):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^##\s+", lines[j]):
                    end = j
                    break
            candidates.append((i + 1, end))
    for i, ln in enumerate(lines):
        if _PLAN_PREMISES_FENCE_RE.match(ln):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if re.match(r"^\s*```+\s*$", lines[j]):
                    end = j
                    break
            candidates.append((i + 1, end))
    for (start, end) in candidates:
        if any(_PLAN_PREMISE_ROW_RE.match(lines[k]) for k in range(start, end)):
            return (start, end)
    return candidates[0] if candidates else None


def _parse_premise_check(check):
    """Parse a `check:` string into {kind, args, raw, error} WITHOUT executing.

    kind ∈ _PREMISE_CHECK_KINDS, or a sentinel: '__rejected__' (shell
    metacharacter — SECURITY), '__unknown__' (kind not in the closed grammar), or
    '__malformed__' (bad arity/value). NEVER runs anything."""
    raw = (check or "").strip()
    if not raw:
        return {"kind": "__malformed__", "args": [], "raw": raw,
                "error": "empty check"}
    if _CHECK_METACHAR_RE.search(raw):
        return {"kind": "__rejected__", "args": [], "raw": raw,
                "error": "shell metacharacter in check — rejected at parse, "
                         "never run"}
    toks = raw.split()
    kind, args = toks[0], toks[1:]
    if kind == "cite-live":
        return {"kind": "cite-live", "args": args, "raw": raw, "error": None}
    if kind == "config-eq":
        if len(args) != 2:
            return {"kind": "__malformed__", "args": args, "raw": raw,
                    "error": "config-eq requires <KEY> <value>"}
        return {"kind": "config-eq", "args": args, "raw": raw, "error": None}
    if kind == "path":
        if not args or args[0] not in _PATH_MODES:
            return {"kind": "__malformed__", "args": args, "raw": raw,
                    "error": "path requires <exists|absent|mtime-before> …"}
        if args[0] == "mtime-before":
            if len(args) != 3:
                return {"kind": "__malformed__", "args": args, "raw": raw,
                        "error": "path mtime-before requires <ISO> <rel>"}
        elif len(args) != 2:
            return {"kind": "__malformed__", "args": args, "raw": raw,
                    "error": "path %s requires <rel>" % args[0]}
        return {"kind": "path", "args": args, "raw": raw, "error": None}
    if kind == "corpus-metric":
        if (len(args) != 3 or args[0] not in _CORPUS_METRICS
                or args[1] not in _CORPUS_OPS):
            return {"kind": "__malformed__", "args": args, "raw": raw,
                    "error": "corpus-metric requires <metric> <op> <int> "
                             "(metric∈%s op∈%s)"
                             % (_CORPUS_METRICS, _CORPUS_OPS)}
        try:
            int(args[2])
        except (TypeError, ValueError):
            return {"kind": "__malformed__", "args": args, "raw": raw,
                    "error": "corpus-metric threshold must be an integer"}
        return {"kind": "corpus-metric", "args": args, "raw": raw, "error": None}
    return {"kind": "__unknown__", "args": args, "raw": raw,
            "error": "unknown check kind %r (closed grammar: %s)"
                     % (kind, ", ".join(_PREMISE_CHECK_KINDS))}


def _premise_flush(cur, records):
    """Close the pending record: parse its `check:` and append it.

    The parse happens HERE and at NO other site, which is the point of the
    helper rather than a side effect of it. `plan_premises` has two flush points
    (a new `- premise:` row, and end-of-block) and they were copy-pasted, so a
    record could have reached `records` un-parsed if either one drifted. Every
    downstream consumer — `verify_premises`, `evaluate_premise`, the R14 lint —
    reads `parsed` and would silently take a different arm if the key were
    absent, so "no record leaves this module un-parsed" is the security-relevant
    invariant of the trust boundary, and it is now one line in one place.
    """
    cur["parsed"] = _parse_premise_check(cur.get("check") or "")
    records.append(cur)


def _premise_absorb_row(cur, ln):
    """Absorb one `cite:` / `check:` continuation row into the pending record.

    FIRST-WINS on each field INDEPENDENTLY, and the fall-through matters: a
    second `cite:` row does not just fail to overwrite, it goes on to be tried
    as a `check:` row — which is why a line can only ever be absorbed once and
    why the two regexes are tried in that order. A `cite:` line can never match
    `_PLAN_CHECK_ROW_RE`, so in practice the fall-through is inert; it is kept
    because it is what the inline block did, and the ordering is pinned by test
    rather than left to be re-derived.

    Mutates `cur` in place and returns nothing: the caller owns the record.
    """
    mc = _PLAN_CITE_ROW_RE.match(ln)
    if mc and cur.get("cite") is None:
        cur["cite"] = mc.group(1).strip()
        return
    mk = _PLAN_CHECK_ROW_RE.match(ln)
    if mk and cur.get("check") is None:
        cur["check"] = mk.group(1).strip()


def plan_premises(text):
    """Parse the `## Premises` section (or ```premises fenced block) into records.

    Each record: {premise, cite, check, parsed, line}. Returns [] when no premises
    block is present (trivial plans stay untouched). `parsed` is the closed-grammar
    parse of `check:` (a metacharacter-bearing check parses to '__rejected__' and
    is NEVER runnable). Pure, offline, read-only — the parser twin of
    plan_target_dirs().

    A continuation row before the first `- premise:` row is DISCARDED (`cur is
    None`), so an orphaned `cite:` above the block can never attach itself to
    the first premise below it.
    """
    span = _premise_region(text)
    if span is None:
        return []
    lines = (text or "").splitlines()
    start, end = span
    records, cur = [], None
    for i in range(start, end):
        ln = lines[i]
        m = _PLAN_PREMISE_ROW_RE.match(ln)
        if m:
            if cur is not None:
                _premise_flush(cur, records)
            cur = {"premise": m.group(1).strip(), "cite": None,
                   "check": None, "line": i + 1}
            continue
        if cur is None:
            continue
        _premise_absorb_row(cur, ln)
    if cur is not None:
        _premise_flush(cur, records)
    return records


def _plan_is_self_extension(text):
    """Deterministic: does the plan declare itself a package/self-extension change?
    Matches the `> Type: … self-extension …` header or the Self-extension warning
    heading seeded by `plan new --self-extension`."""
    t = text or ""
    if re.search(r"(?im)^>\s*Type:.*self-extension", t):
        return True
    if re.search(r"(?im)^##\s+.*Self-extension warning", t):
        return True
    return False


def _plan_names_live_kb_id(text, kdir):
    """D6 (second trigger): does the plan reference a live KB id? Deterministic;
    reads the index once. Ids are long/distinctive (>=8 chars) so a substring
    match is a reliable, cheap signal."""
    if not kdir:
        return False
    # INV-2: read-only — never rebuild the index as a side effect of a lint.
    try:
        data, err = load_index(kdir, _allow_rebuild=False)
    except Exception:
        return False
    if err is not None or not isinstance(data, dict):
        return False
    t = text or ""
    for e in data.get("entries") or []:
        if isinstance(e, dict):
            eid = e.get("id")
            if eid and len(eid) >= 8 and eid in t:
                return True
    return False


def _plan_is_load_bearing(text, kdir=None):
    """D6: a plan is load-bearing iff it is self-extension OR names a live KB id."""
    if _plan_is_self_extension(text):
        return True
    return _plan_names_live_kb_id(text, kdir)


def verify_premises(text, kdir, strict=False):
    """Core of `plan verify-premises` (pure/read-only). Returns a summary dict.

    Exit policy (mirrored by cmd_plan_verify_premises' return code):
      - a REPRODUCED falsification => not ok (hard stop, strict-independent);
      - D5 fail-CLOSED: an UNRESOLVABLE citation on a SELF-EXTENSION plan => not ok
        ONLY under --strict;
      - every other uncheckable (missing verb/corpus/path, unknown/malformed kind)
        => WARN, still ok (D5 fail-OPEN — a drifted check can never DoS the loop)."""
    records = plan_premises(text)
    self_ext = _plan_is_self_extension(text)
    results, falsified, fail_closed = [], False, False
    for rec in records:
        verd = evaluate_premise(rec, kdir)
        v = verd.get("verdict")
        item = {"premise": rec.get("premise"), "cite": rec.get("cite"),
                "check": rec.get("check"), "line": rec.get("line"),
                "verdict": v, "detail": verd.get("detail")}
        for k in ("actual", "expected", "op", "rejected"):
            if k in verd:
                item[k] = verd[k]
        if v == "falsified":
            falsified = True
        if v == "uncheckable" and verd.get("unresolved_cite") and self_ext \
                and strict:
            fail_closed = True
            item["fail_closed"] = True
        results.append(item)
    ok = not (falsified or fail_closed)
    return {"ok": ok, "self_extension": self_ext,
            "load_bearing": _plan_is_load_bearing(text, kdir),
            "premises": results, "falsified": falsified,
            "fail_closed_unresolved_cite": fail_closed, "strict": bool(strict)}


def cmd_plan_verify_premises(args):
    """Re-verify a plan's declared premises against LIVE state (read-only). Exit 1
    on a reproduced falsification, or (under --strict) an unresolvable citation on
    a self-extension plan; an uncheckable premise WARNs and exits 0 (D5). NEVER a
    KB writer (absent from _kb_writer_funcs by omission)."""
    path = args.path
    strict = bool(getattr(args, "strict", False))
    as_json = getattr(args, "format", "text") == "json"
    if not os.path.isfile(path):
        sys.stderr.write("agentware: plan not found: %s\n" % path)
        return 1
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        sys.stderr.write("agentware: cannot read plan %s: %s\n" % (path, exc))
        return 1
    kdir = resolve_knowledge_dir()
    summary = verify_premises(text, kdir, strict=strict)
    summary["plan"] = path
    if as_json:
        json.dump(summary, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("PREMISE VERIFY %s" % ("PASSED" if summary["ok"] else "FAILED"))
        for r in summary["premises"]:
            glyph = {"grounded": "✔", "falsified": "✖",
                     "uncheckable": "⚠"}.get(r["verdict"], "?")
            print("  %s [%s] %s" % (glyph, r["verdict"], r["premise"]))
            print("        cite:  %s" % r["cite"])
            print("        check: %s" % r["check"])
            print("        -> %s" % r["detail"])
        if summary["falsified"]:
            print("REPRODUCED FALSIFICATION — a load-bearing premise does not "
                  "hold against live state.")
        if summary["fail_closed_unresolved_cite"]:
            print("UNRESOLVABLE CITATION on a self-extension plan (fail-closed "
                  "under --strict).")
    return 0 if summary["ok"] else 1


# Explicit facade export list (public AND underscore-private) — the entrypoint
# binds these onto its own namespace via the re-export loop so external consumers
# (tests/_fixtures.load_cli, the dashboard sidecar) keep reaching them as CLI.<name>.
# `evaluate_premise` is re-exported from ops.premisecheck rather than redefined:
# the monolith bound ONE name and every oracle resolves it as CLI.evaluate_premise,
# so the split must not turn one symbol into two.
_EXPORTS = [
    "_PLAN_PREMISES_SECTION_RE",
    "_PLAN_PREMISES_FENCE_RE",
    "_PLAN_PREMISE_ROW_RE",
    "_PLAN_CITE_ROW_RE",
    "_PLAN_CHECK_ROW_RE",
    "_CHECK_METACHAR_RE",
    "_PREMISE_CHECK_KINDS",
    "_CORPUS_METRICS",
    "_CORPUS_OPS",
    "_PATH_MODES",
    "_PLAN_PREMISES_ERROR_SCHEMA",
    "_premise_region",
    "_parse_premise_check",
    "plan_premises",
    "_plan_is_self_extension",
    "_plan_names_live_kb_id",
    "_plan_is_load_bearing",
    "verify_premises",
    "cmd_plan_verify_premises",
]
__all__ = list(_EXPORTS)
