"""agentware_lib.ops.reviewreport -- the review REPORT, the worklog callout and the `review-report` verb (layer: ops).

Owns: the report location (`REVIEW_REPORT_REL`) and workorder provenance tag
(`REVIEW_WORKORDER_GENERATED_BY`), the finding presenters
(`_review_finding_locator` / `_review_finding_oneline` /
`_review_report_model_effort`), the renderer (`_review_render_report`), the
cross-round finding algebra (`_review_finding_key` / `_review_collect_findings` /
`_review_material_findings` / `_review_low_findings`), the worklog callout
(`_review_callout_marker` / `_review_append_worklog_callout`), the low-severity
workorder emitter (`_review_emit_low_workorder`), the artifact readers
(`_review_read_json_file` / `_review_load_round_artifacts`) and the
`review-report` handler + registrar (`cmd_review_report` / `register_review_report`).
Does NOT own: the vocabulary -> ops.reviewschema; the config axes ->
ops.reviewconfig; the audit that produces rounds -> ops.reviewaudit; remediation
-> ops.reviewremediate; the workorder emitter it calls ->
ops.planauthor.plannew.

MATERIAL AND LOW ARE DIFFERENT OUTCOMES, DELIBERATELY
-----------------------------------------------------
`_review_material_findings` and `_review_low_findings` split the surviving
findings by whether they BLOCK. The split exists so that low-severity findings
are neither ignored nor allowed to block a release: material findings gate, low
findings become a tier-2 PROPOSED workorder (`_review_emit_low_workorder`) that a
human triages later. Collapsing the two would force a choice between a gate that
cries wolf and a gate that silently drops real findings, and both of those end
the same way -- with the gate turned off.

DEDUP IS ACROSS ROUNDS, KEYED ON IDENTITY
-----------------------------------------
`_review_finding_key` keys on the finding's identity, not its prose, so the same
defect surfaced by two dimensions or re-surfaced in a later round counts ONCE.
Without that, a multi-round audit inflates its own finding count every round and
the report stops meaning anything.

THE REPORT IS THE ARTIFACT A HUMAN ACTUALLY READS
-------------------------------------------------
It records the model and effort each round ran at (`_review_report_model_effort`)
alongside the findings, because a clean report from a downgraded reviewer and a
clean report from a pinned one are not the same evidence, and six months later
the report is all that is left of the distinction.

Layer deps: ops -> ops (`reviewschema`, `reviewconfig`,
`planauthor.plannew.cmd_plan_new` for the low-finding workorder,
`planauthor.planedit._atomic_write` for the report write), ops -> core (`config`,
`paths`), ops -> knowledge (`curation.curationrender._slugify`), and ops ->
features (`reviewoutput._review_one_line`,
`dreamsteps.dreamcapture._dream_capture` -- the capture harness the workorder
emitter runs `cmd_plan_new` through, the same one `staleproposal` uses). Findings text reaching a workorder is UNTRUSTED and rides
the fenced `--context` body only (R-SEC-02), never a target or verify line.
Determinism: rendering is pure given the rounds; the report path is
knowledge-dir-relative so two runs write the same file rather than accumulating.
"""

import argparse
import json
import os
import re
import sys

import agentware_lib.core.config as config
from agentware_lib.core.config import require_knowledge_dir
from agentware_lib.core.paths import REPO_ROOT
from agentware_lib.features.dreamsteps.dreamcapture import _dream_capture
from agentware_lib.features.reviewoutput import _review_one_line
from agentware_lib.knowledge.curation.curationrender import _slugify
from agentware_lib.ops.planauthor.planedit import _atomic_write
from agentware_lib.ops.planauthor.plannew import cmd_plan_new
from agentware_lib.ops.reviewconfig import (
    REVIEW_DEFAULT_EFFORT,
    REVIEW_DEFAULT_MODEL,
)
from agentware_lib.ops.reviewschema import (
    REVIEW_BLOCKING_SEVERITIES,
    _review_is_blocking,
    _review_normalize_finding,
)


REVIEW_REPORT_REL = "adversarial-review.md"


REVIEW_WORKORDER_GENERATED_BY = "adversarial-review"


def _review_finding_locator(f):
    """`file:line` (or just `file`) for a finding, defaulting unknowns to `?`."""
    loc = _review_one_line(f.get("file", ""), 200) or "?"
    line = f.get("line")
    if line not in (None, "", "?"):
        loc = "%s:%s" % (loc, _review_one_line(line, 20))
    return loc


def _review_finding_oneline(f):
    """One-line `[severity/verdict] file:line - summary (dimension)` label."""
    return "[%s/%s] %s - %s (%s)" % (
        _review_one_line(f.get("severity", "?"), 20) or "?",
        _review_one_line(f.get("verdict", "?"), 20) or "?",
        _review_finding_locator(f),
        _review_one_line(f.get("summary", ""), 200) or "(no summary)",
        _review_one_line(f.get("dimension", "?"), 60) or "?")


def _review_report_model_effort(rounds, cfg=None):
    """Resolve (model, effort) for the report header from cfg, else the first
    round's audit, else the pinned opus/max defaults."""
    model = (cfg or {}).get("model")
    effort = (cfg or {}).get("effort")
    if (not model or not effort) and rounds:
        a0 = rounds[0].get("audit") or {}
        model = model or a0.get("model")
        effort = effort or a0.get("effort")
    return (model or REVIEW_DEFAULT_MODEL, effort or REVIEW_DEFAULT_EFFORT)


def _review_render_report(rounds, cfg=None, feature=None, outcome=None):
    """Render the durable `adversarial-review.md` call-out report for a full gate
    run: header (model/effort/rounds/outcome) + per round the dimensions audited,
    findings + verdicts, completeness-critic gaps/additions, and the remediation
    (fixes + regression tests + gate chain). Deterministic (no clocks); every
    untrusted string is collapsed to a single line."""
    rounds = rounds or []
    model, effort = _review_report_model_effort(rounds, cfg)
    lines = [
        "# Adversarial review — %s" % (
            _review_one_line(feature, 120) or "self-extension diff"),
        "",
        "> Auto-generated by the self-healing adversarial-review gate "
        "(`scripts/agentware review` / `remediate`). READ-ONLY reviewers on "
        "**%s** at **%s** effort constructed hostile inputs, tried to REFUTE each "
        "claim, and a completeness critic named any un-run dimension; every "
        "confirmed high/medium was auto-fixed with a regression test and "
        "RE-AUDITED by a FRESH fan-out." % (model, effort),
        "",
        "- model: %s" % model,
        "- effort: %s" % effort,
        "- rounds: %d" % len(rounds),
        "- outcome: %s" % (_review_one_line(outcome, 80) or "-"),
        "",
    ]
    if not rounds:
        lines.append("_No audit rounds ran (no self-extension diff, or the gate "
                     "was skipped)._")
        return "\n".join(lines).rstrip("\n") + "\n"
    for r in rounds:
        audit = r.get("audit") or {}
        rem = r.get("remediation")
        lines.append("## Round %s" % r.get("round", "?"))
        lines.append("")
        dims = ", ".join(str(d) for d in (audit.get("dimensions") or [])) or "-"
        lines.append("- dimensions audited: %s" % dims)
        lines.append("- confirmed blocking (high/med): %s"
                     % audit.get("confirmed_blocking", 0))
        lines.append("- malformed reviewer payloads: %s"
                     % audit.get("malformed", 0))
        lines.append("")
        findings = [f for f in (audit.get("findings") or [])
                    if isinstance(f, dict)]
        lines.append("### Findings (%d)" % len(findings))
        lines.append("")
        if findings:
            for f in findings:
                lines.append("- %s" % _review_finding_oneline(f))
                fs = _review_one_line(f.get("failure_scenario", ""), 300)
                if fs:
                    lines.append("  - failure: %s" % fs)
        else:
            lines.append("_none_")
        lines.append("")
        critic = audit.get("critic") or {}
        gaps = [g for g in (critic.get("gaps") or [])]
        addl = [f for f in (critic.get("additional_findings") or [])
                if isinstance(f, dict)]
        lines.append("### Completeness critic")
        lines.append("")
        lines.append("- gaps named: %d" % len(gaps))
        for g in gaps:
            lines.append("  - %s" % (_review_one_line(g, 200) or "(empty)"))
        lines.append("- additional findings (surfaced uncertain): %d" % len(addl))
        for f in addl:
            lines.append("  - %s" % _review_finding_oneline(f))
        lines.append("")
        if rem:
            fixes = [x for x in (rem.get("fixes") or []) if isinstance(x, dict)]
            lines.append("### Remediation")
            lines.append("")
            lines.append("- confirmed requested: %s" % rem.get("requested", 0))
            lines.append("- all fixed: %s" % rem.get("all_fixed", False))
            gc = rem.get("gate_chain")
            if gc:
                steps = ", ".join(
                    "%s=%s" % (s.get("name"), s.get("ok"))
                    for s in (gc.get("steps") or []) if isinstance(s, dict))
                lines.append("- gate chain ok: %s (%s)" % (gc.get("ok"), steps))
            for fx in fixes:
                lines.append("  - #%s [%s] %s — reg-test: %s" % (
                    fx.get("index"),
                    _review_one_line(fx.get("status", "?"), 20) or "?",
                    _review_one_line(fx.get("finding_summary", ""), 160)
                    or "(no summary)",
                    _review_one_line(fx.get("regression_test", ""), 120) or "-"))
                nt = _review_one_line(fx.get("notes", ""), 200)
                if nt:
                    lines.append("    notes: %s" % nt)
            lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def _review_finding_key(f):
    """Dedup key for a finding across rounds: (file, line, lowercased summary)."""
    return (_review_one_line(f.get("file", ""), 200),
            _review_one_line(f.get("line", ""), 20),
            _review_one_line(f.get("summary", ""), 120).lower())


def _review_collect_findings(rounds):
    """Flatten every round's reviewer findings + critic additions, deduped by
    `_review_finding_key`. A later round's copy WINS (a fresh re-audit verdict is
    the current truth)."""
    seen, order = {}, []
    for r in (rounds or []):
        audit = r.get("audit") or {}
        pool = [f for f in (audit.get("findings") or []) if isinstance(f, dict)]
        pool += [f for f in ((audit.get("critic") or {}).get(
            "additional_findings") or []) if isinstance(f, dict)]
        for f in pool:
            k = _review_finding_key(f)
            if k not in seen:
                order.append(k)
            seen[k] = f
    return [seen[k] for k in order]


def _review_material_findings(rounds):
    """The CALL-OUT set for the worklog: confirmed high/medium findings
    (`_review_is_blocking`) across all rounds, deduped."""
    return [f for f in _review_collect_findings(rounds) if _review_is_blocking(f)]


def _review_low_findings(rounds):
    """The follow-up-workorder set: LOW-severity findings NOT auto-fixed
    (remediation only touches high/med) and not REFUTED, deduped."""
    out = []
    for f in _review_collect_findings(rounds):
        sev = _review_one_line(f.get("severity", ""), 20).lower()
        verdict = _review_one_line(f.get("verdict", ""), 20).lower()
        if sev == "low" and verdict != "refuted":
            out.append(f)
    return out


def _review_callout_marker(finding):
    """Build a SELF-CLOSING `> DECISION:` + `> WAIVED:` call-out for one material
    finding. Self-closing so surfacing the finding does NOT perpetually wedge the
    loop's promise gate (R-SI-03 / R-AUTO-05) — the durable record is the auto-fix
    plus its regression test (mirroring the task-3 skip precedent). R-SEC-02: all
    finding text is collapsed to a single line so it can never forge a second
    marker."""
    fs = _review_one_line(finding.get("failure_scenario", ""), 260)
    decision = _review_one_line(
        "adversarial-review CALL-OUT %s%s" % (
            _review_finding_oneline(finding),
            ("; failure: " + fs) if fs else ""), 480)
    return ("> DECISION: %s\n> WAIVED: called out for audit; the auto-fix + its "
            "regression test are the durable record (see %s)\n"
            % (decision, REVIEW_REPORT_REL))


def _review_append_worklog_callout(worklog_path, findings, stream=None):
    """Append one self-closing call-out marker per MATERIAL finding to the feature
    worklog. Returns the number written (0 if no path / no findings). Best-effort:
    an OSError is reported to `stream`, never raised (a call-out failure must never
    crash the gate)."""
    stream = stream if stream is not None else sys.stderr
    findings = findings or []
    if not worklog_path or not findings:
        return 0
    blocks = [_review_callout_marker(f) for f in findings]
    try:
        with open(worklog_path, "a", encoding="utf-8") as fh:
            fh.write("\n" + "\n".join(blocks))
    except OSError as exc:
        stream.write("⚠️  WARN [adversarial-review]: could not write "
                     "worklog call-out to %s: %s\n" % (worklog_path, exc))
        return 0
    return len(blocks)


def _review_emit_low_workorder(kdir, finding, report_rel=None):
    """File a follow-up workorder for a LOW finding not auto-fixed, through the
    EXISTING `plan new --auto` emitter (never a parallel emitter). The finding's
    DESCRIPTIVE text (summary + failure scenario + dimension) is quoted via the
    SANITIZED `--context` seam (g1 kernel refusal + `_sanitize_context_block`
    fence, Context section ONLY — never a `--target`, never a Verify/executable
    line; R-SEC-02). Returns (feature, None) on success, or (None, reason) when the
    emitter REFUSES (candidate names a kernel path -> g1, or the g3 draft cap) so
    the caller DEFERS the finding (still called out in the report). The slug embeds
    a fingerprint of the finding so re-filing the SAME finding is a stable no-op."""
    import hashlib
    if not kdir:
        return None, "no knowledge dir"
    summary = _review_one_line(finding.get("summary", ""), 200)
    fs = _review_one_line(finding.get("failure_scenario", ""), 400)
    dim = _review_one_line(finding.get("dimension", ""), 60)
    context = summary or "(low-severity adversarial-review finding)"
    if fs:
        context += "\n\nFailure scenario: " + fs
    if dim:
        context += "\n\nDimension: " + dim
    keyslug = (_slugify(summary or "review-low")[:40]).strip("-") or "review-low"
    h = hashlib.sha1(repr(_review_finding_key(finding)).encode("utf-8")
                     ).hexdigest()[:10]
    slug = "review-low-%s-%s" % (keyslug, h)
    ns = argparse.Namespace(
        feature=slug,
        title="Follow up on a low-severity adversarial-review finding",
        auto=True, self_extension=False,
        generated_by=REVIEW_WORKORDER_GENERATED_BY,
        trigger=("low-severity finding from the self-healing adversarial-review "
                 "gate (not auto-fixed; filed for operator review)"),
        evidence=(report_rel or REVIEW_REPORT_REL),
        target=None, context=context, force=False,
        max_iterations=30, author=None, format="json")
    rc, out = _dream_capture(cmd_plan_new, ns)
    if rc != 0:
        return None, "emitter refused (rc=%d)" % rc
    try:
        feat = json.loads(out).get("feature") or ("auto/" + slug)
    except ValueError:
        feat = "auto/" + slug
    return feat, None


def _review_read_json_file(path):
    """Load a JSON file, tolerant of a missing/garbled artifact (None, never
    raises) — round artifacts are agent-produced and may be absent or malformed."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _review_load_round_artifacts(state_dir):
    """Load the per-round audit + remediation JSON the loop wrote under STATE_DIR
    (`adversarial-review-round-N.json` / `adversarial-remediate-round-N.json`),
    sorted by round number. Tolerant: a missing/garbled file is skipped."""
    rounds = []
    if not state_dir or not os.path.isdir(state_dir):
        return rounds
    import glob

    def _rn(p):
        m = re.search(r"round-(\d+)\.json$", p)
        return int(m.group(1)) if m else 0

    pat = os.path.join(state_dir, "adversarial-review-round-*.json")
    for apath in sorted(glob.glob(pat), key=_rn):
        n = _rn(apath)
        audit = _review_read_json_file(apath)
        if audit is None:
            continue
        rem = _review_read_json_file(os.path.join(
            state_dir, "adversarial-remediate-round-%d.json" % n))
        rounds.append({"round": n, "audit": audit, "remediation": rem})
    return rounds


def cmd_review_report(args):
    """`review-report`: consolidate the gate's per-round audit + remediation JSON
    into `<feature>/adversarial-review.md`, append a self-closing worklog call-out
    per MATERIAL (confirmed high/med) finding, and file a follow-up workorder for
    each LOW finding not auto-fixed (SANITIZED `--context` seam). Best-effort +
    deterministic; ALWAYS returns 0 (a call-out surface must never itself fail the
    gate). R-SEC-02: finding text is only ever fenced/collapsed — never a
    `--target` or a Verify line."""
    rounds = _review_load_round_artifacts(getattr(args, "state_dir", None))
    report = _review_render_report(
        rounds, feature=getattr(args, "feature", None),
        outcome=getattr(args, "outcome", None))
    written = None
    feature_dir = getattr(args, "feature_dir", None)
    if feature_dir:
        try:
            os.makedirs(feature_dir, exist_ok=True)
            path = os.path.join(feature_dir, REVIEW_REPORT_REL)
            _atomic_write(path, report)
            written = path
        except OSError as exc:
            sys.stderr.write("review-report: could not write report: %s\n" % exc)
    material = _review_material_findings(rounds)
    n_callout = _review_append_worklog_callout(
        getattr(args, "worklog", None), material)
    lows = _review_low_findings(rounds)
    emitted, deferred = [], []
    if lows and not getattr(args, "no_workorders", False):
        kdir = require_knowledge_dir()
        for f in lows:
            feat, reason = (_review_emit_low_workorder(kdir, f)
                            if kdir else (None, "no knowledge dir"))
            if feat:
                emitted.append(feat)
            else:
                deferred.append(reason)
    result = {"report": written, "rounds": len(rounds),
              "material_callouts": n_callout, "low_findings": len(lows),
              "workorders_emitted": emitted, "workorders_deferred": deferred}
    if getattr(args, "format", "text") == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(
            "review-report: rounds=%d report=%s callouts=%d low=%d "
            "workorders=%d deferred=%d\n" % (
                len(rounds), written or "-", n_callout, len(lows),
                len(emitted), len(deferred)))
    return 0


def register_review_report(sub, fmt_parent):
    # review-report ----------------------------------------------------------
    p_review_report = sub.add_parser(
        "review-report", parents=[fmt_parent],
        help="consolidate the gate's per-round audit + remediation JSON into "
             "<feature>/adversarial-review.md, append a self-closing worklog "
             "call-out per material finding, and file a workorder per low finding")
    p_review_report.add_argument(
        "--state-dir", dest="state_dir",
        help="dir holding the per-round adversarial-review-round-N.json / "
             "adversarial-remediate-round-N.json artifacts the loop wrote")
    p_review_report.add_argument(
        "--feature-dir", dest="feature_dir",
        help="feature work dir the adversarial-review.md report is written into")
    p_review_report.add_argument(
        "--worklog", dest="worklog",
        help="feature worklog.md the self-closing call-out markers append to")
    p_review_report.add_argument(
        "--feature", dest="feature", help="feature name for the report header")
    p_review_report.add_argument(
        "--outcome", dest="outcome",
        help="final gate outcome for the report header (e.g. clean / blocked)")
    p_review_report.add_argument(
        "--no-workorders", dest="no_workorders", action="store_true",
        help="do not emit follow-up workorders for low findings")
    p_review_report.set_defaults(func=cmd_review_report)


_EXPORTS = [
    "REVIEW_REPORT_REL",
    "REVIEW_WORKORDER_GENERATED_BY",
    "_review_finding_locator",
    "_review_finding_oneline",
    "_review_report_model_effort",
    "_review_render_report",
    "_review_finding_key",
    "_review_collect_findings",
    "_review_material_findings",
    "_review_low_findings",
    "_review_callout_marker",
    "_review_append_worklog_callout",
    "_review_emit_low_workorder",
    "_review_read_json_file",
    "_review_load_round_artifacts",
    "cmd_review_report",
]

__all__ = list(_EXPORTS)
