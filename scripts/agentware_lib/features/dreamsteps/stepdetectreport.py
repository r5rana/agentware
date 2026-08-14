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

import agentware_lib.knowledge.index as index
import agentware_lib.knowledge.worklog as _worklog
import agentware_lib.ops.freshness as _freshness

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


def _dream_render_detect_report(report, markers):
    """Render step e's findings into a deterministic, diff-friendly markdown
    report body. REPORT-ONLY (INV-2) — pure formatting, no mutation. Inputs are
    sorted here so the output is byte-stable for unchanged inputs (INV-1): no
    clocks, no run-id, no host-specific values in the body."""
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
    lines.extend(_dream_render_stale_section(report))
    lines.extend(_dream_render_conflicts_section(report))
    lines.extend(_dream_render_markers_section(markers))
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


def _dream_step_detect_report(kdir, dry_run):
    """Step e — DETECT-AND-REPORT only (Phase 1): stale/duplicate learnings +
    unpromoted markers across work/*/worklog.md. READ-ONLY (INV-2): never
    promotes, deletes, or merges (that is Phase 2). Also writes an actionable,
    deterministic logs/dream-report-latest.md enumerating EACH finding so the
    morning-after 'which 1 duplicate / which N markers?' is self-serve."""
    if dry_run:
        return {"status": "planned",
                "would": "audit --stale + worklog scan + write "
                         "dream-report-latest.md (REPORT only, no mutation)"}
    data, err = index.load_index(kdir)
    if err is not None:
        return {"status": "error", "errors": [err]}
    report = _freshness.build_stale_report(kdir, data, 120)
    markers, scanned = [], 0
    work = os.path.join(kdir, "work")
    if os.path.isdir(work):
        for feat in sorted(os.listdir(work)):
            wl = os.path.join(work, feat, "worklog.md")
            if os.path.isfile(wl):
                scanned += 1
                up, _tot = _worklog.scan_worklog(wl, kdir, data)
                rel = os.path.join("work", feat, "worklog.md")
                for m in up:
                    markers.append({"feature": feat, "rel": rel,
                                    "line": m["line"], "kind": m["kind"],
                                    "text": m["text"]})
    body = _dream_render_detect_report(report, markers)
    report_rel = _dream_write_detect_report(kdir, body)
    result = {"status": "ok", "stale": len(report["stale"]),
              "duplicates": len(report["conflicts"]),
              "worklogs_scanned": scanned, "unpromoted_markers": len(markers)}
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
