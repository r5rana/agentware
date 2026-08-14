"""agentware_lib.retrieval.ledger - the append-only benchmark ledger + ACR default-flip ledger (layer: retrieval).

Owns: the immutable, commit+date-stamped benchmark ledger written to
<kdir>/benchmarks/history.jsonl. The ledger CONSTANTS (HISTORY_REL, LEDGER_SCHEMA,
ACR_GATE_MODE); the read-only HEAD snapshot (git_info); the volatile-state gatherers
that feed a row (the unittest pass-rate probes _parse_failing_test_ids /
_record_test_passrate_detail / _record_test_passrate and the INV-1 determinism twins
_determinism_check / _determinism_check_at); the documented reliability formula
(compute_reliability); the R-LOC-03 gold-path redactor (redact_gold_path); the pure row
BUILDERS (build_ledger_row for own-gold/eval + suite rows, build_acr_gate_row for the
mode="acr-gate" row); the append-only writer (append_ledger_row) and reader (_read_ledger);
the row RECORDERS that gather volatile state and append (record_ledger, record_ledger_suite,
record_acr_gate); and the data-driven default-strategy resolvers that read the ledger
(active_default_strategy -> bm25/bm25+acr from the latest passing acr-gate row;
active_recall_strategy -> honors the onboarding A/B retrieval_mode).

Does NOT own: the ranking/scoring engine it reuses (evaluate -> agentware_lib.retrieval.metrics_rank
via _metrics_rank); the retrieval-mode resolver (resolve_effective_retrieval_mode ->
agentware_lib.retrieval.vectorcache via _vectorcache); the repo-root anchor (REPO_ROOT ->
agentware_lib.core.paths via paths); the git EXEC primitive (_git_capture ->
agentware_lib.core.gitio via gitio); index validation (index.validate_index) + operator-identity
redaction inputs (privacy.operator_identity_strings), both in agentware_lib.knowledge and
imported LAZILY (retrieval -> knowledge stays function-local); the ACR win gate
(agentware_lib.retrieval.acrgate); the ablation instrument (evaluate_ablation, now
agentware_lib.retrieval.evalcmd); the regression + clean-HEAD A/B gates, the scorecard and the
SWE bench (agentware_lib.retrieval.{regressiongate,scorecard,bench}); the recall payload
orchestrator (build_recall_payload, now agentware_lib.retrieval.recallcmd - it reaches
active_recall_strategy via the _ledger alias) and the eval/recall CLI handlers (cmd_eval /
cmd_recall, now evalcmd/recallcmd, wave-3 seam 16b) - they call the ledger builders/recorders/
resolvers via the _ledger module alias by attribute.
Layer deps: retrieval -> core (agentware_lib.core.{paths,config,dates,gitio}) + retrieval ->
retrieval (agentware_lib.retrieval.{metrics_rank as _metrics_rank, vectorcache as _vectorcache},
so the global-resolve gate never walks a foreign sibling function held in this module's __dict__).
retrieval -> knowledge (index / privacy) is deliberately LAZY (function-local imports inside
redact_gold_path / the three recorders), never a module-level import. Imports nothing in ops/features;
nothing in core/ or knowledge/ imports from here.
Fan-in: 11 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-1: active_default_strategy / active_recall_strategy are pure reads of the immutable ledger,
so bare recall is byte-identical at a fixed ledger + as_of; the row builders are deterministic
given their injected volatile inputs (run_ts, latency, git). INV-2: read-only w.r.t. the KB -
append_ledger_row only ever opens the ledger in append mode, never rewrites a row.
"""

import json
import os
import re
import subprocess
import sys

from agentware_lib.core import config
from agentware_lib.core import dates
from agentware_lib.core import gitio
from agentware_lib.core import paths
from agentware_lib.retrieval import metrics_rank as _metrics_rank
from agentware_lib.retrieval import vectorcache as _vectorcache


HISTORY_REL = os.path.join("benchmarks", "history.jsonl")

LEDGER_SCHEMA = "agentware-bench/v1"

def git_info():
    """Read-only snapshot of HEAD: short SHA, subject, UTC commit date, dirtiness.

    Degrades gracefully (commit='unknown', empty strings) outside a git repo so
    `--record` never fails just because git is missing. Never mutates anything.
    """
    utc_env = dict(os.environ)
    utc_env["TZ"] = "UTC"
    commit = gitio._git_capture(["rev-parse", "--short", "HEAD"]) or "unknown"
    subject = gitio._git_capture(["log", "-1", "--pretty=%s"])
    committed = gitio._git_capture(
        ["log", "-1", "--date=iso-strict-local", "--pretty=%cd"], env=utc_env)
    dirty = bool(gitio._git_capture(["status", "--porcelain"]))
    return {"commit": commit, "subject": subject,
            "committed": committed, "dirty": dirty}

def _parse_failing_test_ids(err):
    """Extract failing/erroring test ids from a unittest stderr blob.

    unittest prints one `FAIL: <test_id>` or `ERROR: <test_id>` header line per
    failing test (above each traceback) — e.g.
    `FAIL: test_x (tests.test_mod.TestCase)` or, on newer Pythons,
    `FAIL: test_x (tests.test_mod.TestCase.test_x)`. The count-only parse in
    `_record_test_passrate` throws these away; this captures the ids (deduped,
    first-seen order preserved) so callers can answer "which tests failed?".
    Pure string parsing, read-only, deterministic.
    """
    ids = []
    seen = set()
    for ln in (err or "").splitlines():
        s = ln.strip()
        for prefix in ("FAIL: ", "ERROR: "):
            if s.startswith(prefix):
                tid = s[len(prefix):].strip()
                if tid and tid not in seen:
                    seen.add(tid)
                    ids.append(tid)
                break
    return ids

def _record_test_passrate_detail():
    """Shell the unittest suite once; return (pass_rate, ran, failed, failed_tests).

    Superset of `_record_test_passrate`: in addition to the counts it returns the
    list of failing test ids parsed from the `FAIL:`/`ERROR:` stderr lines (which
    the count-only path discards). Recursion guard: the unittest suite itself
    exercises `eval --record`; if we are already inside a suite run
    (AGENTWARE_NESTED_UNITTEST=1) we return (None, 0, 0, []) instead of spawning
    the suite again. pass_rate is None when not measured. Pure stdlib subprocess
    (INV-6). Read-only.
    """
    if os.environ.get("AGENTWARE_NESTED_UNITTEST") == "1":
        return None, 0, 0, []
    env = dict(os.environ)
    env["AGENTWARE_NESTED_UNITTEST"] = "1"
    tests_dir = os.path.join(paths.REPO_ROOT, "tests")
    if not os.path.isdir(tests_dir):
        return None, 0, 0, []
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=paths.REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env)
    except OSError:
        return None, 0, 0, []
    err = proc.stderr or ""
    rm = re.search(r"Ran (\d+) test", err)
    ran = int(rm.group(1)) if rm else 0
    fm = re.search(r"failures=(\d+)", err)
    em = re.search(r"errors=(\d+)", err)
    failed = (int(fm.group(1)) if fm else 0) + (int(em.group(1)) if em else 0)
    if ran == 0:
        return None, 0, 0, []
    return (ran - failed) / ran, ran, failed, _parse_failing_test_ids(err)

def _record_test_passrate():
    """Shell the unittest suite once and return (pass_rate, ran, failed).

    Back-compatible 3-tuple wrapper over `_record_test_passrate_detail()` (which
    also returns the failing test ids) so existing call sites keep working.
    pass_rate is None when not measured. Pure stdlib subprocess (INV-6).
    Read-only.
    """
    pass_rate, ran, failed, _ = _record_test_passrate_detail()
    return pass_rate, ran, failed

def _determinism_check(kdir, data, gold, top_k):
    """Score the bm25 path twice over the gold set; True iff ranking is identical.

    Operationalizes INV-1 as a recorded boolean: identical inputs -> identical
    ordering. Latency is excluded (the only non-deterministic field). Read-only.
    """
    a = _metrics_rank.evaluate(kdir, data, gold, "bm25", top_k)["per_query"]
    b = _metrics_rank.evaluate(kdir, data, gold, "bm25", top_k)["per_query"]
    return [q["ranked"] for q in a] == [q["ranked"] for q in b]

def _determinism_check_at(kdir, data, gold, top_k, strategy, as_of):
    """Twin-run ranking-equality check for `strategy` at a PINNED `as_of` (INV-1).

    Mirrors `_determinism_check` (which fixes strategy=bm25) but parameterized by
    strategy + as_of so the ACR gate can assert `bm25+acr` is byte-identical
    across runs at a fixed date (decay is a pure function of the two dates, no
    clock). Latency excluded (the only non-deterministic field). Read-only.
    """
    a = _metrics_rank.evaluate(kdir, data, gold, strategy, top_k, as_of=as_of)["per_query"]
    b = _metrics_rank.evaluate(kdir, data, gold, strategy, top_k, as_of=as_of)["per_query"]
    return [q["ranked"] for q in a] == [q["ranked"] for q in b]

def compute_reliability(recall_at_k, test_pass_rate, index_ok, determinism_ok):
    """Composite 0-100 reliability score (documented formula).

    Weighted blend (weights sum to 1.0): Recall@k 0.40, unittest pass-rate 0.30,
    index validate clean 0.15, determinism check 0.15. When test_pass_rate is
    None (not measured, e.g. nested run) its component is dropped and the
    remaining weights are renormalized so the score stays in [0, 100].
    """
    comps = [(0.40, max(0.0, min(1.0, recall_at_k)))]
    if test_pass_rate is not None:
        comps.append((0.30, max(0.0, min(1.0, test_pass_rate))))
    comps.append((0.15, 1.0 if index_ok else 0.0))
    comps.append((0.15, 1.0 if determinism_ok else 0.0))
    total_w = sum(w for w, _ in comps)
    if total_w <= 0:
        return 0.0
    return round(100.0 * sum(w * v for w, v in comps) / total_w, 2)

def redact_gold_path(gold_path):
    """Reduce an eval gold-set path to a RELATIVE, PII-free form for the ledger.

    The committed `benchmarks/history.jsonl` is public + reproducible, so a row
    must NEVER carry an absolute operator HOME path (R-LOC-03). This is the source
    fix: relativize the gold path against the knowledge dir (its natural root →
    e.g. "benchmarks/recall-gold.json"), falling back to a HOME-relative form,
    then to the bare basename. A final guard drops to the basename if any residual
    operator-identity token survives as a path component. Idempotent: an
    already-relative path is returned unchanged. Applied in the row BUILDERS (the
    single persistence choke-point every recorded row of every mode passes
    through) so the no-leak guarantee is structural, not per-call-site.
    """
    if not gold_path or not os.path.isabs(gold_path):
        return gold_path
    kdir = config.resolve_knowledge_dir()
    bases = []
    if kdir:
        bases.append(os.path.abspath(kdir))
    home = os.path.expanduser("~")
    if home and home not in ("", "/", "~"):
        bases.append(home)
    rel = None
    for base in bases:
        try:
            cand = os.path.relpath(gold_path, base)
        except ValueError:
            continue
        if cand != os.pardir and not cand.startswith(os.pardir + os.sep):
            rel = cand
            break
    if rel is None:
        rel = os.path.basename(gold_path)
    from agentware_lib.knowledge import privacy
    for needle in privacy.operator_identity_strings(kdir):
        if needle and needle in rel:
            rel = os.path.basename(gold_path)
            break
    return rel

def build_ledger_row(result, git_meta, run_ts, checks):
    """Assemble one immutable ledger row from an eval/ablate result. Pure.

    Deterministic given its inputs (the only volatile values — run_ts, latency,
    git state — are injected by the caller), so tests can build + serialize a row
    byte-identically. `checks` carries test_pass_rate / index_validate_ok /
    determinism_ok / tests_ran / tests_failed.
    """
    mode = result.get("mode", "eval")
    if mode == "ablate":
        treatment = result["treatment"]
        metrics = result["strategies"][treatment]
        strategy = treatment
        ablation = {
            "baseline": result["baseline"],
            "treatment": treatment,
            "delta": result["delta"],
        }
    else:
        metrics = result["metrics"]
        strategy = result.get("strategy", "bm25")
        ablation = None
    recall = metrics.get("recall_at_k", 0.0)
    reliability = compute_reliability(
        recall, checks.get("test_pass_rate"),
        checks.get("index_validate_ok", False),
        checks.get("determinism_ok", False))
    return {
        "schema": LEDGER_SCHEMA,
        "run": run_ts,
        "commit": git_meta.get("commit", "unknown"),
        "committed": git_meta.get("committed", ""),
        "subject": git_meta.get("subject", ""),
        "dirty": git_meta.get("dirty", False),
        "mode": mode,
        # `suite` isolates benchmark families in the SAME ledger: None for the
        # own gold set (the historical default), "longmemeval" for the public
        # comparable scorer. The gate only ever compares rows of the same suite
        # (see gate_baseline) so a session-level LongMemEval row never pollutes
        # the gold-set baseline and vice-versa.
        "suite": result.get("suite"),
        "strategy": strategy,
        "top_k": result.get("top_k"),
        "num_queries": result.get("num_queries"),
        # RELATIVE, PII-free gold path (R-LOC-03): the committed ledger is public,
        # so never persist an absolute operator HOME path. See redact_gold_path.
        "gold_path": redact_gold_path(result.get("gold_path")),
        # Deterministic whole-corpus fingerprint of the scored corpus. Additive
        # field (append-only preserved); makes gate comparability corpus-aware so
        # own-gold drift no longer false-FAILs. Legacy rows lack it (treated as a
        # distinct bucket by gate_baseline).
        "corpus_fingerprint": result.get("corpus_fingerprint"),
        # Scored-corpus entry count (additive, append-only, same pattern as
        # corpus_fingerprint). Powers /api/scaling's Recall@k-vs-corpus-size plot;
        # legacy rows lacking it report N unknown.
        "corpus_size": result.get("corpus_size"),
        "metrics": metrics,
        "ablation": ablation,
        "checks": checks,
        "reliability": reliability,
    }

def append_ledger_row(path, row):
    """Append ONE JSON line to the ledger. APPEND-ONLY — never rewrites a row.

    Opens strictly in append mode ('a'), so existing rows are byte-immutable;
    re-recording at the same commit adds a NEW row (distinct run timestamp). Keys
    are sorted so a fixed row serializes byte-identically. Returns the line.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    line = json.dumps(row, ensure_ascii=False, sort_keys=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line

def _read_ledger(path):
    """Read all rows from the append-only ledger; tolerate blanks/bad lines.

    Read-only (INV-2): only ever opens the file for reading. Returns [] when the
    file does not exist (first-ever run).
    """
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows

def record_ledger(kdir, data, gold, gold_path, result, top_k):
    """Gather volatile state (git, UTC ts, test pass-rate, validate, determinism),
    build a row, and append it. Returns the row. Read-only w.r.t. the KB."""
    pass_rate, ran, failed = _record_test_passrate()
    from agentware_lib.knowledge import index
    checks = {
        "test_pass_rate": pass_rate,
        "tests_ran": ran,
        "tests_failed": failed,
        "index_validate_ok": not index.validate_index(kdir, data),
        "determinism_ok": _determinism_check(kdir, data, gold, top_k),
    }
    row = build_ledger_row(result, git_info(), dates.utc_now_iso(), checks)
    append_ledger_row(os.path.join(kdir, HISTORY_REL), row)
    return row

def record_ledger_suite(kdir, data, result):
    """Append one immutable ledger row for a benchmark-suite run (e.g. LongMemEval).

    Mirrors record_ledger but uses the suite's OWN per-run determinism result
    (already computed inside the scorer over the real haystacks) instead of the
    own-gold-set determinism check. Read-only w.r.t. the KB; append-only.
    """
    pass_rate, ran, failed = _record_test_passrate()
    from agentware_lib.knowledge import index
    checks = {
        "test_pass_rate": pass_rate,
        "tests_ran": ran,
        "tests_failed": failed,
        "index_validate_ok": not index.validate_index(kdir, data),
        "determinism_ok": bool(result.get("determinism_ok")),
    }
    row = build_ledger_row(result, git_info(), dates.utc_now_iso(), checks)
    append_ledger_row(os.path.join(kdir, HISTORY_REL), row)
    return row

ACR_GATE_MODE = "acr-gate"

def build_acr_gate_row(result, decision, git_meta, run_ts, checks):
    """Assemble one immutable `mode="acr-gate"` ledger row. Pure given inputs.

    `strategy` is the DECIDED default (treatment iff the gate passed, else the
    baseline), so `active_default_strategy` can resolve the agent default from a
    single field. The full `decision` is stored verbatim as evidence (deltas,
    t-stat, win-rate, no-harm, per-stratum). Volatile values (run_ts, latency,
    git) are injected by the caller so a fixed decision serializes identically.
    """
    baseline = result["baseline"]
    treatment = result["treatment"]
    metrics = result["strategies"][treatment]
    decided = treatment if decision.get("passed") else baseline
    reliability = compute_reliability(
        metrics.get("recall_at_k", 0.0), checks.get("test_pass_rate"),
        checks.get("index_validate_ok", False),
        checks.get("determinism_ok", False))
    return {
        "schema": LEDGER_SCHEMA,
        "run": run_ts,
        "commit": git_meta.get("commit", "unknown"),
        "committed": git_meta.get("committed", ""),
        "subject": git_meta.get("subject", ""),
        "dirty": git_meta.get("dirty", False),
        "mode": ACR_GATE_MODE,
        "strategy": decided,
        "top_k": result.get("top_k"),
        "num_queries": result.get("num_queries"),
        # RELATIVE, PII-free gold path (R-LOC-03) — see redact_gold_path. Covers
        # the acr-gate row family too, so no recorded row of any mode leaks HOME.
        "gold_path": redact_gold_path(result.get("gold_path")),
        # Whole-corpus fingerprint of the scored corpus (inherited from the
        # ablation result). Keeps acr-gate rows corpus-aware too (Task 3 criterion:
        # EVERY recorded row carries a fingerprint).
        "corpus_fingerprint": result.get("corpus_fingerprint"),
        # Scored-corpus entry count (additive; inherited from the ablation result)
        # so acr-gate rows are corpus-size-aware for /api/scaling too.
        "corpus_size": result.get("corpus_size"),
        "as_of": result.get("as_of"),
        "metrics": metrics,
        "ablation": {
            "baseline": baseline,
            "treatment": treatment,
            "delta": result["delta"],
        },
        "acr_gate": {
            "passed": bool(decision.get("passed")),
            "decided_strategy": decided,
            "params": decision.get("params"),
            "checks": decision.get("checks"),
        },
        "checks": checks,
        "reliability": reliability,
    }

def record_acr_gate(kdir, data, gold, gold_path, result, decision, top_k, as_of):
    """Gather volatile state and append ONE acr-gate row. Read-only w.r.t. the KB.

    Mirrors `record_ledger` but stamps the bm25+acr determinism check at the
    PINNED `as_of` (INV-1) into the row's checks. Returns the appended row.
    """
    pass_rate, ran, failed = _record_test_passrate()
    from agentware_lib.knowledge import index
    checks = {
        "test_pass_rate": pass_rate,
        "tests_ran": ran,
        "tests_failed": failed,
        "index_validate_ok": not index.validate_index(kdir, data),
        "determinism_ok": _determinism_check_at(
            kdir, data, gold, top_k, "bm25+acr", as_of),
    }
    row = build_acr_gate_row(result, decision, git_info(), dates.utc_now_iso(), checks)
    append_ledger_row(os.path.join(kdir, HISTORY_REL), row)
    return row

def active_default_strategy(kdir):
    """Resolve the agent-facing default recall strategy from the ledger.

    Returns "bm25+acr" iff the MOST RECENT recorded ACR-gate decision PASSED;
    otherwise "bm25". No acr-gate row yet (fresh clone, or the live gate FAILED)
    -> "bm25" — the honest, baseline-preserving default. Pure read of the
    append-only ledger (INV-2); deterministic given the ledger state, so bare
    `recall` is byte-identical at a fixed ledger + `as_of` (INV-1). This is the
    auto-flip seam: agents call bare `recall` and inherit the gated default with
    no steering/agent edits.
    """
    rows = _read_ledger(os.path.join(kdir, HISTORY_REL))
    acr_rows = [r for r in rows if r.get("mode") == ACR_GATE_MODE]
    if not acr_rows:
        return "bm25"
    latest = max(acr_rows, key=lambda r: r.get("run") or "")
    gate = latest.get("acr_gate") or {}
    return "bm25+acr" if gate.get("passed") else "bm25"

def active_recall_strategy(kdir):
    """Resolve the bare-`recall` default strategy, honoring `retrieval_mode` (A/B).

    The Phase 7.1 wiring that makes the onboarding-chosen A/B mode actually change
    what bare `recall` (and the loop's `R-CTX-05` recall) ranks with:

      * Mode A (`deterministic`, the DEFAULT): the gated BM25 default from
        `active_default_strategy` (`bm25` / `bm25+acr`). Byte-identical to today —
        Mode A is untouched and works with nothing installed.
      * Mode B (`semantic`) WITH a local embedding model present: `bm25+embed`,
        the Phase 5 hybrid stack (a won LOCAL reranker would layer here in a later
        phase if/when the embed gate passes).
      * Mode B requested with NO local model: fall back to Mode A's default and
        surface a one-line notice. Mode A is the zero-install invariant; Mode B is
        inert until a local model exists (`resolve_effective_retrieval_mode`).

    Returns (strategy, notice). `notice` is '' UNLESS a semantic->deterministic
    fallback happened (kept off stdout by the caller so JSON capture stays clean).
    Pure read (INV-2); deterministic at a fixed ledger + config + model.
    """
    eff_mode, fell_back, notice = _vectorcache.resolve_effective_retrieval_mode()
    if eff_mode == config.RETRIEVAL_MODE_SEMANTIC:
        return "bm25+embed", ""
    # deterministic: either configured Mode A, or Mode B that fell back to A.
    return active_default_strategy(kdir), (notice if fell_back else "")

_EXPORTS = [
    'HISTORY_REL',
    'LEDGER_SCHEMA',
    'git_info',
    '_parse_failing_test_ids',
    '_record_test_passrate_detail',
    '_record_test_passrate',
    '_determinism_check',
    '_determinism_check_at',
    'compute_reliability',
    'redact_gold_path',
    'build_ledger_row',
    'append_ledger_row',
    '_read_ledger',
    'record_ledger',
    'record_ledger_suite',
    'ACR_GATE_MODE',
    'build_acr_gate_row',
    'record_acr_gate',
    'active_default_strategy',
    'active_recall_strategy',
]
