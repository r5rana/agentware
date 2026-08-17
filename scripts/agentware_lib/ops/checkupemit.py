"""agentware_lib.ops.checkupemit -- turning findings into ACTION (layer: ops).

Owns: the half of the self-exam that can change the world -- the LLM narrative
input and call (`_checkup_llm_input` / `_checkup_llm_narrative`), candidate
selection (`_checkup_candidates`), the workorder emitter
(`_checkup_emit_workorder`), the refusal reason (`_checkup_reject_reason`) and the
idempotency signature (`_checkup_sig`).

WHY EMISSION IS SEPARATED FROM OBSERVATION
Every other checkup module is READ-ONLY: it looks and reports. This one writes a
workorder into the knowledge base, so it is the only place the exam can be WRONG in
a way that costs the operator something. Keeping it apart makes that boundary
reviewable — everything else can be trusted to be inert.

The SIGNATURE is the anti-duplicate: an exam that runs every cycle must not emit
the same workorder every cycle. `_checkup_sig` derives a stable identity from the
finding's content so a re-emit is recognised and skipped, and
`_checkup_reject_reason` records WHY a candidate was not emitted rather than
dropping it silently — a candidate that vanishes without explanation is
indistinguishable from a bug in the selector.

The LLM narrative is OPTIONAL and gated (`resolve_checkup_llm`): the exam produces
its findings deterministically, and the model only writes them up. That ordering
matters — a model failure degrades the prose, never the findings, and the report
still ships. Its output is UNTRUSTED (R-SEC-02) and goes through
features.reviewoutput before it can reach the report.

Does NOT own: the probes that produce findings (ops.checkupprobes,
ops.checkuphealth); thresholds (ops.checkupconfig); rendering and the CLI
(ops.checkupcmd); the agent spawn itself (features.reviewagent).
Layer deps: ops -> core + features (reviewagent, reviewoutput) + ops.planauthor
for the workorder writer. All DOWNWARD or same-layer.
Fan-in: 1 at landing (regen: python3 tests/_manifest_gen.py --write).

The bodies are a PURE MOVE from the monolith -- byte-identical, comments and all.
"""

import argparse
import hashlib
import json
import os
import re

from agentware_lib.core.dates import _now_iso
from agentware_lib.core.config import require_knowledge_dir, resolve_knowledge_dir
from agentware_lib.features.reviewagent import (
    REVIEW_AGENT_TIMEOUT,
    REVIEW_REVIEWER_PERSONA,
    _review_invoke_agent,
)
from agentware_lib.features.reviewoutput import _review_extract_json, _review_fence
from agentware_lib.core.capture import _dream_capture
from agentware_lib.knowledge.curation import _slugify
from agentware_lib.ops.planauthor.plankernelpath import _pref_hits_kernel
from agentware_lib.ops.planauthor.plannew import cmd_plan_new
from agentware_lib.ops.planauthor.planstate import STALLED_CLAIM_HOURS
from agentware_lib.ops.checkupconfig import (
    CHECKUP_REPORT_REL,
    resolve_checkup_llm,
)
from agentware_lib.ops.prefergate import _pref_loosens_gate


def _checkup_llm_input(agg):
    """A compact, numeric/enum/id-only summary of the deterministic aggregate for
    the LLM. Prefer values over inlined free text (untrusted-vs-trusted, Q4)."""
    fw = agg.get("flywheel", {})
    ph = agg.get("plan_health", {})
    wa = agg.get("watcher", {})
    lines = ["checkup deterministic aggregate (numeric/enum only):"]
    for k, v in sorted(fw.items()):
        lines.append("- flywheel.%s: %s" % (k, json.dumps(v, sort_keys=True)))
    lines.append("- plan_health: done=%s rotting=%s stalled=%s ready=%s"
                 % (ph.get("done"), ph.get("rotting"),
                    len(ph.get("stalled", [])), ph.get("ready")))
    lines.append("- watcher: fired_ok=%s missed_cycles=%s frozen=%d"
                 % (wa.get("fired_ok"), wa.get("missed_cycles"),
                    len(wa.get("frozen_metrics", []))))
    lines.append("- structure_triggers_fired: %d"
                 % sum(1 for t in agg.get("triggers", []) if t.get("fired")))
    return "\n".join(lines)


def _checkup_llm_narrative(agg, cfg):
    """DELEGATE the ONE bounded pass to the CLI-aware _review_invoke_agent seam
    (NOT a hand-rolled `claude -p` — a copy breaks under AGENTWARE_CLI=codex, audit
    L2-1), with the aggregate FENCED as untrusted data (R-SEC-02, audit L2-2). Parse
    stdout with _review_extract_json expecting ONE wrapping object
    {"narrative","observations"} (the parser returns the FIRST object, not a list,
    audit L1-03). Its output lands ONLY in the report; on failure/timeout the seam
    returns '' -> extract None -> the report OMITS the narrative. The LLM NEVER
    emits a workorder."""
    prompt = "\n\n".join([
        "You are a checkup meta-audit narrator for the agentware self-maintenance "
        "system. Read the DETERMINISTIC aggregate below and write a brief, sober "
        "narrative of what the trends mean plus a few concrete observations. You "
        "have NO authority to change anything — your output is REPORT-ONLY.",
        _review_fence("CHECKUP_AGGREGATE", _checkup_llm_input(agg)),
        "Return ONLY one JSON object: "
        "{\"narrative\": \"<2-4 sentences>\", "
        "\"observations\": [\"<short observation>\", ...]}.",
    ])
    try:
        # retries=0: this is the documented ONE bounded pass — a background
        # report-only job must degrade cheaply (omit the narrative) rather than
        # stall up to 3 deadlines + real backoff sleeps on a flaky runtime.
        # warn_tag='checkup': a degradation here is a CHECKUP event, never
        # mislabeled as an adversarial-review failure in captured logs.
        out = _review_invoke_agent("checkup", REVIEW_REVIEWER_PERSONA, prompt,
                                   cfg or {}, timeout=REVIEW_AGENT_TIMEOUT,
                                   retries=0, warn_tag="checkup")
    except Exception:
        return None
    obj = _review_extract_json(out)
    if not isinstance(obj, dict):
        return None
    narrative = obj.get("narrative")
    obs = obj.get("observations")
    if not isinstance(narrative, str):
        narrative = ""
    clean_obs = []
    if isinstance(obs, list):
        for o in obs:
            if isinstance(o, str) and o.strip():
                clean_obs.append(" ".join(o.split())[:300])
    narrative = " ".join(narrative.split())[:1200]
    if not narrative and not clean_obs:
        return None
    return {"narrative": narrative, "observations": clean_obs}


def _checkup_candidates(agg):
    """The CLOSED, checkup-authored DETERMINISTIC candidate set (Q5/Q6): stalled
    plan · frozen metric · fired structure trigger. LLM findings are NEVER here —
    the emitter's input set is closed by construction, so no LLM-authored proposal
    can become a workorder. Each candidate = {kind, fp, trigger, text}."""
    cands = []
    for s in agg.get("plan_health", {}).get("stalled", []):
        text = ("Stalled plan %s is running with a claim aged past the %dh "
                "threshold — investigate or cancel it. Plan title: %s"
                % (s["rel"], STALLED_CLAIM_HOURS, s.get("title") or "(untitled)"))
        cands.append({"kind": "stalled", "fp": "stalled:" + s["feature"],
                      "trigger": "checkup meta-audit: stalled plan "
                                 "(tier-2 deterministic candidate)", "text": text})
    for fm in agg.get("watcher", {}).get("frozen_metrics", []):
        text = ("Frozen metric %s held at %s across %d present dream cycles within "
                "tolerance (the 130-for-13-nights pathology) — the watcher runs but "
                "its number is not moving; investigate the underlying step."
                % (fm["metric"], fm["value"], fm["present_cycles"]))
        cands.append({"kind": "frozen", "fp": "frozen:" + fm["metric"],
                      "trigger": "checkup meta-audit: frozen headline metric "
                                 "(130-for-13-nights)", "text": text})
    for t in agg.get("triggers", []):
        if not (t.get("fired") and t.get("fires_workorder")):
            continue
        text = ("KB-structure trigger fired: %s value %s reached threshold %s — "
                "propose a structure migration (author a TESTED rollback per the "
                "safe-migration skill at execution time)."
                % (t["signal"], t.get("value"), t.get("threshold")))
        cands.append({"kind": "trigger", "fp": "trigger:" + t["signal"],
                      "trigger": "checkup meta-audit: KB-structure migration "
                                 "trigger (approval-gated)", "text": text})
    return cands


def _checkup_emit_workorder(kdir, cand):
    """Emit ONE tier-2 suggested workorder for a DETERMINISTIC candidate through
    the EXISTING sanitized `plan new --auto --context` emitter (never --target,
    never a Verify/executable line; candidate fenced in Context only) — a copy of
    _prefer_emit_workorder / _review_emit_low_workorder. `title=` is REQUIRED
    (cmd_plan_new reads args.title DIRECT, audit L1-04). Fingerprint-slugged for
    idempotence: an existing workorder for this candidate returns SUCCESS (no double
    emit). Returns (feature, None) or (None, reason) when the emitter REFUSES (g1/g3)
    so the caller DEFERS — deterministic candidates re-derive each cycle so a g3
    DEFER recurs next cycle (audit L1-05)."""
    import hashlib
    keyslug = (_slugify("%s %s" % (cand.get("kind", "checkup"),
                                   cand.get("text", "")))[:40]).strip("-") \
        or "checkup"
    h = hashlib.sha1((cand.get("fp") or cand.get("text") or "").encode("utf-8")
                     ).hexdigest()[:10]
    slug = "checkup-%s-%s" % (keyslug, h)
    existing = os.path.join(kdir, "work", "auto", slug, "plan.md")
    if os.path.isfile(existing):
        return "auto/" + slug, None      # idempotent no-op (already emitted)
    ns = argparse.Namespace(
        feature=slug, title="Review a checkup meta-audit finding",
        auto=True, self_extension=False, generated_by="checkup",
        trigger=cand["trigger"], evidence=CHECKUP_REPORT_REL,
        target=None, context=cand["text"], force=False,
        max_iterations=30, author=None, format="json")
    rc, out = _dream_capture(cmd_plan_new, ns)
    if rc != 0:
        return None, "emitter refused (rc=%d)" % rc
    try:
        feat = json.loads(out).get("feature") or ("auto/" + slug)
    except ValueError:
        feat = "auto/" + slug
    return feat, None


def _checkup_reject_reason(text):
    """Which tier-3 reject scan fires (for the report's Rejected section), or None
    when the candidate is clean. Mirrors _prefer_classify_tier's tier-3 predicate."""
    if _pref_hits_kernel(text):
        return "names a kernel path / the agentware package"
    if _pref_loosens_gate(text):
        return "reads as weakening a governance gate"
    return None


def _checkup_sig(d):
    """Render one flywheel signal dict as a stable one-liner."""
    if not isinstance(d, dict) or d.get("insufficient_data"):
        return "insufficient data"
    parts = []
    if "value" in d:
        parts.append("%s" % d["value"])
    if d.get("gauge"):
        tags = ["gauge"]
        if d.get("low_precision"):
            tags.append("low-precision")
        if d.get("lower_bound"):
            tags.append("lower bound")
        parts.append("(%s, n=%s)" % (", ".join(tags), d.get("sample_n")))
    elif d.get("direction"):
        parts.append("(%s, n=%s)" % (d["direction"], d.get("sample_n")))
    elif "direction" in d and d.get("direction") is None:
        parts.append("(no direction, n=%s)" % d.get("sample_n"))
    return " ".join(parts) if parts else "insufficient data"


_EXPORTS = [
    "_checkup_llm_input",
    "_checkup_llm_narrative",
    "_checkup_candidates",
    "_checkup_emit_workorder",
    "_checkup_reject_reason",
    "_checkup_sig",
]

__all__ = list(_EXPORTS)
