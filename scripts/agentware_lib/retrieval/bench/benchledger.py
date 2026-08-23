"""agentware_lib.retrieval.bench.benchledger -- the ledger-touching `bench`
handlers: the append-only-safe gold_path redactor + `bench redact` / `bench lint`
(layer: retrieval).

Owns: redact_ledger_gold_paths -- the ONLY sanctioned mutation of the append-only
benchmark ledger (it may touch ONLY the `gold_path` field) -- plus the two
handlers built on the ledger/gold surface: cmd_bench_redact (PII scrub + derived
scorecard regeneration) and cmd_bench_lint (gold-set staleness + coverage ratio)
with its two halves -- the single-pass coverage fold _gold_coverage and the
human report _print_bench_lint_text.

Does NOT own: the SWE-task domain logic + the `bench swe` runner
(-> agentware_lib.retrieval.bench.benchswe); the `bench scorecard` handler + the
`bench` parser registrar (-> agentware_lib.retrieval.bench.benchcmd); the
append-only ledger itself (-> agentware_lib.retrieval.ledger) or the scorecard
render/write VIEW (-> agentware_lib.retrieval.scorecard) -- both are siblings held
via the `_ledger` / `_scorecard` module aliases and referenced by ATTRIBUTE so the
bytecode global-resolve gate never walks a foreign sibling function held in this
module's __dict__. Nor the knowledge index -- cmd_bench_lint imports it LAZILY
(`from agentware_lib.knowledge import index`) because retrieval->knowledge must
stay function-local (layer discipline).

Layer deps: retrieval -> core (config; allowed at module level) + the sibling
retrieval.ledger/retrieval.scorecard (by module alias, attribute reads) +
knowledge.index LAZILY (function-local, cmd_bench_lint only). No ops/features/cli
import. _EXPORTS lists the redactor + the 2 handlers for the entrypoint by-name
facade.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""
import json
import os
import sys

import agentware_lib.core.config as config
import agentware_lib.core.frontmatter as frontmatter
from agentware_lib.retrieval import ledger as _ledger
from agentware_lib.retrieval import scorecard as _scorecard


def redact_ledger_gold_paths(ledger_path):
    """The ONLY sanctioned mutation of the append-only ledger (`:3556` exception):
    rewrite ONLY the `gold_path` field of each row to its RELATIVE, PII-free form
    (`redact_gold_path`), preserving every other field value AND row order
    byte-for-byte.

    Redacting personal data is the documented EXCEPTION to append-only — metric
    values, reliability, fingerprints and ordering are NEVER touched. A row whose
    `gold_path` does not change keeps its ORIGINAL bytes verbatim; only a row that
    actually leaks is re-serialized (sorted keys, matching `append_ledger_row`).
    Un-parseable / blank lines are passed through untouched. Returns
    (total_rows, changed_rows). Resolves operator identity via
    `redact_gold_path` → `operator_identity_strings` (no hardcoded HOME).
    """
    if not os.path.isfile(ledger_path):
        return (0, 0)
    with open(ledger_path, encoding="utf-8") as f:
        lines = f.readlines()
    out = []
    total = 0
    changed = 0
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            out.append(raw)
            continue
        try:
            row = json.loads(stripped)
        except ValueError:
            out.append(raw)  # preserve un-parseable lines verbatim
            continue
        total += 1
        if isinstance(row, dict) and "gold_path" in row:
            new_gp = _ledger.redact_gold_path(row.get("gold_path"))
            if new_gp != row.get("gold_path"):
                row["gold_path"] = new_gp
                out.append(
                    json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                changed += 1
                continue
        out.append(raw)  # unchanged row → keep original bytes (append-only intact)
    with open(ledger_path, "w", encoding="utf-8") as f:
        f.writelines(out)
    return (total, changed)


def cmd_bench_redact(args):
    """`bench redact` scrubs operator personal data (R-LOC-03) from the committed
    benchmark ledger: it rewrites ONLY the leaking `gold_path` field to its
    relative form, preserving every metric byte-for-byte and row order, then
    regenerates the derived SCORECARD.md beside the ledger.

    Defaults to the live knowledge dir's `benchmarks/history.jsonl`; pass an
    explicit ledger PATH (e.g. the repo's committed `benchmarks/history.jsonl`)
    to scrub that file instead. This is the ONLY sanctioned mutation of the
    append-only ledger and it may touch ONLY `gold_path`.
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    ledger = getattr(args, "path", None) or os.path.join(kdir, _ledger.HISTORY_REL)
    if not os.path.isfile(ledger):
        print("bench redact: no ledger at %s (nothing to scrub)" % ledger)
        return 0
    total, changed = redact_ledger_gold_paths(ledger)
    # Regenerate the derived scorecard VIEW beside the scrubbed ledger.
    scorecard = os.path.join(
        os.path.dirname(ledger) or ".", os.path.basename(_scorecard.SCORECARD_REL))
    rows = _ledger._read_ledger(ledger)
    parent = os.path.dirname(scorecard)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(scorecard, "w", encoding="utf-8") as f:
        f.write(_scorecard.render_scorecard(rows))
    print("bench redact: scrubbed %d/%d row gold_path(s) in %s; "
          "regenerated %s" % (changed, total, ledger, scorecard))
    return 0


def _gold_coverage(gold, entries):
    """Fold the gold set against the index into ONE coverage reading.

    The three loops `cmd_bench_lint` used to run inline are one obligation --
    answering "what does this gold set reach, what does it miss, and what does
    it name that is not there" -- and they walked the same two collections
    three times. Split out at task 99 wave 10.

    Returns a dict with `covered` / `uncovered` / `dangling` id sets and the
    `clusters` map (tag -> uncovered ids). Pure; read-only; no I/O.

    `covered` is every id the gold set REFERENCES, which is NOT the same as
    every id the gold set reaches: a dangling id is in BOTH `covered` and
    `dangling`. That is deliberate and load-bearing, not an oversight to tidy
    up in a refactor -- `covered` exists to compute `uncovered = entry_ids -
    covered`, a set difference over index ids that a dangling id cannot affect,
    and the report prints its size as "covered: N entries". Narrowing it to
    `covered & entry_ids` would leave `uncovered` identical and silently change
    that ONE printed number on exactly the runs that also print a DANGLING
    line. This is the shape a same-looking body changes and no metric moves.

    `clusters` reads tags through `frontmatter._entry_list_field`, which keeps
    STRING tags only -- `setdefault(<dict>, ...)` on a poisoned entry is an
    unhashable-type crash, and this reader is the guard against it.
    """
    entry_ids = {e["id"] for e in entries}
    covered, dangling = set(), set()
    for q in gold:
        for eid in q.get("expected_ids", []):
            covered.add(eid)
            if eid not in entry_ids:
                dangling.add(eid)
    uncovered = entry_ids - covered
    clusters = {}
    for e in entries:
        if e["id"] in uncovered:
            for t in frontmatter._entry_list_field(e, "tags")[:2]:
                clusters.setdefault(t, []).append(e["id"])
    return {"covered": covered, "uncovered": uncovered,
            "dangling": dangling, "clusters": clusters}


def _print_bench_lint_text(cov, n_queries, n_entries, ratio, threshold, ok):
    """The human `bench lint` report. Split out at task 99 wave 10.

    Two properties live here rather than in the caller and are worth naming,
    because both are invisible in a report that merely looks right:

      * the VERDICT IS TWO-PART. A run can fail for a low ratio, for a dangling
        expected_id, or for both, and each prints its OWN failure line -- so a
        healthy ratio with a dangling id still fails, and says why.
      * the CLUSTER BLOCK IS BOUNDED. It prints only for 1..30 uncovered
        entries. Above that the list is a wall rather than a work order, and
        the bound is the reason the report stays readable on a large KB.

    Prints; returns nothing. `ok` is passed in rather than recomputed so the
    text and JSON arms can never disagree about the verdict.
    """
    print("bench lint: %d queries / %d entries = %.2f coverage (threshold: %.2f)"
          % (n_queries, n_entries, ratio, threshold))
    if cov["dangling"]:
        print("  DANGLING expected_ids (not in index): %s"
              % sorted(cov["dangling"]))
    print("  covered: %d entries | uncovered: %d entries"
          % (len(cov["covered"]), len(cov["uncovered"])))
    if cov["uncovered"] and len(cov["uncovered"]) <= 30:
        top_clusters = sorted(cov["clusters"].items(),
                              key=lambda x: -len(x[1]))[:5]
        if top_clusters:
            print("  top uncovered clusters (by tag):")
            for tag, ids in top_clusters:
                print("    %s (%d entries): %s" % (
                    tag, len(ids), ", ".join(sorted(ids)[:3]) +
                    ("..." if len(ids) > 3 else "")))
    if ok:
        print("BENCH LINT PASSED")
    else:
        if ratio < threshold:
            print("BENCH LINT FAILED: coverage %.2f < threshold %.2f"
                  % (ratio, threshold))
        if cov["dangling"]:
            print("BENCH LINT FAILED: %d dangling expected_ids reference "
                  "entries not in index" % len(cov["dangling"]))


def cmd_bench_lint(args):
    """`bench lint` -- gold-set staleness and coverage-ratio check.

    Checks:
    1. Gold query coverage ratio (queries / entries) >= threshold (default 0.25)
    2. Lists entry clusters with no gold query coverage
    3. Reports difficulty-tier breakdown if tiers are present in the gold JSON

    EXIT 0 if healthy, EXIT 1 if coverage ratio below threshold.
    """
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    from agentware_lib.knowledge import index  # lazy: retrieval -> knowledge (layer discipline)
    idx, ierr = index.load_index(kdir)
    if ierr is not None:
        sys.stderr.write("agentware: %s\n" % ierr)
        return 1

    gold_path = os.path.join(kdir, "benchmarks", "recall-gold.json")
    if not os.path.isfile(gold_path):
        print("bench lint: no gold set at %s — skipping." % gold_path)
        return 0

    with open(gold_path, "r", encoding="utf-8") as f:
        gold = json.load(f)

    entries = idx.get("entries") or []
    n_entries = len(entries)
    n_queries = len(gold)
    threshold = getattr(args, "min_coverage", 0.25)
    ratio = n_queries / n_entries if n_entries > 0 else 0.0
    cov = _gold_coverage(gold, entries)

    # The verdict is TWO-part: a healthy ratio with a dangling expected_id is
    # still a failure, and the `and not dangling` is the whole reason.
    ok = ratio >= threshold and not cov["dangling"]

    if getattr(args, "format", "text") == "json":
        print(json.dumps({
            "entries": n_entries,
            "gold_queries": n_queries,
            "coverage_ratio": round(ratio, 4),
            "threshold": threshold,
            "covered_entries": len(cov["covered"]),
            "uncovered_entries": len(cov["uncovered"]),
            "dangling_expected_ids": sorted(cov["dangling"]),
            "pass": ok,
        }, indent=2))
    else:
        _print_bench_lint_text(cov, n_queries, n_entries, ratio, threshold, ok)
    return 0 if ok else 1


_EXPORTS = [
    "redact_ledger_gold_paths",
    "cmd_bench_redact",
    "_gold_coverage",
    "_print_bench_lint_text",
    "cmd_bench_lint",
]
