"""agentware_lib.retrieval.regressiongate - the INV-7 regression gate + the clean-HEAD vs working-tree A/B gate (layer: retrieval).

Owns: the deterministic no-regression gate guarding every package update
(`eval --gate`). The gated headline-metric set (GATE_METRICS); the best-comparable-
baseline selector (gate_baseline - MAX per metric among prior rows sharing
strategy+suite+corpus_fingerprint); the pass/fail-within-tolerance decision
(evaluate_gate); the --record/--gate orchestrator that reads the prior ledger BEFORE
appending so a fresh row never gates against itself (_record_and_gate); the gate
exit-code (_gate_exit) and the human-readable gate printer (_print_gate). AND the
clean-HEAD vs working-tree A/B gate (`eval --gate --ab`) that isolates THIS diff's
recall effect from live-KB corpus drift: the pure verdict (_ab_verdict - PASS iff
working >= head - tolerance); the read-only (INV-2) HEAD extraction of the WHOLE
scripts/ tree via `git archive HEAD scripts` (per-blob `git show` fallback) with a
synthesized AGENTS.md marker so the split package's marker-walk resolves
(_extract_head_cli / _extract_head_scripts) and its guarded temp-dir cleanup
(_cleanup_head_cli); the HEAD-CLI subprocess recall scorer (_ab_head_recall); the
assembler (run_ab_gate) and its printer (_print_ab).

Does NOT own: the append-only benchmark ledger it reads + writes (HISTORY_REL /
_read_ledger / record_ledger -> agentware_lib.retrieval.ledger, referenced via the
_ledger module alias); the ranking engine + gold-fixture scorer feeding a gate run
(agentware_lib.retrieval.{metrics_rank,goldfixture}, reached by resident callers as
bare names); the repo-root anchor (REPO_ROOT -> agentware_lib.core.paths, imported as
a BARE name so the A/B integration test can monkeypatch regressiongate.REPO_ROOT to a
throwaway git repo - the owner-module-attribute form, since this seam's HEAD-extraction
functions ARE monkeypatched, unlike the pure/never-patched seams before it); the git
EXEC primitive (_git_capture -> agentware_lib.core.gitio via gitio); the today-stamp
(dates._today -> agentware_lib.core.dates); the ACR win gate
(agentware_lib.retrieval.acrgate); the scorecard + SWE bench (later wave-3 seams, still
resident); the eval CLI handlers (cmd_eval + the gold-fixture gate path, now
agentware_lib.retrieval.evalcmd, wave-3 seam 16b) - they call gate_baseline /
evaluate_gate / _record_and_gate / _gate_exit / _print_gate / run_ab_gate / _print_ab
via the _regressiongate module alias by attribute.
Layer deps: retrieval -> core (agentware_lib.core.{paths,dates,gitio}) + retrieval ->
retrieval (agentware_lib.retrieval.ledger as _ledger, referenced by ATTRIBUTE so the
global-resolve gate never walks a foreign sibling function held in this module's
__dict__). Imports nothing in ops/features/knowledge; nothing in core/ or knowledge/
imports from here.
Fan-in: 2 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).

INV-2: read-only w.r.t. the work tree / index - the A/B HEAD extraction only reads git
objects (`git archive` / `git show` / `git ls-tree`) and writes ONLY under a throwaway
`agentware-head-*` temp dir, cleaned up by a guarded rmtree that refuses any path not
directly under the system temp root. INV-7: gate_baseline + evaluate_gate are pure over
the injected ledger rows, so at a fixed ledger the gate verdict is deterministic; a
first-ever comparable run seeds + PASSES (never a false-fail on corpus growth).
"""

import json
import os
import shutil
import subprocess
import sys

from agentware_lib.core import dates
from agentware_lib.core import gitio
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.retrieval import ledger as _ledger


GATE_METRICS = ("recall_at_k", "precision_at_k", "ndcg_at_k", "mrr")

def gate_baseline(rows, strategy, suite=None, corpus_fingerprint=None):
    """Best-seen value per gated metric + reliability among COMPARABLE rows.

    Comparable = rows recorded with the same retrieval `strategy` AND the same
    benchmark `suite` AND the same `corpus_fingerprint` (apples to apples: a bm25
    run is never gated against a tag-only baseline, a session-level LongMemEval
    row is never gated against an own-gold-set row even when both are
    strategy="bm25", AND an own-gold run is never gated against a row scored over
    a DIFFERENT corpus). `suite=None` selects the historical own-gold-set rows
    (which carry no/None suite).

    The `corpus_fingerprint` filter is what makes own-gold gating REPRODUCIBLE:
    the live operator KB grows over time, so a row recorded against a smaller
    corpus has a different fingerprint and is NOT comparable to a run over the
    grown corpus -> the gate seeds a fresh baseline and PASSES instead of
    false-failing on corpus drift (byte-identical code). Legacy rows recorded
    before fingerprinting carry no `corpus_fingerprint` (None) and so form their
    own distinct bucket -> they never gate a new fingerprinted run. Callers that
    omit `corpus_fingerprint` (None) match only rows whose fingerprint is also
    None (legacy semantics preserved).

    The baseline is the MAX of each metric across all prior comparable rows, so a
    run can never silently drop below any earlier peak. Returns None when there
    is no comparable history (first-ever run for this strategy+suite+corpus ->
    seeds + passes).
    """
    comparable = [r for r in rows
                  if r.get("strategy") == strategy and r.get("suite") == suite
                  and r.get("corpus_fingerprint") == corpus_fingerprint]
    if not comparable:
        return None
    base = {}
    for key in GATE_METRICS:
        vals = [r.get("metrics", {}).get(key) for r in comparable]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if vals:
            base[key] = max(vals)
    rels = [r.get("reliability") for r in comparable
            if isinstance(r.get("reliability"), (int, float))]
    if rels:
        base["reliability"] = max(rels)
    return base

def evaluate_gate(metrics, reliability, baseline, tolerance):
    """Decide pass/fail vs a baseline. Returns (passed, regressions).

    Each gated metric must stay within `tolerance` of the best prior value
    (current >= best - tolerance). Reliability is a 0-100 score, so its
    allowance is `tolerance * 100` points. `baseline is None` => first run =>
    pass with no regressions (caller marks it seeded). `regressions` is a list of
    (metric, baseline_value, current_value) for every metric that dropped too far.
    """
    if baseline is None:
        return True, []
    regressions = []
    for key in GATE_METRICS:
        if key not in baseline:
            continue
        cur = metrics.get(key)
        if not isinstance(cur, (int, float)):
            continue
        if cur < baseline[key] - tolerance:
            regressions.append((key, baseline[key], cur))
    if "reliability" in baseline and isinstance(reliability, (int, float)):
        if reliability < baseline["reliability"] - tolerance * 100.0:
            regressions.append(
                ("reliability", baseline["reliability"], reliability))
    return (len(regressions) == 0), regressions

def _record_and_gate(args, kdir, data, gold, gold_path, result):
    """Apply --record / --gate to a computed eval result.

    --gate implies --record (every gated run appends an immutable row). Reads the
    prior ledger BEFORE appending so the new row never compares against itself.
    Returns (recorded_or_None, gate_or_None); attaches `recorded`/`gate` to
    `result` for the JSON output.
    """
    want_gate = getattr(args, "gate", False)
    if not (getattr(args, "record", False) or want_gate):
        return None, None
    ledger_path = os.path.join(kdir, _ledger.HISTORY_REL)
    prior_rows = _ledger._read_ledger(ledger_path) if want_gate else None
    recorded = _ledger.record_ledger(kdir, data, gold, gold_path, result, args.top_k)
    result["recorded"] = {
        "path": ledger_path, "commit": recorded["commit"],
        "run": recorded["run"], "reliability": recorded["reliability"],
    }
    gate = None
    if want_gate:
        baseline = gate_baseline(prior_rows, recorded["strategy"],
                                 recorded.get("suite"),
                                 recorded.get("corpus_fingerprint"))
        passed, regressions = evaluate_gate(
            recorded["metrics"], recorded["reliability"],
            baseline, args.tolerance)
        gate = {
            "passed": passed,
            "tolerance": args.tolerance,
            "strategy": recorded["strategy"],
            "seeded": baseline is None,
            "baseline": baseline,
            "regressions": [
                {"metric": m, "baseline": b, "current": c}
                for m, b, c in regressions
            ],
        }
        result["gate"] = gate
    return recorded, gate

def _gate_exit(gate, ab=None):
    """Exit code for a gate result: non-zero (1) only on a real regression.

    When an A/B result is supplied (`eval --gate --ab`), a real A/B FAIL also
    forces exit 1 — the clean-HEAD vs working-tree comparison is an independent
    regression signal layered on top of the absolute baseline gate.
    """
    code = 0 if (gate is None or gate["passed"]) else 1
    if ab is not None and not ab["passed"]:
        code = 1
    return code

def _print_gate(gate):
    """Human-readable gate verdict for text format."""
    if gate is None:
        return
    if gate["seeded"]:
        print("  gate: PASS (seeded baseline; no prior %s history)"
              % gate["strategy"])
        return
    if gate["passed"]:
        print("  gate: PASS (no regression beyond tolerance %.4f)"
              % gate["tolerance"])
        return
    print("  gate: FAIL (regression beyond tolerance %.4f)" % gate["tolerance"])
    for r in gate["regressions"]:
        print("    %-16s best=%.4f  current=%.4f  drop=%.4f" % (
            r["metric"], r["baseline"], r["current"],
            r["baseline"] - r["current"]))

def _ab_verdict(working, head, tolerance):
    """Pure A/B decision: (passed, delta). PASS iff working >= head - tolerance."""
    delta = working - head
    return (working >= head - tolerance), delta

def _extract_head_cli():
    """Extract HEAD's scripts/ tree to a throwaway temp dir; return
    (entrypoint_path, head_commit). Read-only (INV-2).

    Extracts the WHOLE scripts/ tree (not just the entrypoint blob) so the split
    agentware_lib package sits next to the entrypoint and `import agentware_lib...`
    resolves under the extracted CLI's own sys.path bootstrap; a self-contained
    monolith HEAD extracts fine too. An AGENTS.md marker is synthesized in the
    temp dir so agentware_lib.core.paths' marker-walk REPO_ROOT resolution
    succeeds (the extracted tree carries no AGENTS.md of its own).

    Returns (None, None) when git/HEAD/the tree is unavailable. Writes ONLY under
    a throwaway temp dir — never the work tree or the index. The caller removes
    the temp dir via _cleanup_head_cli(entrypoint_path).
    """
    head_commit = gitio._git_capture(["rev-parse", "--short", "HEAD"])
    if not head_commit:
        return None, None
    import tempfile
    tmpdir = tempfile.mkdtemp(prefix="agentware-head-")
    entry = os.path.join(tmpdir, "scripts", "agentware")
    ok = _extract_head_scripts(tmpdir)
    if ok:
        # Synthesize the repo-root marker in the throwaway tree so the (possibly
        # split) entrypoint's marker-walk paths.py resolves. Temp dir only (INV-2).
        try:
            src_marker = os.path.join(REPO_ROOT, "AGENTS.md")
            dst_marker = os.path.join(tmpdir, "AGENTS.md")
            if os.path.isfile(src_marker):
                shutil.copy2(src_marker, dst_marker)
            else:
                with open(dst_marker, "w", encoding="utf-8") as f:
                    f.write("")
        except OSError:
            ok = False
    if not ok or not os.path.isfile(entry):
        shutil.rmtree(tmpdir, ignore_errors=True)
        return None, None
    return entry, head_commit

def _extract_head_scripts(tmpdir):
    """Extract HEAD:scripts/ into <tmpdir>/scripts/. Return True on success.

    Prefers `git archive HEAD scripts | tar -x -C tmpdir` (single 'scripts'
    pathspec — extra pathspecs make git archive exit 128 on a scripts-only repo);
    falls back to a per-blob loop over `git ls-tree -r --name-only HEAD scripts`.
    Read-only: reads git objects, writes only under tmpdir.
    """
    try:
        archive = subprocess.run(
            ["git", "archive", "HEAD", "scripts"], cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if archive.returncode == 0 and archive.stdout:
            tar = subprocess.run(
                ["tar", "-x", "-C", tmpdir], input=archive.stdout,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if tar.returncode == 0 and os.path.isfile(
                    os.path.join(tmpdir, "scripts", "agentware")):
                return True
    except OSError:
        pass
    listing = gitio._git_capture(["ls-tree", "-r", "--name-only", "HEAD", "scripts"])
    if not listing:
        return False
    got_entry = False
    for rel in listing.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        try:
            proc = subprocess.run(
                ["git", "show", "HEAD:%s" % rel], cwd=REPO_ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError:
            return False
        if proc.returncode != 0:
            continue
        dest = os.path.join(tmpdir, rel)
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(proc.stdout)
        except OSError:
            return False
        if rel == "scripts/agentware":
            got_entry = True
    return got_entry

def _cleanup_head_cli(head_path):
    """Remove the throwaway HEAD extraction created by _extract_head_cli.

    head_path is <tmpdir>/scripts/agentware. Removes the tmpdir ONLY when it is a
    genuine `agentware-head-*` dir directly under the system temp root — never a
    derived or foreign path (guards against mocked test paths and any accidental
    broad delete). Any other path falls back to a best-effort file unlink.
    """
    if not head_path:
        return
    import tempfile
    tmpdir = os.path.dirname(os.path.dirname(os.path.abspath(head_path)))
    base = os.path.basename(tmpdir)
    sys_tmp = os.path.realpath(tempfile.gettempdir())
    if (base.startswith("agentware-head-")
            and os.path.realpath(os.path.dirname(tmpdir)) == sys_tmp
            and os.path.isdir(tmpdir)):
        shutil.rmtree(tmpdir, ignore_errors=True)
        return
    try:
        os.unlink(head_path)
    except OSError:
        pass

def _ab_head_recall(head_path, kdir, strategy, top_k, as_of, gold_path):
    """Score the HEAD CLI's plain eval over the SAME corpus; return recall_at_k.

    Runs the extracted HEAD script as a subprocess (a plain read-only eval — NO
    --record/--gate, so the ledger is untouched), pinning AGENTWARE_KNOWLEDGE_DIR
    so it scores the IDENTICAL live corpus regardless of the temp file's location.
    Returns the float recall_at_k, or None if the HEAD run produced none.
    """
    env = dict(os.environ)
    env["AGENTWARE_KNOWLEDGE_DIR"] = kdir
    argv = [sys.executable, head_path, "eval",
            "--strategy", strategy, "--top-k", str(top_k),
            "--as-of", as_of, "--gold", gold_path, "--format", "json"]
    try:
        proc = subprocess.run(
            argv, cwd=REPO_ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, env=env)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return None
    recall = (payload.get("metrics") or {}).get("recall_at_k")
    return recall if isinstance(recall, (int, float)) else None

def run_ab_gate(args, kdir, result, gold_path):
    """Clean-HEAD vs working-tree A/B over the SAME corpus (read-only).

    Attaches `result["ab"]` and returns it. PASS iff working >= head - tolerance.
    An unavailable HEAD/git (or a HEAD run that yields no recall) PASSES with
    available=False — it cannot prove this diff caused a regression.
    """
    working = (result.get("metrics") or {}).get("recall_at_k")
    tolerance = args.tolerance
    strategy = result.get("strategy") or getattr(args, "strategy", "bm25")
    top_k = result.get("top_k") or args.top_k
    as_of = result.get("as_of") or dates._today()
    head_path, head_commit = _extract_head_cli()
    if head_path is None:
        ab = {"available": False, "passed": True,
              "reason": "git HEAD scripts/agentware unavailable",
              "working": working, "head": None, "delta": None,
              "tolerance": tolerance, "head_commit": None, "strategy": strategy}
        result["ab"] = ab
        return ab
    try:
        head = _ab_head_recall(head_path, kdir, strategy, top_k, as_of, gold_path)
    finally:
        _cleanup_head_cli(head_path)
    if head is None or working is None:
        ab = {"available": False, "passed": True,
              "reason": "HEAD eval produced no recall score",
              "working": working, "head": head, "delta": None,
              "tolerance": tolerance, "head_commit": head_commit,
              "strategy": strategy}
        result["ab"] = ab
        return ab
    passed, delta = _ab_verdict(working, head, tolerance)
    ab = {"available": True, "passed": passed,
          "working": working, "head": head, "delta": delta,
          "tolerance": tolerance, "head_commit": head_commit,
          "strategy": strategy}
    result["ab"] = ab
    return ab

def _print_ab(ab):
    """Human-readable A/B verdict for text format."""
    if ab is None:
        return
    if not ab.get("available", True):
        print("  ab: PASS (clean-HEAD A/B unavailable: %s)"
              % ab.get("reason", "n/a"))
        return
    print("  ab: %s  working=%.4f  head=%.4f  delta=%+.4f  tol=%.4f  head=%s" % (
        "PASS" if ab["passed"] else "FAIL",
        ab["working"], ab["head"], ab["delta"], ab["tolerance"],
        ab.get("head_commit") or "?"))

_EXPORTS = [
    'GATE_METRICS',
    'gate_baseline',
    'evaluate_gate',
    '_record_and_gate',
    '_gate_exit',
    '_print_gate',
    '_ab_verdict',
    '_extract_head_cli',
    '_extract_head_scripts',
    '_cleanup_head_cli',
    '_ab_head_recall',
    'run_ab_gate',
    '_print_ab',
]
