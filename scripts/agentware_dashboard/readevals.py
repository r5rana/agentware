"""agentware_dashboard.readevals — the QUALITY / EVALUATION read model.

Owns: the payloads read from the benchmark ledger and the alert derivation —
the raw quality ledger (`/api/quality`), the eval-vs-ACR-gate split
(`/api/evals`), the retrieval scaling trend and its slope (`/api/scaling`), the
severity-ranked alert list (`/api/alerts`), and the post-phase self-assessment
text for one feature (`/api/assessments/<feature>`).
Does NOT own: the ledger format or its sole writer (`eval --record`), the alert
rules (`agentware_lib.ops.alerts`), or path confinement (`confine`).

READ-ONLY: `benchmarks/history.jsonl` is read, never appended to, and the
assessment endpoint reads one confined file under `<kdir>/work/`.
"""

import os
import urllib.parse
from http import HTTPStatus

from agentware_lib.ops import alerts
from agentware_lib.retrieval import ledger

from agentware_dashboard import confine
from agentware_dashboard.reqparams import NO_KDIR, kdir_of

#: The one artifact `/api/assessments/<feature>` may disclose from a work dir.
ASSESSMENT_FILE = "assessment.md"


def _ledger_rows(kdir):
    """Every ledger row for this knowledge dir, oldest first."""
    return ledger._read_ledger(os.path.join(kdir, ledger.HISTORY_REL))


def linear_slope(points):
    """Ordinary-least-squares slope over (x, y) numeric points; None if < 2 or
    degenerate. Used by /api/scaling to summarize Recall@k vs corpus size."""
    pts = [(x, y) for (x, y) in points
           if isinstance(x, (int, float)) and not isinstance(x, bool)
           and isinstance(y, (int, float)) and not isinstance(y, bool)]
    n = len(pts)
    if n < 2:
        return None
    sx = sum(x for x, _ in pts)
    sy = sum(y for _, y in pts)
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return (n * sxy - sx * sy) / denom


def api_quality(request):
    """`/api/quality` — the raw ledger plus a per-run reliability series."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, ledger=[], count=0, latest=None)
    rows = _ledger_rows(kdir)
    series = [{
        "run": r.get("run"),
        "commit": r.get("commit"),
        "strategy": r.get("strategy"),
        "suite": r.get("suite"),
        "reliability": r.get("reliability"),
        "metrics": r.get("metrics", {}),
    } for r in rows]
    return HTTPStatus.OK, {
        "ledger": rows,
        "series": series,
        "count": len(rows),
        "latest": rows[-1] if rows else None,
    }


def api_scaling(request):
    """`/api/scaling` — Recall@k against corpus size, with an OLS slope."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, points=[], slope=None, count=0)
    points = []
    xy = []
    for r in _ledger_rows(kdir):
        recall = (r.get("metrics") or {}).get("recall_at_k")
        size = r.get("corpus_size")
        points.append({
            "corpus_size": size,
            "recall_at_k": recall,
            "commit": r.get("commit"),
            "run": r.get("run"),
            "strategy": r.get("strategy"),
        })
        if isinstance(size, (int, float)) and not isinstance(size, bool) \
                and isinstance(recall, (int, float)):
            xy.append((size, recall))
    return HTTPStatus.OK, {
        "points": points,
        "slope": linear_slope(xy),
        "count": len(points),
        "measured": len(xy),
    }


def _eval_row(r):
    """One ledger row projected onto the eval trend series."""
    m = r.get("metrics") or {}
    return {
        "run": r.get("run"),
        "commit": r.get("commit"),
        "strategy": r.get("strategy"),
        "suite": r.get("suite"),
        "mode": r.get("mode"),
        "reliability": r.get("reliability"),
        "recall_at_k": m.get("recall_at_k"),
        "ndcg_at_k": m.get("ndcg_at_k"),
        "mrr": m.get("mrr"),
        "precision_at_k": m.get("precision_at_k"),
        "corpus_size": r.get("corpus_size"),
    }


def _acr_row(r):
    """One ACR-gate ledger row projected onto the gate-decision series."""
    gate = r.get("acr_gate") or {}
    return {
        "run": r.get("run"),
        "commit": r.get("commit"),
        "decided_strategy": gate.get("decided_strategy"),
        "passed": gate.get("passed"),
        "checks": gate.get("checks", {}),
    }


def api_evals(request):
    """Evaluation & quality: the eval/gate ledger trend — Recall@k / nDCG / MRR
    / reliability per run — SPLIT from the ACR-gate decisions, both read
    READ-ONLY from benchmarks/history.jsonl (the sole eval writer is the CLI's
    `eval --record`). Pairs with /api/assessments/<feature> for the post-phase
    self-assessment text."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, series=[], acr=[], count=0,
                                   acr_count=0, latest=None, latest_acr=None)
    series = []
    acr = []
    for r in _ledger_rows(kdir):
        if r.get("mode") == "acr-gate":
            acr.append(_acr_row(r))
            continue
        series.append(_eval_row(r))
    return HTTPStatus.OK, {
        "series": series,
        "acr": acr,
        "count": len(series),
        "acr_count": len(acr),
        "latest": series[-1] if series else None,
        "latest_acr": acr[-1] if acr else None,
    }


def api_alerts(request):
    """Symptom-based, severity-ranked alerts: reliability/nDCG regression,
    retrieval scaling-slope, cost spike, stuck-loop/runaway, stale/conflicting
    KB, and unpromoted LEARNED/DECISION at finish — each with a deep-link to its
    panel. Also returns the ledger `commit_markers` so the trend charts can place
    commit markers aligned to ledger SHAs. Delegates to the read-only toolkit
    derivation; degrades to an empty list when sources are absent."""
    kdir = kdir_of(request)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, alerts=[], open_count=0,
                                   summary={"critical": 0, "warning": 0,
                                            "info": 0},
                                   status="ok", commit_markers=[])
    return HTTPStatus.OK, alerts.derive_alerts(kdir)


def api_assessments(request, feature):
    """Post-phase self-ASSESSMENT for one feature: the raw
    work/<feature>/assessment.md text the loop's post phase writes, so quality
    drift is visible alongside the eval trend. READ-ONLY; the feature path is
    canonicalized and CONFINED under <kdir>/work by the security kernel (reject
    `..`/symlink escapes, security-first) before any read. Missing assessment →
    exists:false, no error."""
    kdir = kdir_of(request)
    feature = urllib.parse.unquote(feature)
    if not kdir:
        return HTTPStatus.OK, dict(NO_KDIR, feature=feature, exists=False,
                                   text="", bytes=0, path=None)
    target = confine.confined_work_file(kdir, feature, ASSESSMENT_FILE)
    if target is None:
        return HTTPStatus.OK, {"feature": feature, "exists": False, "text": "",
                               "bytes": 0, "path": None,
                               "error": "invalid feature"}
    text = ""
    exists = os.path.isfile(target)
    if exists:
        try:
            with open(target, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            exists, text = False, ""
    return HTTPStatus.OK, {
        "feature": feature,
        "exists": exists,
        "text": text,
        "bytes": len(text.encode("utf-8")),
        "path": os.path.relpath(target, kdir) if exists else None,
    }
