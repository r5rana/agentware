"""agentware_lib.ops.plangovern -- plan lint R12..R16, the GOVERNANCE rules (layer: ops).

Owns: the four plan-lint rules that need the plan's PLACE IN THE KNOWLEDGE BASE
rather than only its text -- its KB-relative path, the KB's schema generation and
the knowledge dir -- together with the two ramp thresholds they key on.

  R12  _lint_workorder  a plan under `work/auto/` carries `> Origin:` /
                        `> Generated-by:` / `> Trigger:` provenance, and its slug
                        follows the YYMMDD- convention (`_WORKORDER_SLUG_RE`)
  R13  _lint_status     the declared `> Status:` is in vocab, appears exactly
                        once, and `done` agrees with derived task reality
  R14  _lint_premises   a LOAD-BEARING plan carries a well-formed CITED
                        `## Premises` block (the R-GND-01 structural half)
  R15  _lint_graph      `## Depends-on:` closed grammar -- no malformed row, no
                        self-edge, no duplicate predecessor
  R16  _lint_graph      `> Checkpoint:` in vocab, and non-`none` for a
                        self-extension plan (`_PLAN_GRAPH_ERROR_SCHEMA`)

WHY THESE FOUR ARE A MODULE, AND WHY THEY ALL RAMP
--------------------------------------------------
R1..R11 are decidable from the plan text alone, so they are errors from the
moment they land. These four are not: R12 must know the plan is a workorder, R13
compares a declaration against task reality, R14 must scan the live index to
decide whether a plan is load-bearing, and R15/R16 read blocks that did not exist
when most plans in a knowledge base were written. Hard-failing them on arrival
would retroactively break every existing plan -- so each rides a WARN->ERROR
VERSIONED RAMP keyed on the KB's own schema generation, never on a config flag: a
knowledge base opts in by MIGRATING, which is an auditable, journaled event, and
the ramp thresholds live here beside the rules that consult them.

The severity choice is expressed as a BUCKET, never a flag: a rule picks `errors`
or `warnings` and appends through the one shared `_lint_add`, so a rule can never
invent a severity the emitter does not render.

Does NOT own: the pure-format rules R1..R11, the orchestrator that sequences all
sixteen, the readiness gate or the CLI handler -> ops.planlint, which imports this
module downward. The BLOCK PARSERS these rules consult (`> Status:`,
`## Depends-on:`, `> Checkpoint:`) -> ops.plangraph; the `## Premises` trust
boundary -> ops.planpremises; the task-marker grammar -> ops.planmarkers. Nothing
here parses: every rule asks a leaf for the parsed block and then JUDGES it, which
is what keeps "a plan declaration is never executed" provable in planpremises
alone.

Layer deps: ops.plangovern -> ops.planmarkers, ops.plangraph, ops.planpremises
(same-layer, all one-way; nothing imports back). No core, no knowledge, no
features. The only KB access is R14's load-bearing test, which reads the index
through ops.planpremises.

Determinism: pure judgement over already-parsed blocks plus that one read-only
index lookup; stdlib `re` only. Nothing here writes (INV-2, INV-6).

_EXPORTS is re-exported by ops.planlint so every `CLI.<name>` oracle and the
resident plan callers resolve the SAME objects.
Fan-in: 1 (modules that import this; see catalog/modules.json depended_on_by; regen: python3 tests/_manifest_gen.py --write).
"""

import re

from agentware_lib.ops.plangraph import (
    _CHECKPOINT_POLICIES,
    _PLAN_EDGE_MALFORMED,
    _PLAN_STATES,
    _read_plan_status,
    plan_checkpoint,
    plan_depends_on,
)
from agentware_lib.ops.planmarkers import _lint_add, _plan_open_marker_count
from agentware_lib.ops.planpremises import (
    _PLAN_PREMISES_ERROR_SCHEMA,
    _plan_is_load_bearing,
    _plan_is_self_extension,
    plan_premises,
)


_WORKORDER_SLUG_RE = re.compile(r"^\d{6}-")  # YYMMDD- (lint WARN otherwise)

# R15/R16 WARN->ERROR versioned ramp (mirrors R12/R14). The rules stay WARN under
# the current KB schema so the strict pre-hook never retroactively hard-fails an
# existing self-extension plan lacking `## Depends-on:`/`> Checkpoint:` headers;
# the HARD enforcement of these invariants lives in the graph MANIFEST validator
# (graph_manifest_errors, feature 260722 Task 2), which refuses outright.
_PLAN_GRAPH_ERROR_SCHEMA = 2


def _lint_workorder(rel_path, header_text, schema_version, errors, warnings):
    """R12 — workorders (plans under work/auto/) MUST carry provenance headers.

    Severity: WARN while the KB schema < 1, ERROR once schema >= 1 (the
    deterministic, versioned flip — no config flag). The slug convention is a
    separate, always-WARN finding: a badly-named workorder is untidy, not unsafe.
    A plan outside `work/auto/`, or one whose path could not be resolved inside
    the KB at all, is a no-op — R12 never guesses at provenance it cannot place.
    """
    if not (rel_path and rel_path.startswith("work/auto/")):
        return
    bucket = errors if schema_version >= 1 else warnings
    missing = [h for h in ("Origin", "Generated-by", "Trigger")
               if not re.search(r"(?m)^>\s*%s:\s*\S" % h, header_text)]
    if missing:
        _lint_add(bucket, "R12", 1,
                  "workorder under work/auto/ is missing provenance header(s): "
                  "%s (emit via `plan new --auto --generated-by … --trigger …`)"
                  % ", ".join("> %s:" % m for m in missing))
    # Slug SHOULD be YYMMDD- (WARN only).
    parts = rel_path.split("/")
    slug = parts[2] if len(parts) > 2 else ""
    if slug and not _WORKORDER_SLUG_RE.match(slug):
        _lint_add(warnings, "R12", 1,
                  "workorder slug %r does not match the YYMMDD- convention"
                  % slug)


def _lint_status(text, strict, errors, warnings):
    """R13 — status-reality binding.

    The declared `> Status:` must be one of the six states, must appear exactly
    once (header parsing is FIRST-MATCH, so a duplicate is a hand-edit that
    silently changes nothing and must be named rather than tolerated), and `done`
    must agree with derived task reality: a done plan has every task ✅.
    """
    state, status_count = _read_plan_status(text)
    if status_count > 1:
        _lint_add(errors, "R13", 1,
                  "plan carries %d `> Status:` header lines (duplicate/"
                  "hand-edited) — exactly one is allowed; header parsing is "
                  "first-match" % status_count)
    elif status_count == 1 and state not in _PLAN_STATES:
        _lint_add(errors, "R13", 1,
                  "unrecognized `> Status: %s` — one of: %s"
                  % (state, ", ".join(_PLAN_STATES)))
    if status_count >= 1 and state == "done":
        open_n = _plan_open_marker_count(text)
        if open_n > 0:
            bucket = errors if strict else warnings
            _lint_add(bucket, "R13", 1,
                      "declared `> Status: done` but %d open task marker(s) "
                      "remain (a done plan must have every task ✅)" % open_n)


def _lint_premises(text, strict, schema_version, kdir, errors, warnings):
    """R14 — a LOAD-BEARING plan MUST carry a well-formed, CITED `## Premises`.

    Load-bearing = self-extension OR names a live KB id (D6). STRUCTURAL only: it
    checks that premise rows exist and parse into the closed grammar, NOT whether
    they reproduce (that is `plan verify-premises` at run time). Severity: WARN by
    default; ERROR under --strict once schema_version >= the ramp threshold (a
    WARN->ERROR versioned ramp mirroring R12 — so the strict pre-hook never
    retroactively hard-fails existing self-extension plans until the KB schema
    advances). A trivial (non-load-bearing) plan is a no-op.
    """
    if not _plan_is_load_bearing(text, kdir):
        return
    prem = plan_premises(text)
    cited = [p for p in prem if p.get("cite") and p.get("check")]
    ramp = strict and schema_version >= _PLAN_PREMISES_ERROR_SCHEMA
    bucket = errors if ramp else warnings
    if not cited:
        _lint_add(bucket, "R14", 1,
                  "load-bearing plan (self-extension or cites a live KB id) has "
                  "no well-formed cited `## Premises` block — declare "
                  "`premise:`/`cite:`/`check:` rows so `plan verify-premises` "
                  "can re-check each premise against live state before execution "
                  "(R-GND-01/R-FAIL-08)")
        return
    bad = next((p for p in prem
                if str((p.get("parsed") or {}).get("kind", "")).startswith("__")),
               None)
    if bad is not None:
        _lint_add(bucket, "R14", bad.get("line", 1),
                  "premise check is not a valid closed-grammar kind: %s"
                  % ((bad.get("parsed") or {}).get("error") or "malformed"))


def _plan_own_slug(rel_path):
    """The plan's own graph slug: work/<feat>/plan.md or work/auto/<feat>/plan.md
    -> <feat>. None when the plan is not resolvable inside the KB's work tree, in
    which case R15 cannot know what a self-edge would even be."""
    if not rel_path:
        return None
    rparts = rel_path.split("/")
    if len(rparts) >= 3 and rparts[0] == "work":
        return rparts[-2]
    return None


def _lint_graph(text, rel_path, strict, schema_version, errors, warnings):
    """R15/R16 (feature 260722 plan-graph) — the AUTHORING-CONVENIENCE graph blocks.

    R15 refuses a malformed `## Depends-on:` row, a self-edge and a duplicate
    predecessor; R16 refuses an out-of-vocab `> Checkpoint:` and a self-extension
    plan that declares `none`. Severity rides the same WARN->ERROR versioned ramp
    as R12/R14: WARN today, ERROR under --strict once the schema advances. (The
    HARD refusal of these invariants is the manifest validator, not lint.)
    """
    graph_ramp = strict and schema_version >= _PLAN_GRAPH_ERROR_SCHEMA
    bucket = errors if graph_ramp else warnings
    own_slug = _plan_own_slug(rel_path)

    edges = plan_depends_on(text)
    seen_preds = set()
    for slug, kind in edges:
        if kind == _PLAN_EDGE_MALFORMED:
            _lint_add(bucket, "R15", 1,
                      "malformed `## Depends-on:` row %r — each row is "
                      "`- <slug> [logical|resource]` with a slug matching "
                      "[A-Za-z0-9_-]+ and kind in {logical, resource}" % slug)
            continue
        if own_slug and slug == own_slug:
            _lint_add(bucket, "R15", 1,
                      "`## Depends-on:` declares a self-edge on %r — a node may "
                      "not depend on itself" % slug)
        if slug in seen_preds:
            _lint_add(bucket, "R15", 1,
                      "`## Depends-on:` declares a duplicate edge on "
                      "predecessor %r" % slug)
        seen_preds.add(slug)

    policy = plan_checkpoint(text)
    if policy not in _CHECKPOINT_POLICIES:
        _lint_add(bucket, "R16", 1,
                  "`> Checkpoint: %s` is not a recognized policy — one of: %s"
                  % (policy, ", ".join(_CHECKPOINT_POLICIES)))
    elif _plan_is_self_extension(text) and policy == "none":
        _lint_add(bucket, "R16", 1,
                  "self-extension plan must declare a non-none `> Checkpoint:` "
                  "(review|manual) — a self-extension node is a mandatory manual "
                  "review barrier (R-PKG-03)")


_EXPORTS = [
    "_WORKORDER_SLUG_RE", "_PLAN_GRAPH_ERROR_SCHEMA", "_lint_workorder",
    "_lint_status", "_lint_premises", "_plan_own_slug", "_lint_graph",
]
__all__ = list(_EXPORTS)
