"""agentware_lib.ops.audit -- the `audit` command + all consistency checks (layer: ops).

Owns: the one-pass consistency audit -- cmd_audit + every _audit_* check EXCEPT
the dream heartbeat (steering-lint / unittest / benchmark-gate / bm25-cache /
mode-B-semantic / provenance-coverage / personal-data / graph-integrity), the
provenance_coverage read-only helper, and the Rule-7 graph_health metric emitter
(_append_graph_health_metric). Owns the `audit` parser registrar (register_audit).
Does NOT own: the dream-health check + its heartbeat closure (-> features.dream,
reached by a FUNCTION-LOCAL import for the audit<->dream cycle); steering lint (->
ops.steering); the release gate (-> ops.releasegate); freshness/staleness +
duplicate detection (-> ops.freshness); the ledger pass-rate probe (->
retrieval.ledger); ranking/eval primitives (-> retrieval.*); the KB index/graph/
privacy (-> knowledge.*). See catalog/modules.json.
Layer deps: ops.audit -> core (paths.REPO_ROOT, config, routing, frontmatter,
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
dates), knowledge (index, graph, privacy), retrieval (scorecard, bm25, vectorcache,
corpus, ledger), ops (steering, freshness) -- all downward or same-layer, reached
by MODULE ALIASES so the global-resolve gate never cascades into them. Reaches
features.dream ONLY via a function-local import inside cmd_audit (upward, lazy).
Determinism: every check is read-only w.r.t. the KB/index (INV-2); _audit_tests_check
/ _audit_gate_check shell an isolated subprocess (stdlib subprocess, INV-6);
_append_graph_health_metric appends best-effort to a gitignored log and never
blocks. No check feeds ranking (INV-1).
"""

import json
import os
import subprocess
import sys

from agentware_lib.core.paths import REPO_ROOT
import agentware_lib.core.config as config
import agentware_lib.core.routing as routing
import agentware_lib.core.frontmatter as frontmatter
import agentware_lib.core.dates as dates
import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.graph as graph
import agentware_lib.knowledge.privacy as privacy
import agentware_lib.ops.steering as _steering
import agentware_lib.ops.freshness as _freshness
import agentware_lib.retrieval.scorecard as _scorecard
import agentware_lib.retrieval.bm25 as _bm25
import agentware_lib.retrieval.vectorcache as _vectorcache
import agentware_lib.retrieval.corpus as _corpus
import agentware_lib.retrieval.ledger as _ledger


def _audit_steering_lint_check():
    result = _steering.lint_steering()
    details = []
    if not result["budget_ok"]:
        details.append("char budget exceeded: %d > %d" % (
            result["total_chars"], result["char_budget"]))
    for v in result["violations"]:
        details.append("%s %s:%d %s" % (v["rule"], v["file"], v["line"], v["text"]))
    for b in result["banned_words"]:
        details.append("DSF-5 %s:%d banned %r" % (b["file"], b["line"], b["word"]))
    return {
        "name": "steering_lint",
        "ok": result["ok"],
        "details": details or ["DSF-clean: %d/%d chars" % (
            result["total_chars"], result["char_budget"])],
    }

def _audit_tests_check():
    """Shell the stdlib unittest suite and report pass/fail as an audit check.

    Read-only, hermetic: the suite points the CLI at a synthetic temp KB via
    AGENTWARE_KNOWLEDGE_DIR (see tests/_fixtures.py), so it never touches the
    operator KB. subprocess is stdlib, so INV-6 (stdlib-only) holds.
    """
    tests_dir = os.path.join(REPO_ROOT, "tests")
    if not os.path.isdir(tests_dir):
        return {"name": "unittest", "ok": False,
                "details": ["tests/ directory not found at %s" % tests_dir]}
    try:
        test_env = dict(os.environ)
        test_env["AGENTWARE_NESTED_UNITTEST"] = "1"  # prevent config corruption
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=test_env)
    except OSError as exc:
        return {"name": "unittest", "ok": False,
                "details": ["could not launch unittest: %s" % exc]}
    ok = proc.returncode == 0
    lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
    ran_line = next((ln for ln in lines if ln.startswith("Ran ")), "")
    details = [ran_line.strip() or ("unittest exit %d" % proc.returncode)]
    if not ok:
        details.extend(lines[-12:])
    # Thread the failing test ids (parsed from the FAIL:/ERROR: header lines) into
    # the check payload so `audit --with-tests` consumers (e.g. dream step c) can
    # answer "which tests failed?" without re-running the suite. Sorted for a
    # byte-stable, diff-friendly payload (INV-1).
    failed_tests = sorted(_ledger._parse_failing_test_ids(proc.stderr or ""))
    return {"name": "unittest", "ok": ok, "details": details,
            "failed_tests": failed_tests}

def _audit_gate_check(kdir):
    """Run the benchmark regression gate as the audit's INV-7 enforcement.

    Shells `eval --strategy bm25 --gate --format json` as an isolated subprocess
    (so it measures an honest reliability via its own test pass-rate and stays
    decoupled from this process), then regenerates SCORECARD.md from the freshly
    appended row. FAILS the audit on a beyond-tolerance regression (non-zero
    gate exit). A first-ever run seeds + passes. When no gold set exists yet the
    check is a soft pass (nothing to benchmark). Read-only w.r.t. index.json;
    only appends to the append-only ledger + regenerates the derived scorecard.
    """
    gold = os.path.join(kdir, "benchmarks", "recall-gold.json")
    if not os.path.isfile(gold):
        return {"name": "benchmark_gate", "ok": True,
                "details": ["no gold set at %s; skipped" % gold]}
    env = dict(os.environ)
    env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "scripts", "agentware"),
             "eval", "--strategy", "bm25", "--gate", "--format", "json"],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env)
    except OSError as exc:
        return {"name": "benchmark_gate", "ok": False,
                "details": ["could not launch eval --gate: %s" % exc]}
    ok = proc.returncode == 0
    details = []
    try:
        payload = json.loads(proc.stdout)
        g = payload.get("gate", {}) or {}
        rec = payload.get("recorded", {}) or {}
        if g.get("seeded"):
            details.append("seeded baseline (no prior bm25 history)")
        details.append("reliability=%.2f commit=%s tolerance=%.4f" % (
            rec.get("reliability", 0.0), rec.get("commit", "?"),
            g.get("tolerance", 0.0)))
        for r in g.get("regressions", []) or []:
            details.append("REGRESSION %s best=%.4f current=%.4f" % (
                r.get("metric"), r.get("baseline", 0.0), r.get("current", 0.0)))
    except ValueError:
        details.append("eval --gate exit %d" % proc.returncode)
        if not ok:
            details.extend((proc.stderr or "").splitlines()[-6:])
    try:
        _scorecard.write_scorecard(kdir)
    except OSError as exc:
        details.append("scorecard regen failed: %s" % exc)
    return {"name": "benchmark_gate", "ok": ok, "details": details}

def _audit_bm25_cache_check(kdir, data):
    """Cache-integrity guard for the derived BM25 postings cache (Rule 7).

    The cache is an OPTIMIZATION, never a correctness dependency, so ABSENCE is a
    PASS (recall falls back to the live scan). When the cache IS present it must be
    FRESH and derivable — a present-but-stale cache is the silent-divergence risk
    this check surfaces so the dashboard can show cache integrity. Remedy on a FAIL
    is always `scripts/agentware index rebuild` (the sole writer). Read-only.
    """
    cache = _bm25.load_bm25_cache(kdir)
    if cache is None:
        return {"name": "bm25_cache_health", "ok": True,
                "details": ["absent or unreadable: recall uses the live scan "
                            "(cache is an optimization, not a dependency)"]}
    fresh = _bm25.bm25_cache_is_fresh(cache, data, kdir)
    return {
        "name": "bm25_cache_health",
        "ok": fresh,
        "details": ["fresh: postings match live KB content (%d term(s), n=%d)"
                    % (len(cache.get("postings") or {}), cache.get("n", 0))]
        if fresh else
        ["STALE: postings cache diverges from live KB content — run "
         "`scripts/agentware index rebuild` to regenerate (recall is still "
         "correct: it ignores a stale cache and falls back to the live scan)"],
    }

def _audit_mode_b_health_check(kdir, data):
    """Mode-B (semantic) health for the dashboard (Rule 7; feature
    260627-semantic-embedding-settings).

    INERT in deterministic mode (the zero-install default) — it returns a PASS and
    never false-alarms users who never opted in. When the CONFIGURED retrieval mode
    is `semantic`, it verifies the two things Mode B needs to actually run:
      1. a LOCAL embedder is loadable (the optional backend is installed), and
      2. the derived vector cache is present AND fresh (every live corpus entry has
         a cached vector whose content fingerprint matches).
    A missing backend or a missing/stale cache is a clear, ACTIONABLE warning
    (FAILs the check) so the operator knows semantic is silently falling back to
    Mode A. Read-only (INV-2): never loads/writes the cache as a side effect beyond
    a read. Maps to the eval-ledger retrieval metrics (Recall@5/nDCG@5/MRR) — no new
    hot-path cost.
    """
    configured = config.resolve_retrieval_mode()
    if configured != config.RETRIEVAL_MODE_SEMANTIC:
        return {"name": "mode_b_health", "ok": True,
                "details": ["inert: retrieval_mode=deterministic (Mode A / BM25, "
                            "zero-install default) — no semantic backend required"]}
    problems = []
    if not _vectorcache.semantic_embedder_available():
        problems.append(
            "no LOCAL embedder is loadable (set one with `scripts/agentware config "
            "--set-embedder <backend>` and install it, e.g. "
            "`python3 -m pip install fastembed==0.8.0`) — semantic is falling back "
            "to Mode A (BM25)")
    else:
        model = routing.resolve_embed_model()
        cache = _vectorcache.load_vector_cache(kdir, model)
        if cache is None:
            problems.append(
                "vector cache for model %r is missing/unreadable — run "
                "`scripts/agentware index rebuild` to build it (recall still works: "
                "it falls back to Mode A until the cache exists)" % model)
        else:
            cached = cache.get("vectors") or {}
            stale = []
            for entry, doc_text in _corpus.build_corpus(kdir, data):
                eid = entry.get("id")
                if eid is None:
                    continue
                hit = cached.get(eid)
                if not (isinstance(hit, dict)
                        and hit.get("fp") == _vectorcache._corpus_fingerprint(doc_text)):
                    stale.append(eid)
            if stale:
                problems.append(
                    "vector cache is STALE for %d entr(y/ies) (e.g. %s) — run "
                    "`scripts/agentware index rebuild` to regenerate (recall stays "
                    "correct: a stale/missing vector falls back to the BM25 floor)"
                    % (len(stale), ", ".join(sorted(stale)[:5])))
    if problems:
        return {"name": "mode_b_health", "ok": False, "details": problems}
    model = routing.resolve_embed_model()
    cache = _vectorcache.load_vector_cache(kdir, model) or {}
    return {"name": "mode_b_health", "ok": True,
            "details": ["healthy: semantic mode active, embedder loadable, vector "
                        "cache fresh (model %s, %d entr(y/ies))"
                        % (model, len(cache.get("vectors") or {}))]}

def provenance_coverage(kdir, data):
    """Return (missing_author_ids, total) over indexed entries (READ-ONLY).

    An entry "has provenance" when its file frontmatter carries a non-empty
    `author`. In a SHARED (team) KB this attributes each learning to the member
    who wrote it; missing-`author` entries are the gap the provenance-mix panel
    surfaces. Stdlib-only, no clock/network. Feature 260625-team-mode-onboarding-fork.
    """
    missing, total = [], 0
    for e in (data.get("entries") or []):
        rel = e.get("path")
        if not rel:
            continue
        total += 1
        fm = frontmatter.read_entry_frontmatter(os.path.join(kdir, rel))
        if not (fm.get("author") or "").strip():
            missing.append(e.get("id") or rel)
    return missing, total

def _audit_provenance_coverage_check(kdir, data):
    """Per-user provenance coverage (Rule 7 observability; advisory in power mode).

    Reports indexed entries whose frontmatter lacks an `author`. In TEAM mode this
    FAILS the check so a shared KB cannot silently lose attribution; in POWER mode
    it is purely INFORMATIONAL (never flips `ok` — a solo KB predating provenance
    is fine). Read-only w.r.t. the KB/index (INV-2).
    """
    missing, total = provenance_coverage(kdir, data)
    mode = config.resolve_kb_mode()
    ok = (not missing) or (mode != config.KB_MODE_TEAM)
    if not missing:
        details = ["full coverage: %d/%d indexed entr(y/ies) carry an `author`"
                   % (total, total)]
    else:
        covered = total - len(missing)
        sev = "FAIL (team mode)" if mode == config.KB_MODE_TEAM else "advisory (power mode)"
        details = ["%s: %d/%d entr(y/ies) missing `author` provenance: %s"
                   % (sev, len(missing), total, ", ".join(sorted(missing)[:20]))]
        if covered:
            details.append("%d/%d already attributed" % (covered, total))
    return {"name": "provenance_coverage", "ok": ok, "details": details}

def _audit_personal_data_check(kdir):
    """R-LOC-03 guard: the package must ship ZERO operator personal data.

    Resolves the LIVE operator identity (handle, email, HOME path) at runtime
    and scans the package tree (REPO_ROOT) for those exact strings. Hardcodes
    nothing, so the package still ships zero personal data. FAILS if any leak is
    present. Read-only.
    """
    needles = privacy.operator_identity_strings(kdir)
    hits = privacy.scan_personal_data(REPO_ROOT, needles)
    return {
        "name": "personal_data",
        "ok": not hits,
        "details": hits or [
            "clean: no operator identity in package tree "
            "(%d needle(s) checked)" % len(needles)],
    }

def _audit_graph_integrity_check(kdir, data):
    """Declared-edge graph health (INV-2 advisory; never auto-mutates).

    Reports DANGLING edges (a `relates` target not in the index) and UNKNOWN
    relation types — both FAIL the check. CYCLES are reported advisory-only and
    do NOT flip `ok` (a cyclic dependency graph is a signal, not corruption). A
    clean/empty KB ⇒ ok:True. Read-only.
    """
    dangling, unknown = graph.graph_relation_errors(data)
    forward, _reverse = graph.build_adjacency(data)
    cycles = graph.find_cycles(forward)
    details = []
    for (eid, target) in dangling:
        details.append("dangling edge: '%s' -> '%s' (target not in index)"
                       % (eid, target))
    for (eid, tok) in unknown:
        details.append("unknown relation type: '%s' declared by '%s' (vocab: %s)"
                       % (tok, eid, ", ".join(frontmatter.RELATION_TYPES)))
    for cyc in cycles:
        details.append("cycle (advisory): %s"
                       % " -> ".join(list(cyc) + [cyc[0]]))
    ok = not dangling and not unknown
    if not details:
        details = ["clean: no dangling edges, unknown types, or cycles"]
    return {"name": "graph_integrity", "ok": ok, "details": details}

def _append_graph_health_metric(kdir, data, ts=None):
    """Rule-7 observability: append one advisory graph_health line to
    `<kdir>/logs/metrics.jsonl` (gitignored, append-only).

    Best-effort + read-only w.r.t. the KB/index (INV-2): a write failure never
    blocks the caller. `ts` is metadata only — passed in, never read by ranking.
    """
    try:
        gh = graph.graph_health(data)
        rec = {"event": "graph_health", "ts": ts or dates.utc_now_iso()}
        rec.update(gh)
        logs = os.path.join(kdir, "logs")
        os.makedirs(logs, exist_ok=True)
        with open(os.path.join(logs, "metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass

def cmd_audit(args):
    kdir = config.require_knowledge_dir()
    if not kdir:
        return 1
    as_json = getattr(args, "format", "text") == "json"
    data, err = index.load_index(kdir)
    if err is not None:
        checks = [{"name": "index_validate", "ok": False, "details": [err]}]
    else:
        from agentware_lib.features.dream import _audit_dream_health_check
        ve = index.validate_index(kdir, data)
        un = index.scan_unindexed(kdir, data)
        te = index.tag_consistency_errors(data)
        tce = index.template_conformance_errors(kdir, data)
        oe = index.orphaned_learning_errors(kdir, data)
        checks = [
            {"name": "index_validate", "ok": not ve, "details": ve},
            {"name": "filesystem_sync", "ok": not un, "details": un},
            {"name": "tag_consistency", "ok": not te, "details": te},
            {"name": "template_conformance", "ok": not tce, "details": tce},
            {"name": "orphaned_learnings", "ok": not oe, "details": oe},
            _audit_steering_lint_check(),
            _audit_personal_data_check(kdir),
            _audit_bm25_cache_check(kdir, data),
            _audit_mode_b_health_check(kdir, data),
            _audit_graph_integrity_check(kdir, data),
            _audit_provenance_coverage_check(kdir, data),
            _audit_dream_health_check(kdir),
        ]
        # Rule-7 observability: advisory graph-health signal (append-only,
        # gitignored, read-only w.r.t. the KB/index).
        _append_graph_health_metric(kdir, data)
    if getattr(args, "with_tests", False):
        checks.append(_audit_tests_check())
        if err is None:
            checks.append(_audit_gate_check(kdir))
    stale_report = None
    if getattr(args, "stale", False) and err is None:
        stale_report = _freshness.build_stale_report(
            kdir, data, getattr(args, "max_age_days", 120))
    ok = all(c["ok"] for c in checks)
    if as_json:
        payload = {"ok": ok, "checks": checks}
        if stale_report is not None:
            payload["stale_report"] = stale_report
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print("AUDIT %s (%d check(s)):" % ("PASSED" if ok else "FAILED", len(checks)))
        for c in checks:
            print("  [%s] %s" % ("ok" if c["ok"] else "FAIL", c["name"]))
            if not c["ok"]:
                for d in c["details"]:
                    print("      - %s" % d)
        if stale_report is not None:
            _freshness._print_stale_report(stale_report)
    return 0 if ok else 1


def register_audit(sub, fmt_parent):
    # audit ------------------------------------------------------------------
    p_audit = sub.add_parser(
        "audit", parents=[fmt_parent], help="run all consistency checks in one pass")
    p_audit.add_argument(
        "--with-tests", action="store_true",
        help="also run the unittest suite (tests/) and fail the audit on failure")
    p_audit.add_argument(
        "--stale", action="store_true",
        help="report aged volatile entries + near-duplicate/conflict pairs (advisory)")
    p_audit.add_argument(
        "--max-age-days", type=int, default=120, dest="max_age_days",
        help="staleness window in days for --stale (default: 120)")
    p_audit.set_defaults(func=cmd_audit)


_EXPORTS = [
    "_audit_steering_lint_check",
    "_audit_tests_check",
    "_audit_gate_check",
    "_audit_bm25_cache_check",
    "_audit_mode_b_health_check",
    "provenance_coverage",
    "_audit_provenance_coverage_check",
    "_audit_personal_data_check",
    "_audit_graph_integrity_check",
    "_append_graph_health_metric",
    "cmd_audit",
]
