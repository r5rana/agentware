"""agentware_lib.features.dreamsteps.stepdetectreport -- dream step "e", detect-report: enumerate stale/duplicate learnings + unpromoted markers (layer: features).

Owns: dream step "e" (_dream_step_detect_report), the markdown renderer
(_dream_render_detect_report) and the three section renderers it decomposes into
(_dream_render_stale_section, _dream_render_conflicts_section,
_dream_render_markers_section), plus the best-effort artifact writer
(_dream_write_detect_report).
Does NOT own: the stale/conflict report builder -> ops.freshness; the worklog
marker scanner -> knowledge.worklog; the index loader -> knowledge.index; the
artifact PATH constant DREAM_REPORT_REL -> dreamsteps.dreamcapture; the ordered
registry that binds this handler to id "e" -> dreamsteps.registry. See
catalog/modules.json.
Layer deps: knowledge (index, worklog) + ops (freshness) -- all DOWNWARD -- plus
ONE dreamsteps SIBLING (dreamcapture, a stdlib-only leaf), so the package graph
stays acyclic.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
Determinism: DETECT-AND-REPORT only (INV-2) -- it never promotes, merges or
deletes. Every input is sorted before rendering and the body carries no clock,
run-id or host-specific value, so the artifact is byte-stable for unchanged
inputs (INV-1); the write is best-effort (an OSError never aborts the cycle) and
the fixed-name artifact is gitignored via logs/.
"""

import os
import time

import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.worklog as _worklog
import agentware_lib.ops.freshness as _freshness

# Bound BY NAME because the ported detect-report step and its renderer are the
# preserved monolith's bodies verbatim and call all of these bare. They are the
# WINNING versions: this tree's copies had never grown the plan-only or drift
# sections at all, so `## Plan-only work dirs` was absent from every report the
# modular core produced — a whole detection silently missing from a report whose
# entire job is to surface what nobody is looking at.
from agentware_lib.core.dates import _iso_to_epoch
from agentware_lib.knowledge.freshsignals import STALE_MAX_AGE_DAYS
from agentware_lib.knowledge.index import load_index
from agentware_lib.knowledge.worklog import scan_worklog
from agentware_lib.ops.checkupprobes import (
    AUTO_DRAFT_TTL_DAYS, OPERATOR_DRAFT_TTL_DAYS)
from agentware_lib.ops.freshness import build_stale_report
from agentware_lib.ops.plangraph import _read_plan_status
from agentware_lib.ops.planauthor.planstate import (
    STALLED_CLAIM_HOURS, _parse_claim)
from .dreamcapture import DREAM_REPORT_REL


def _dream_render_stale_section(report):
    """The '## Stale entries' block (trailing blank line included). Inputs are
    sorted here so the output is byte-stable for unchanged inputs (INV-1)."""
    stale = sorted(report.get("stale") or [],
                   key=lambda s: (s.get("id") or ""))
    lines = []
    lines.append("## Stale entries (%d)" % len(stale))
    lines.append("")
    lines.append("> last_verified older than %d day(s) in %s"
                 % (report.get("max_age_days", 0),
                    ", ".join(report.get("volatile_categories") or [])))
    lines.append("")
    if stale:
        for s in stale:
            lines.append("- %s [%s] last_verified=%s age=%dd  (%s)"
                         % (s.get("id"), s.get("category"),
                            s.get("last_verified"), s.get("age_days") or 0,
                            s.get("path")))
    else:
        lines.append("_none_")
    lines.append("")
    return lines


def _dream_render_conflicts_section(report):
    """The '## Possible duplicate / conflict pairs' block (trailing blank line
    included), sorted by pair for a byte-stable body (INV-1)."""
    conflicts = sorted(report.get("conflicts") or [],
                       key=lambda c: (c.get("a") or "", c.get("b") or ""))
    lines = []
    lines.append("## Possible duplicate / conflict pairs (%d)" % len(conflicts))
    lines.append("")
    if conflicts:
        for c in conflicts:
            lines.append("- %s <-> %s [%s] jaccard=%.4f"
                         % (c.get("a"), c.get("b"), c.get("category"),
                            c.get("jaccard") or 0.0))
    else:
        lines.append("_none_")
    lines.append("")
    return lines


def _dream_render_markers_section(markers):
    """The '## Unpromoted worklog markers' block, grouped by feature in sorted
    order with each feature's markers sorted by line (INV-1)."""
    lines = []
    lines.append("## Unpromoted worklog markers (%d)" % len(markers))
    lines.append("")
    if markers:
        by_feat = {}
        for m in markers:
            by_feat.setdefault(m["feature"], []).append(m)
        for feat in sorted(by_feat):
            items = sorted(by_feat[feat], key=lambda m: m["line"])
            lines.append("### %s (%d)" % (feat, len(items)))
            lines.append("")
            for m in items:
                lines.append("- %s:%d [%s] %s"
                             % (m["rel"], m["line"], m["kind"], m["text"]))
            lines.append("")
    else:
        lines.append("_none_")
        lines.append("")
    return lines


DREAM_PLAN_ONLY_MIN_AGE_HOURS = 48  # step e flags plan.md w/ no worklog.md older than this








def _dream_render_plan_only_section(plan_only):
    """`## Plan-only work dirs` — a planned feature that produced no worklog."""
    plan_only = sorted(plan_only or [], key=lambda p: (p.get("rel") or ""))
    out = ["## Plan-only work dirs (%d)" % len(plan_only), "",
           "> plan.md present but NO worklog.md, plan older than %dh "
           "(a planned feature that produced no worklog — the learning "
           "pipeline never ran for it)." % DREAM_PLAN_ONLY_MIN_AGE_HOURS, ""]
    for p in plan_only:
        out.append("- %s age=%sh" % (p.get("rel"), p.get("age_hours")))
    if not plan_only:
        out.append("_none_")
    out.append("")
    return out


def _dream_render_drift_section(drift):
    """`## Drift` (feature 260710): report-only; NO acting worker (P3)."""
    drift = sorted(drift or [], key=lambda d: (d.get("rel") or ""))
    out = ["## Drift — stalled / stale-draft (%d)" % len(drift), "",
           "> stalled = `running` claimed >%dh ago; stale-draft = `draft` "
           "idle >%dd (auto) / >%dd (operator). `cancelled` excluded. "
           "Report-only — no worker acts on these here."
           % (STALLED_CLAIM_HOURS, AUTO_DRAFT_TTL_DAYS,
              OPERATOR_DRAFT_TTL_DAYS), ""]
    for d in drift:
        out.append("- %s [%s] %s"
                   % (d.get("rel"), d.get("kind"), d.get("detail") or ""))
    if not drift:
        out.append("_none_")
    out.append("")
    return out


def _dream_render_detect_report(report, markers, plan_only=None, drift=None):
    """Render step e's findings into a deterministic, diff-friendly markdown
    report body. REPORT-ONLY (INV-2) — pure formatting, no mutation. Inputs are
    sorted per section so the output is byte-stable for unchanged inputs (INV-1):
    no clocks, no run-id, no host-specific values in the body. `plan_only`
    (optional, default None) lists plan.md-only work dirs; `drift` (optional)
    lists stalled/stale-draft plans; ages are passed IN as data (the live clock is
    read only by the caller) so this stays pure.

    One builder per SECTION: the section order IS the report's contract, and
    keeping it visible as a single list is what makes an added or dropped section
    a one-line, reviewable diff.
    """
    lines = [
        "# Dream Detect Report (latest)",
        "",
        "> Deterministic, report-only (INV-2) enumeration of what dream step e",
        "> detected. NEVER promotes, merges, or deletes — it only makes the",
        "> counts self-serve (which stale entry / which duplicate pair / which",
        "> unpromoted marker) with zero re-derivation. Machine-local; gitignored",
        "> via logs/. Overwritten each cycle; byte-stable for unchanged inputs.",
        "",
    ]
    lines += _dream_render_stale_section(report)
    lines += _dream_render_conflicts_section(report)
    lines += _dream_render_markers_section(markers)
    lines += _dream_render_plan_only_section(plan_only)
    lines += _dream_render_drift_section(drift)
    return "\n".join(lines).rstrip("\n") + "\n"


def _dream_write_detect_report(kdir, body):
    """Overwrite logs/dream-report-latest.md with step e's actionable report.
    Best-effort: an OSError is swallowed and never aborts the cycle. Returns the
    relative report path on success, else None. Overwrites (not appends) so the
    'latest' report is byte-stable for unchanged inputs (INV-1)."""
    try:
        path = os.path.join(kdir, DREAM_REPORT_REL)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return DREAM_REPORT_REL
    except OSError:
        return None


def _iter_feats(work_dir):
    """Feature dirs under work/, one level PLUS the auto/ lane.

    Lifted out of the step body unchanged (feature 260710): `auto` is a container,
    not a feature, so descend into work/auto/<slug>/. Deterministic ordering.
    """
    for name in sorted(os.listdir(work_dir)):
        d = os.path.join(work_dir, name)
        if not os.path.isdir(d):
            continue
        if name == "auto":
            try:
                for sub in sorted(os.listdir(d)):
                    sp = os.path.join(d, sub)
                    if os.path.isdir(sp):
                        yield "auto/" + sub, sp
            except OSError:
                pass
        else:
            yield name, d


def _detect_feature_markers(kdir, data, feat, wl):
    """Unpromoted markers in ONE feature's worklog, as report rows."""
    up, _tot = scan_worklog(wl, kdir, data)
    rel = "work/%s/worklog.md" % feat
    return [{"feature": feat, "rel": rel, "line": m["line"],
             "kind": m["kind"], "text": m["text"]} for m in up]


def _detect_plan_only(feat, plan, now):
    """A plan.md with NO worklog.md, once older than the min-age budget.

    Returns the report row or None. READ-ONLY; an unstattable plan contributes
    age 0.0 and is therefore never flagged, which keeps the check fail-quiet.
    """
    try:
        age_h = (now - os.path.getmtime(plan)) / 3600.0
    except OSError:
        age_h = 0.0
    if age_h < DREAM_PLAN_ONLY_MIN_AGE_HOURS:
        return None
    return {"feature": feat, "rel": "work/%s/plan.md" % feat,
            "age_hours": round(age_h, 1)}


def _detect_plan_drift(feat, plan, ptext, now):
    """Plan-state DRIFT for ONE feature: stalled `running`, stale `draft`.

    `cancelled` is excluded (legitimate cleanup). Ages are computed here from the
    live clock and handed to the pure renderer as DATA, never as a clock the
    renderer reads itself (INV-1). Returns the report row or None.
    """
    state, _cnt = _read_plan_status(ptext)
    rel_plan = "work/%s/plan.md" % feat
    is_auto = feat.startswith("auto/")
    if state == "running":
        claim = _parse_claim(ptext)
        cepoch = _iso_to_epoch(claim["ts"]) if claim else None
        if cepoch is None:
            return None
        age_h = (now - cepoch) / 3600.0
        if age_h < STALLED_CLAIM_HOURS:
            return None
        return {"feature": feat, "rel": rel_plan, "kind": "stalled",
                "detail": "claimed %.1fh ago (>%dh)" % (age_h, STALLED_CLAIM_HOURS)}
    if state == "draft":
        ttl_days = AUTO_DRAFT_TTL_DAYS if is_auto else OPERATOR_DRAFT_TTL_DAYS
        try:
            age_d = (now - os.path.getmtime(plan)) / 86400.0
        except OSError:
            age_d = 0.0
        if age_d <= ttl_days:
            return None
        return {"feature": feat, "rel": rel_plan, "kind": "stale-draft",
                "detail": "draft idle %.1fd (>%dd, %s lane)"
                          % (age_d, ttl_days, "auto" if is_auto else "operator")}
    return None


def _scan_feature(kdir, data, feat, fdir, now):
    """Every per-feature detection for ONE work dir, as one record.

    The three scans are independent but share the same two stat questions (does a
    worklog exist, does a plan exist), so they are answered once here rather than
    re-derived per detector. Returns lists so the caller is a pure accumulation.
    """
    out = {"scanned": 0, "markers": [], "plan_only": [], "drift": []}
    wl = os.path.join(fdir, "worklog.md")
    has_wl = os.path.isfile(wl)
    if has_wl:
        out["scanned"] = 1
        out["markers"] = _detect_feature_markers(kdir, data, feat, wl)
    plan = os.path.join(fdir, "plan.md")
    if not os.path.isfile(plan):
        return out
    if not has_wl:
        row = _detect_plan_only(feat, plan, now)
        if row:
            out["plan_only"].append(row)
    try:
        with open(plan, "r", encoding="utf-8") as f:
            ptext = f.read()
    except OSError:
        ptext = ""
    row = _detect_plan_drift(feat, plan, ptext, now)
    if row:
        out["drift"].append(row)
    return out


def _dream_step_detect_report(kdir, dry_run):
    """Step e — DETECT-AND-REPORT only (Phase 1): stale/duplicate learnings +
    unpromoted markers across work/*/worklog.md. READ-ONLY (INV-2): never
    promotes, deletes, or merges (that is Phase 2). Also writes an actionable,
    deterministic logs/dream-report-latest.md enumerating EACH finding so the
    morning-after 'which 1 duplicate / which N markers?' is self-serve.

    The three per-feature scans (markers, plan-only, drift) are independent and
    each lives in its own detector; this body is the walk plus the tally. The
    `dreamsteps` package carries NO ceiling debt by construction (task 21), so a
    ported body over the 12-branch cap is decomposed rather than declared.
    """
    if dry_run:
        return {"status": "planned",
                "would": "audit --stale + worklog scan + write "
                         "dream-report-latest.md (REPORT only, no mutation)"}
    data, err = load_index(kdir)
    if err is not None:
        return {"status": "error", "errors": [err]}
    report = build_stale_report(kdir, data, STALE_MAX_AGE_DAYS)
    markers, plan_only, drift, scanned = [], [], [], 0
    now = time.time()
    work = os.path.join(kdir, "work")
    if os.path.isdir(work):
        for feat, fdir in _iter_feats(work):
            found = _scan_feature(kdir, data, feat, fdir, now)
            scanned += found["scanned"]
            markers.extend(found["markers"])
            plan_only.extend(found["plan_only"])
            drift.extend(found["drift"])
    body = _dream_render_detect_report(report, markers, plan_only, drift)
    report_rel = _dream_write_detect_report(kdir, body)
    result = {"status": "ok", "stale": len(report["stale"]),
              "duplicates": len(report["conflicts"]),
              "worklogs_scanned": scanned, "unpromoted_markers": len(markers),
              "plan_only_dirs": len(plan_only),
              "stalled": sum(1 for d in drift if d["kind"] == "stalled"),
              "stale_draft": sum(1 for d in drift if d["kind"] == "stale-draft")}
    if report_rel:
        result["report"] = report_rel
    return result


_EXPORTS = [
    "_dream_render_stale_section",
    "_dream_render_conflicts_section",
    "_dream_render_markers_section",
    "_dream_render_detect_report",
    "_dream_write_detect_report",
    "_dream_step_detect_report",
]
