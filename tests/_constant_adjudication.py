#!/usr/bin/env python3
"""ADJUDICATIONS — the hand-audited half of the constant differ (task 31).

`_constant_differ.py` computes every divergence mechanically. Two things it
cannot compute live here:

1. **Which value was DECIDED**, for each constant that exists on both sides with
   a different value. There are 17, and each one was read in place — the
   assignment, its call sites and the plan task that owns it — before it was
   written down below. Nothing here forgives a row: every entry still leaves a
   check the gate runs on every invocation.
2. **Which plan task owns each monolith-only constant**, so a reader chasing a
   `pending` row is pointed at the port that will discharge it rather than left
   to grep. That half is a PREFIX table, because the monolith's constants are
   already namespaced by the verb family they serve (`CHECKUP_*`, `GRAPH_*`,
   `REVIEW_*`, …) and hand-listing 248 names would rot on the first rename.

Keep this table beside the mechanics, not inside them, exactly as
`_body_adjudication.py` does: a reviewer asking "which value won, and who said
so" should read one short file and see all of it, with the reasoning attached to
the row rather than buried in a commit message.

Decision kinds and what each obliges (see `_constant_differ` for the mechanics):

  lib         -> `decided_literal`; the lib's value IS the decision and must
                 stay byte-identical to it.
  legacy      -> `decided_literal` + `follow_up`; this tree's value is the
                 decision but has not landed. The monolith must keep holding it,
                 and the lib may hold ONLY the decision or the pinned
                 `pending_literal` — no third value can appear quietly.
  equivalent  -> no literal at all; the two spellings are refactors of one
                 another and are proven equal at RUNTIME by `--probe`, which is
                 the only thing that can discharge a derived value (see
                 `learn-a-derived-constant-is-invisible-to-a-static-allowlist-gate`).
"""

# --------------------------------------------------------------------------
# EQUIVALENT -- refactors of the same value, proven by the runtime probe
# --------------------------------------------------------------------------
#
# Every row here is a case where the lib reaches the SAME object by a different
# expression. A static literal compare can never discharge one of these, and
# hand-asserting "these are the same really" would be exactly the un-gated prose
# this differ exists to replace. So they carry no literal: `--probe` imports
# both sides in a subprocess and compares the live objects, and the row fails if
# they ever stop agreeing.

_EQUIV = "equivalent"

_REFACTOR_ROWS = {
    # The monolith computes the repo root from its own two-deep location; the
    # lib walks up for the marker instead, because a module 4 levels deep cannot
    # hardcode a `dirname` count. Same directory, and the probe proves it.
    "REPO_ROOT": "task 25 landed the lib package one level deeper than the "
                 "monolith, so `_find_repo_root` replaces a fixed dirname walk",
    # These three are the same path, now spelled through the module that owns
    # the root rather than through a module-local copy of it.
    "REPO_CONFIG": "same path via `paths.REPO_ROOT` rather than a local rebind",
    "SKILLS_CATALOG_PATH": "same path via `paths.REPO_ROOT`",
    "ATTACH_REQUIRED_DIRS": "same tuple via `index.KNOWLEDGE_SUBDIRS`",
    # `HOME_CONFIG` is the donor's AGENTWARE_CONFIG_HOME support (task 53 adopts
    # it deliberately). With the override unset -- the default, and the only
    # configuration the probe runs in -- `_CONFIG_HOME` IS `~/.agentware`, so
    # the two agree. The divergence is a capability, not a value change.
    "HOME_CONFIG": "`_CONFIG_HOME` defaults to ~/.agentware, so the resolved "
                   "path is unchanged; the indirection is task 53's config-home "
                   "override arriving early",
    # The monolith routes the string through `SUPERSEDES_RELATION` because
    # R-ORG-08 needs one place naming the relation edge; the lib inlines it.
    # Identical tuple today. The NAME is a separate legacy-only row, so the
    # indirection itself is still owed by the port that lands `relate`.
    "RELATION_TYPES": "identical tuple; the monolith spells the third element "
                      "through SUPERSEDES_RELATION, which is its own pending row",
}

# --------------------------------------------------------------------------
# LIB -- the lib's value is the decision
# --------------------------------------------------------------------------

_LIB_ROWS = {
    "BM25_CACHE_VERSION": {
        "rationale": "task 24 bumped the cache version to 3 -- a value NEITHER "
                     "tree had used (2 here, 1 in the donor, over the SAME "
                     "cache path), so a foreign-schema cache can never load as "
                     "valid. The monolith's 2 is the thing being replaced.",
    },
    "FANOUT_MERGE_UNION_PATHS": {
        "rationale": "the lib's tuple is a strict superset: it adds the "
                     "generated `_registration.py`, which every fanout lane "
                     "regenerates and must union-merge rather than conflict on. "
                     "Task 70 retires that file and must re-adjudicate this row.",
        "follow_up": "task 70",
    },
}

# --------------------------------------------------------------------------
# LEGACY -- this tree's value is the decision, and it has NOT landed yet
# --------------------------------------------------------------------------
#
# These are the plan's four documented silent-loss classes, made mechanical. The
# gate pins BOTH ends: the monolith may not lose the decided value while the
# port is pending, and the lib may not drift to some third value on the way.

_LEGACY_ROWS = {
    "SKILL_PIN_FLOOR": {
        "follow_up": "task 49",
        "rationale": "the stage-1 skill pin is OFF in the donor (`None`), which "
                     "disables R-RET-03 stage-1 precedence with NO failing test, "
                     "because the donor's tests were written against the "
                     "disabled state. 0.85 is the committed floor; task 49 ports "
                     "it with its three-argument signature and all three call "
                     "sites including the evaluation path.",
    },
    "LEARNED_RE": {
        "follow_up": "task 45",
        "rationale": "the donor regex drops the `(?:[-*+]\\s+)?` list-bullet "
                     "prefix, so a marker written as a markdown bullet stops "
                     "matching -- the R-SI-03 direction that fails OPEN, letting "
                     "an unpromoted learning through the completion promise.",
    },
    "DECISION_RE": {
        "follow_up": "task 45",
        "rationale": "same list-bullet prefix as LEARNED_RE, on the R-AUTO-05 "
                     "side: a bulleted `> DECISION:` would stop blocking.",
    },
    "PROMOTED_RE": {
        "follow_up": "task 45",
        "rationale": "the monolith also recognises the `→` arrow and the "
                     "`[kb: <id>]` form. Losing them does not fail open on the "
                     "marker -- it fails open on the PROMOTION, so an already "
                     "promoted marker reads as unpromoted and the gate cries "
                     "wolf until someone disables it.",
    },
    "DREAM_STEP_FUNCS": {
        "follow_up": "task 46",
        "rationale": "the donor registry holds 8 steps; this tree has 12. The "
                     "four missing ones (stale-propose, skill-propose, "
                     "transcript-mine, prefer-classify) are exactly the dropped "
                     "nightly steps task 46 restores as four new step files.",
    },
    "_DREAM_METRIC_STEP_FIELDS": {
        "follow_up": "task 46",
        "rationale": "the metric fields the four dropped steps report "
                     "(proposed, deferred, blocked, reason, extended, clean). "
                     "Restoring the steps without these leaves them running and "
                     "unmeasured, which is the same silence one level down.",
    },
    "KB_GITIGNORE_CONTENT": {
        "follow_up": "task 57",
        "rationale": "the donor leaves the nine derived knowledge artifacts "
                     "TRACKED, so two clones conflict on essentially every push. "
                     "This tree's content de-commits them; task 57 lands it.",
    },
    "KB_GITIGNORE_REQUIRED": {
        "follow_up": "task 57",
        "rationale": "the enforcement half of the same decision -- the donor's "
                     "required set omits KB_DERIVED_GITIGNORE_RELS, so a fresh "
                     "init would PASS its own ignore check while tracking them.",
    },
    "_TERMINAL_OUTCOMES": {
        "follow_up": "task 34",
        "rationale": "this tree emits `premise_falsified` as a distinct terminal "
                     "so the premise-grounding pre-hook's precision is separately "
                     "measurable. The donor tuple lacks it, and its org lint "
                     "check O12 REJECTS unknown outcomes -- which is precisely "
                     "the hard-block task 34 exists to prevent.",
    },
}

DIVERGENT = {}
for _name, _why in _REFACTOR_ROWS.items():
    DIVERGENT[_name] = {"decision": _EQUIV, "rationale": _why}
for _name, _row in _LIB_ROWS.items():
    DIVERGENT[_name] = dict(_row, decision="lib")
for _name, _row in _LEGACY_ROWS.items():
    DIVERGENT[_name] = dict(_row, decision="legacy")

# --------------------------------------------------------------------------
# LEGACY-ONLY overrides
# --------------------------------------------------------------------------
#
# Every monolith-only constant defaults to `pending`: still in the monolith,
# owed to a named port. An entry here overrides that -- today only to record an
# `excluded` row (a constant a plan decision deliberately drops), which must
# then carry `reason` + `follow_up` and satisfies an ABSENCE probe, so an
# excluded constant reappearing under the lib is a failure too.
#
# The table held no rows until task 63. Every constant task 15 and task 16 would
# have excluded (the KB-repointing module, the repo-hard-reset dream step) had no
# module-level constant of its own, so there was nothing to exclude. Leaving
# the mechanism wired and proven by the suite -- rather than adding it later
# under time pressure -- is the point.

LEGACY_ONLY = {
    # `_KB_MIGRATIONS` is the ONE ported constant that is designed to GROW: it is
    # the registered-migration table, and every schema change this package will
    # ever ship appends a row to it. The pinned monolith literal therefore stops
    # being "the value the lib owes" the moment a migration lands, and holding
    # the gate to it would mean the KB schema could never advance without the
    # differ reading the advance as a porting defect.
    #
    # So the literal is re-pinned here, by hand, ONE APPEND AT A TIME, and the
    # obligation it carries is unchanged in the direction that matters: the
    # monolith's own row (migration 001, `plan-state-backfill`) is still spelled
    # out verbatim below, so DROPPING or re-spelling it still reddens the gate.
    # What the re-pin permits is exactly the append task 63 makes, and the next
    # migration must come back here and say so too.
    "_KB_MIGRATIONS": {
        "literal": "((1, 'additive', 'plan-state-backfill', "
                   "_migration_001_state_backfill), (2, 'additive', "
                   "'team-mode-scaffold', _migration_002_team_mode_scaffold))",
        "reason": "the migration registry is append-only by design; task 63 "
                  "appends migration 002 (the additive team-mode scaffold). "
                  "Migration 001's row is preserved verbatim, so a lost or "
                  "re-spelt monolith migration still fails the differ.",
        "follow_up": "task 63",
    },
}

# --------------------------------------------------------------------------
# FOLLOW-UP -- which port discharges each pending constant
# --------------------------------------------------------------------------
#
# Longest-prefix-wins over an ordered table. This is DOCUMENTATION, not a check:
# the gate's obligations do not depend on it, so a wrong row here misleads a
# reader but cannot let a constant escape. Ordered most-specific first.

_FOLLOW_UP_PREFIXES = (
    (("_KB_MIGRATION", "KB_SCHEMA_VERSION", "SCHEMA_REL"), "task 37"),
    (("SUPERSEDES_RELATION",), "task 38"),
    (("_PLAN_", "_PREMISE_", "_CHECKPOINT_", "_CHECK_METACHAR", "_CTX_",
      "_CORPUS_", "_WRITE_SET_", "_WORKORDER_", "WORKORDER_", "AUTO_DRAFT_",
      "OPERATOR_DRAFT_", "STALLED_CLAIM_", "SUGGESTED_ID_RE", "SLUG_RE"),
     "task 39"),
    (("ALLOWED_SIGNERS_NAME", "_SSH_KEYTYPES"), "task 40"),
    (("UPDATER_", "UPDATE_STATE_", "PKG_", "_PKG_"), "task 41"),
    (("TICK_", "_TICK_"), "task 43"),
    (("CHECKUP_",), "task 44"),
    (("MINER_", "PREFER_", "_PREFER_", "_PREF_", "_TRANSCRIPT_", "WAIVED_RE",
      "_PROMPTS_HEADER_RE"), "task 45"),
    (("DREAM_", "_DREAM_"), "task 46"),
    (("REVIEW_", "_REVIEW_"), "task 47"),
    (("GRAPH_", "_GRAPH_"), "task 48"),
    (("STALENESS_", "STALE_MAX_AGE_DAYS", "_RET_", "_WORK_STATUS_MULT",
      "WORK_CORPUS_CACHE_"), "task 49"),
    (("SKILL_PROMOTE_", "_SKILL_", "SKILL_LOCK_SCHEMA", "_SAFE_SKILL_NAME_RE",
      "MIN_FROZEN_CYCLES"), "task 54"),
    (("KB_DERIVED_GITIGNORE_RELS",), "task 57"),
    (("LEARN_NEAR_DUP_THRESHOLD", "MAX_QUARANTINED_TAGS"), "task 61"),
    (("_KERNEL_", "_PATH_MODES"), "task 69"),
    (("GIT_GUARD_MARKER",), "task 35"),
)

# Everything not claimed above lands with its verb in the shared-verb promotion
# wave, which is the task that deletes the last legacy handler.
FOLLOW_UP_DEFAULT = "task 54"


def follow_up_for(name):
    """The plan task that discharges this monolith-only constant."""
    best, best_len = FOLLOW_UP_DEFAULT, -1
    for prefixes, task in _FOLLOW_UP_PREFIXES:
        for prefix in prefixes:
            if name.startswith(prefix) and len(prefix) > best_len:
                best, best_len = task, len(prefix)
    return best
